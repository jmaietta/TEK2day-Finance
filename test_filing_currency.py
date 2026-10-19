#!/usr/bin/env python3
"""Statements are labelled in the currency they are filed in.

    pytest test_filing_currency.py

No network, no Firestore.

WHY THIS FILE EXISTS — TSM, 18 October 2026.

TSM trades in US dollars in New York and files in Taiwan dollars. Its Q2 2026
revenue is stored as 1,270,381,000,000 — NT$ — and every surface labelled it
"$M", which reads as $1.27T, about 30x too big. Of 10,029 active companies, 699
file in a currency other than the US dollar (SEC filings, 2025q4-2026q3).

Nothing is converted. The figures are as filed; only the label changes, and an
unknown currency keeps today's "$" label rather than a guess.
"""
import json

from testkit import check

import envelope
import fetchers
import partner_api
import terminal

# TSM-shaped: NT$ revenue, NT$ EPS per ADR.
TSM_RECORDS = [
    {"period": "2026-Q2", "period_end": "2026-06-30", "freq": "Q",
     "income": {"Total Revenue": 1_270_381_000_000.0, "Diluted EPS": 136.231667},
     "balance_sheet": {"Total Assets": 8_000_000_000_000.0},
     "cash_flow": {"Operating Cash Flow": 700_000_000_000.0}},
]


class Req:
    headers: dict = {}


def install(financial_currency):
    import storage
    meta = {"symbol": "TSM", "name": "Taiwan Semiconductor", "active": True, "currency": "USD"}
    if financial_currency is not None:
        meta["financial_currency"] = financial_currency
    storage.get_ticker_meta = lambda s: meta
    terminal._firestore_meta = lambda s: meta
    terminal._all_financials = lambda s: TSM_RECORDS
    partner_api.require_kilby = lambda r: "test"


def api(statement="income"):
    result = partner_api.equity_financials(Req(), symbol="TSM", statement=statement,
                                           frequency="quarterly")
    return json.loads(result.body) if hasattr(result, "body") else result


# ── what counts as a currency ────────────────────────────────────────────────

def test_only_a_plain_three_letter_code_is_a_filing_currency():
    check("TWD kept", envelope.filing_currency("TWD") == "TWD")
    check("spaces trimmed", envelope.filing_currency(" EUR ") == "EUR")
    for bad in (None, "", "US", "USDX", "usd", "GBp", 978):
        check(f"{bad!r} is unknown", envelope.filing_currency(bad) is None)


# ── the weekly pull keeps it, from the response it already gets ─────────────

class _Ticker:
    def __init__(self, info):
        self.info = info


def test_the_weekly_pull_keeps_yahoos_financial_currency(monkeypatch):
    info = {"symbol": "TSM", "shortName": "Taiwan Semiconductor", "currency": "USD",
            "financialCurrency": "TWD", "sector": "Technology", "industry": "Semiconductors"}
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _Ticker(info))
    meta = fetchers.fetch_ticker_info("TSM")
    check("filing currency kept", meta["financial_currency"] == "TWD", meta)
    check("quote currency untouched", meta["currency"] == "USD", meta)


def test_a_response_without_it_cannot_erase_a_stored_one(monkeypatch):
    """The write merges, so a missing key leaves the stored value alone."""
    info = {"symbol": "TSM", "shortName": "Taiwan Semiconductor", "currency": "USD"}
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _Ticker(info))
    meta = fetchers.fetch_ticker_info("TSM")
    check("no key written", "financial_currency" not in meta, meta)


# ── the shared label ─────────────────────────────────────────────────────────

def test_us_dollars_and_unknown_read_exactly_as_before():
    cells = [("Total Revenue", 90e9)]
    check("unknown", terminal.statement_scale("income", cells)[0] == "$M")
    check("USD", terminal.statement_scale("income", cells, "USD")[0] == "$M")
    check("small USD", terminal.statement_scale("income", [("Total Revenue", 5e5)], "USD")[0] == "$K")
    check("EPS unchanged", terminal.statement_display("income", "Diluted EPS", 1.08, 1e6, 0, "USD") == "$1.08")


