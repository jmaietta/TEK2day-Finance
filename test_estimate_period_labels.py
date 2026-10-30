#!/usr/bin/env python3
"""Each estimates column says which period it covers.

    pytest test_estimate_period_labels.py

No network, no Firestore.

WHY THIS FILE EXISTS — owner, 29 October 2026.

The website's estimates card headed its columns "Curr Q", "Next Q", "Curr Yr"
and "Next Yr" and stopped there. For NVDA "Curr Yr" is the year ending January
2027; for MSFT it is the year ending June 2027; AAPL's "Curr Q" was still the
September quarter a month after it ended, because Apple had not reported. The
end dates were already stored with every weekly snapshot (`horizons`, from
Yahoo's earningsTrend endDate); the card just did not show them.

Kilby's feed already carries the dates as target_period_ends and is not
changed here.
"""
from testkit import check

import terminal

# NVDA's snapshot of 27 Oct 2026, as stored.
NVDA = {"date": "2026-10-27",
        "horizons": {"0q": "2026-10-31", "+1q": "2027-01-31", "0y": "2027-01-31", "+1y": "2028-01-31"},
        "eps_avg": {"0q": 2.47, "+1q": 2.75, "0y": 9.31, "+1y": 15.91},
        "rev_avg": {"0q": 1.08977e11, "+1q": 1.24483e11, "0y": 4.11653e11, "+1y": 6.92206e11}}


class Req:
    headers: dict = {}


def payload(monkeypatch, record):
    import app
    import storage
    meta = {"symbol": "NVDA", "active": True, "currency": "USD"}
    monkeypatch.setattr(storage, "get_ticker_meta", lambda s: meta)
    monkeypatch.setattr(terminal, "_firestore_meta", lambda s: meta)
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [record])
    return app._estimates_payload("NVDA")


def test_a_quarter_is_labelled_by_the_month_it_ends():
    check("quarter", terminal.period_end_label("0q", "2026-10-31") == "Oct 2026")
    check("next quarter", terminal.period_end_label("+1q", "2027-01-31") == "Jan 2027")


def test_a_year_is_labelled_as_a_fiscal_year():
    check("year", terminal.period_end_label("0y", "2027-01-31") == "FY Jan 2027")
    check("next year", terminal.period_end_label("+1y", "2028-06-30") == "FY Jun 2028")


def test_no_date_means_no_label_not_a_guess():
    check("missing", terminal.period_end_label("0q", None) == "")
    check("garbage", terminal.period_end_label("0y", "not a date") == "")


def test_the_website_card_labels_every_column(monkeypatch):
    out = payload(monkeypatch, NVDA)
    for section in out["sections"]:
        check(section["title"] + " headings unchanged",
              section["periods"] == ["Curr Q", "Next Q", "Curr Yr", "Next Yr"], section["periods"])
        check(section["title"] + " period ends",
              section["periodEnds"] == ["Oct 2026", "Jan 2027", "FY Jan 2027", "FY Jan 2028"],
              section["periodEnds"])


def test_an_old_snapshot_without_dates_reads_as_before(monkeypatch):
    record = {k: v for k, v in NVDA.items() if k != "horizons"}
    out = payload(monkeypatch, record)
    for section in out["sections"]:
        check(section["title"] + " blank labels", section["periodEnds"] == ["", "", "", ""], section["periodEnds"])


def test_a_partly_dated_snapshot_labels_only_what_it_knows(monkeypatch):
    record = dict(NVDA, horizons={"0q": "2026-10-31", "0y": "2027-01-31"})
    out = payload(monkeypatch, record)
    check("partial", out["sections"][0]["periodEnds"] == ["Oct 2026", "", "FY Jan 2027", ""],
          out["sections"][0]["periodEnds"])


def test_the_estimates_download_carries_the_same_labels(monkeypatch):
    import auth
    import excel_export
    import exports
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [NVDA])
    monkeypatch.setattr(terminal, "estimate_currency", lambda *a: None)
    monkeypatch.setattr(auth, "require_uid", lambda r: "test")
    sections = []
    monkeypatch.setattr(excel_export, "build_workbook", lambda title, source, s, **k: sections.extend(s) or b"")
    monkeypatch.setattr(exports, "_xlsx_response", lambda data, name: None)
    exports.export_estimates(Req(), symbol="NVDA", fmt="xlsx")
    check("columns", sections and sections[0]["columns"] == [
        "Curr Q (Oct 2026)", "Next Q (Jan 2027)", "Curr Yr (FY Jan 2027)", "Next Yr (FY Jan 2028)"],
        sections and sections[0]["columns"])


def test_the_feed_kilby_reads_is_not_changed_by_this(monkeypatch):
    """Kilby already gets the raw dates; its display strings stay as they were."""
    import partner_api
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [NVDA])
    raw = partner_api._estimates("NVDA")
    check("raw dates pass through", raw.get("target_period_ends") == NVDA["horizons"], raw.get("target_period_ends"))
    check("no new field", "periodEnds" not in raw, sorted(raw))


if __name__ == "__main__":
    from testkit import run_all
    run_all(globals())