def test_a_foreign_filer_is_labelled_by_its_code_not_a_dollar_sign():
    cells = [("Total Revenue", 1.27e12)]
    check("TWD millions", terminal.statement_scale("income", cells, "TWD")[0] == "TWD M")
    check("TWD thousands", terminal.statement_scale("income", [("Total Revenue", 5e5)], "TWD")[0] == "TWD K")
    check("EPS in TWD", terminal.statement_display("income", "Diluted EPS", 136.231667, 1e6, 0, "TWD")
          == "TWD 136.23")
    check("a loss keeps its brackets", terminal._eps(-1.5, "DKK") == "(DKK 1.50)")
    check("figures are not converted",
          terminal.statement_display("income", "Total Revenue", 1_270_381_000_000.0, 1e6, 0, "TWD")
          == "1,270,381")


def test_revenue_estimates_follow_the_filing_currency():
    check("TWD", terminal._dollar(1_459_139_238_150.0, "TWD") == "TWD 1.46T")
    check("USD unchanged", terminal._dollar(90e9) == "$90.00B")


# ── the feed Kilby reads ─────────────────────────────────────────────────────

def test_kilby_gets_the_filing_currency_in_the_label_and_the_units_block():
    install("TWD")
    body = api()
    check("unit label", body["data"]["unit"] == "TWD M", body["data"]["unit"])
    check("units block", body["units"]["currency"] == "TWD", body["units"])
    eps = next(r for r in body["data"]["rows"] if r["field"] == "Diluted EPS")
    check("EPS display", eps["display"][0] == "TWD 136.23", eps["display"])
    rev = next(r for r in body["data"]["rows"] if r["field"] == "Total Revenue")
    check("raw value as filed", rev["values"][0] == 1_270_381_000_000.0)


def test_an_unknown_currency_answers_as_every_response_did_before():
    install(None)
    body = api()
    check("unit label", body["data"]["unit"] == "$M", body["data"]["unit"])
    check("units block", body["units"]["currency"] == "USD", body["units"])


# ── the website ──────────────────────────────────────────────────────────────

def test_the_website_card_states_the_filing_currency():
    import app
    install("TWD")
    payload = app._financial_payload.__wrapped__ if hasattr(app._financial_payload, "__wrapped__") else app._financial_payload
    out = payload("TSM", "income", terminal.INCOME_FIELDS, "Income Statement")
    check("unit", out["unit"] == "TWD M", out["unit"])
    eps = next(r for s in out["sections"] for r in s["rows"] if r["label"] == "Reported EPS (Diluted)")
    check("EPS", eps["values"][0] == "TWD 136.23", eps["values"])


# ── estimates: in the currency Yahoo states for them ─────────────────────────
#
# Not always the filing currency. Measured 19 Oct 2026 across the 699 foreign
# filers: 22 of 315 revenue labels are US dollars (KOF files in MXN), and EPS
# estimates are US dollars for 137 and local currency for 180.

def estimate_record(eps_currency=None, rev_currency=None):
    record = {"date": "2026-10-17",
              "eps_avg": {"0q": 4.62067, "+1q": 4.308, "0y": 22.38512, "+1y": 22.88814},
              "eps_yearagoeps": {"0q": 4.5, "+1q": 4.1, "0y": 20.1, "+1y": 22.38512},
              "rev_avg": {"0q": 8.1e10, "+1q": 8.3e10, "0y": 3.2e11, "+1y": 3.4e11}}
    if eps_currency:
        record["eps_currency"] = {p: eps_currency for p in ("0q", "+1q", "0y", "+1y")}
    if rev_currency:
        record["rev_currency"] = {p: rev_currency for p in ("0q", "+1q", "0y", "+1y")}
    return record


def estimates_payload(monkeypatch, filing, record):
    import app
    install(filing)
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [record])
    out = app._estimates_payload("TSM")
    eps = next(s for s in out["sections"] if s["title"] == "EPS Estimates")
    rev = next(s for s in out["sections"] if s["title"] == "Revenue Estimates")
    consensus = next(r for r in eps["rows"] if r["label"] == "Consensus")
    return eps, rev, consensus


def test_revenue_estimates_yahoo_gives_in_dollars_are_not_labelled_local(monkeypatch):
    """KOF: files in MXN, Yahoo's revenue estimates in USD. PR #12 called them MXN."""
    _, rev, _ = estimates_payload(monkeypatch, "MXN", estimate_record("USD", "USD"))
    check("revenue in $", rev["unit"] == "$M", rev["unit"])


def test_eps_estimates_in_local_currency_say_so(monkeypatch):
    """Novo: EPS estimates in DKK. They used to read "$22.89"."""
    eps, rev, consensus = estimates_payload(monkeypatch, "DKK", estimate_record("DKK", "DKK"))
    check("EPS in DKK", consensus["values"][3] == "DKK 22.89", consensus["values"])
    check("revenue in DKK", rev["unit"] == "DKK M", rev["unit"])


def test_each_section_keeps_its_own_currency(monkeypatch):
    """TSM: EPS estimates in US dollars, revenue estimates in TWD."""
    _, rev, consensus = estimates_payload(monkeypatch, "TWD", estimate_record("USD", "TWD"))
    check("EPS in $", consensus["values"][3] == "$22.89", consensus["values"])
    check("revenue in TWD", rev["unit"] == "TWD M", rev["unit"])


def test_without_yahoos_label_estimates_read_as_before(monkeypatch):
    _, rev, consensus = estimates_payload(monkeypatch, "TWD", estimate_record())
    check("EPS keeps $", consensus["values"][3] == "$22.89", consensus["values"])
    check("revenue falls back to the filing currency", rev["unit"] == "TWD M", rev["unit"])


def test_disagreeing_labels_are_not_trusted():
    labels = {"0q": "DKK", "+1q": "USD"}
    check("EPS falls back to $", terminal.estimate_currency("TSM", labels, "eps") is None)
    install("TWD")
    check("revenue falls back to the filing currency",
          terminal.estimate_currency("TSM", labels, "rev") == "TWD")


def test_us_estimates_are_unchanged(monkeypatch):
    _, rev, consensus = estimates_payload(monkeypatch, None, estimate_record("USD", "USD"))
    check("EPS", consensus["values"][3] == "$22.89", consensus["values"])
    check("revenue", rev["unit"] == "$M", rev["unit"])


def test_the_estimates_download_states_both_currencies(monkeypatch):
    import auth
    import excel_export
    import exports
    install("DKK")
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [estimate_record("DKK", "USD")])
    monkeypatch.setattr(auth, "require_uid", lambda r: "test")
    titles = []
    monkeypatch.setattr(excel_export, "build_workbook", lambda title, *a, **k: titles.append(title) or b"")
    monkeypatch.setattr(exports, "_xlsx_response", lambda data, name: None)
    exports.export_estimates(Req(), symbol="NVO", fmt="xlsx")
    check("header", titles and "$ in millions (EPS in DKK)" in titles[0], titles)


def test_the_feed_kilby_reads_is_not_changed_by_this(monkeypatch):
    """Owner, 19 Oct 2026: Kilby is not touched by this fix. The feed's display
    strings for estimates stay exactly as they were; the raw values and Yahoo's
    currency labels already reach Kilby unchanged."""
    import partner_api
    record = estimate_record("DKK", "DKK")
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [record])
    raw = partner_api._estimates("NVO")
    check("raw label passes through", raw["eps"]["currency"]["+1y"] == "DKK", raw["eps"].get("currency"))
    shown = partner_api._display({"estimates": raw})
    check("display as before", shown["estimates"]["eps"]["avg"]["+1y"] == "$22.89", shown["estimates"])


def test_the_public_company_page_states_the_filing_currency():
    import seo_pages
    table = seo_pages._income_table(TSM_RECORDS, "TWD")
    eps = next(r for r in table["rows"] if r["label"] == "Diluted EPS")
    check("EPS", eps["values"][0] == "TWD 136.23", eps["values"])
    us = seo_pages._income_table(TSM_RECORDS, None)
    eps = next(r for r in us["rows"] if r["label"] == "Diluted EPS")
    check("unknown unchanged", eps["values"][0] == terminal._price(136.231667), eps["values"])
