#!/usr/bin/env python3
"""Checked share counts, SEC filings as the source of truth (issue #16).

    pytest test_sec_share_counts.py

No network, no Firestore.

WHY THIS FILE EXISTS — 29 October 2026.

Market cap is the price times the latest quarter's share count, and every
valuation ratio follows from it. 622 of 6,734 companies were more than 1.5x
from Yahoo's market cap. The owner ruled: "The source of truth in my view
should be the SEC filing", and companies with several classes follow the SEC
count (IBKR 450,088,032, as Bloomberg shows). These tests use the real numbers
measured that night.
"""
from datetime import date

import pandas as pd

from testkit import check

import fetchers
import share_counts as sc


# ── a small stand-in for Firestore ───────────────────────────────────────────

class _Doc:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def get(self):
        data = self.store.get(self.path)
        return type("Snap", (), {"to_dict": lambda s: dict(data) if data else None, "exists": data is not None})()

    def set(self, data, merge=False):
        if merge and self.path in self.store:
            merged = dict(self.store[self.path])
            for key, value in data.items():
                if isinstance(value, dict) and isinstance(merged.get(key), dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
            self.store[self.path] = merged
        else:
            self.store[self.path] = dict(data)


class _Col:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def document(self, doc_id):
        return _Doc(self.store, f"{self.path}/{doc_id}")


class FakeDB:
    def __init__(self, store=None):
        self.store = store or {}

    def collection(self, name):
        return _Col(self.store, name)


def quarter(period, end, diluted):
    return {"period": period, "period_end": end, "diluted": diluted}


def one(q, sec=None, splits=None, foreign=False, yahoo=None):
    return sc.correct_quarters([q], sec or {}, {} if splits is None else splits, foreign, yahoo)[q["period"]] \
        if (sec or foreign) else None


# ── which number wins ────────────────────────────────────────────────────────

def test_a_bad_statement_number_gives_way_to_sec():
    """TTD: statement 4.7M, SEC 469.9M, Yahoo 462M."""
    entry = one(quarter("2026-Q2", "2026-06-30", 4_699_480), {"2026-06-30": 469_948_000}, yahoo=461_900_000)
    check("SEC count", entry["shares"] == 469_948_000, str(entry))
    check("status", entry["status"] == "sec", str(entry))


def test_firstenergy_follows_its_filing():
    entry = one(quarter("2026-Q1", "2026-03-31", 2_316_000_000), {"2026-03-31": 580_000_000}, yahoo=578_600_000)
    check("SEC count", entry["shares"] == 580_000_000, str(entry))


def test_firstenergy_before_sec_publishes_its_latest_quarter():
    """FE's June 2026 quarter is not in SEC's file yet. Its March quarter (580M)
    and Yahoo (579M) agree; the statement's 2,316M is replaced."""
    out = sc.correct_quarters([quarter("2026-Q1", "2026-03-31", 578_000_000),
                               quarter("2026-Q2", "2026-06-30", 2_316_000_000)],
                              {"2026-03-31": 580_000_000}, {}, False, 578_600_000)
    check("latest", out["2026-Q2"]["shares"] == 580_000_000 and out["2026-Q2"]["status"] == "sec_earlier_quarter",
          str(out))


def test_the_earlier_quarter_is_not_used_without_yahoos_agreement():
    out = sc.correct_quarters([quarter("2026-Q1", "2026-03-31", 100_000_000),
                               quarter("2026-Q2", "2026-06-30", 300_000_000)],
                              {"2026-03-31": 100_000_000}, {}, False, 290_000_000)
    check("kept", "2026-Q2" not in out, str(out))


def test_several_share_classes_follow_the_sec_count_not_yahoos_units():
    """IBKR: the owner's ruling. 450,088,032 in the 10-Q; Yahoo counts 1.74B units."""
    entry = one(quarter("2026-Q2", "2026-06-30", 452_000_000), {"2026-06-30": 450_088_032}, yahoo=1_740_700_000)
    check("SEC count", entry["shares"] == 450_088_032, str(entry))


def test_an_sec_unit_slip_is_corrected_not_believed():
    """McDonald's: the SEC file says 711.1 shares, meaning 711.1 million."""
    entry = one(quarter("2026-Q2", "2026-06-30", 711_100_000), {"2026-06-30": 711.1}, yahoo=704_700_000)
    check("corrected", abs(entry["shares"] - 711_100_000) < 1, str(entry))
    check("flagged", entry["sec_units_corrected"] is True, str(entry))


def test_a_count_filed_before_a_split_is_put_on_todays_basis():
    """A 10-Q filed before a 2-for-1 split, read after it."""
    entry = one(quarter("2026-Q2", "2026-06-30", 2_578_400_000), {"2026-06-30": 1_289_200_000},
                splits={"2026-08-14": 2.0}, yahoo=2_373_000_000)
    check("doubled", entry["shares"] == 2_578_400_000 and entry["split_factor"] == 2.0, str(entry))


def test_a_split_before_the_quarter_ended_is_not_applied_again():
    entry = one(quarter("2026-Q2", "2026-06-30", 2_578_400_000), {"2026-06-30": 2_578_400_000},
                splits={"2024-06-12": 2.0}, yahoo=2_373_000_000)
    check("unchanged", entry["shares"] == 2_578_400_000 and entry["split_factor"] == 1.0, str(entry))


def test_a_count_sec_restated_after_the_split_is_not_split_again():
    """IBKR, 4-for-1 on 17 June 2025. SEC's March 2025 figure comes from a 10-Q
    filed after the split, already on the new basis: 439M, not 110M. Applying
    the ratio again gave 1,758M beside 450M either side (29 Oct 2026)."""
    out = sc.correct_quarters([quarter("2025-Q1", "2025-03-31", 443_750_000),
                               quarter("2026-Q2", "2026-06-30", 452_173_913)],
                              {"2025-03-31": 439_462_964, "2026-06-30": 450_088_032},
                              {"2025-06-18": 4.0}, False, 1_740_700_000)
    check("not split twice", out["2025-Q1"]["shares"] == 439_462_964 and out["2025-Q1"]["split_factor"] == 1.0,
          str(out))
    check("latest", out["2026-Q2"]["shares"] == 450_088_032, str(out))


def test_an_older_count_filed_before_the_split_is_still_split():
    """The same company, but SEC's March figure was filed before the split (110M)."""
    out = sc.correct_quarters([quarter("2025-Q1", "2025-03-31", 110_000_000),
                               quarter("2026-Q2", "2026-06-30", 452_173_913)],
                              {"2025-03-31": 109_865_741, "2026-06-30": 450_088_032},
                              {"2025-06-18": 4.0}, False, 1_740_700_000)
    check("x4", out["2025-Q1"]["shares"] == 439_462_964 and out["2025-Q1"]["split_factor"] == 4.0, str(out))


def test_a_reverse_split_divides():
    entry = one(quarter("2026-Q1", "2026-03-31", 500_000), {"2026-03-31": 5_000_000},
                splits={"2026-05-01": 0.1}, yahoo=520_000)
    check("one tenth", abs(entry["shares"] - 500_000) < 1, str(entry))


def test_without_a_split_record_nothing_changes_yet():
    """The first weekly run saves the splits; until a ticker has its record, no SEC count is used."""
    entry = sc.correct_quarters([quarter("2026-Q2", "2026-06-30", 2_578_400_000)],
                                {"2026-06-30": 1_289_200_000}, None, False, 2_373_000_000)["2026-Q2"]
    check("waiting", entry["status"] == "waiting_for_split_record" and "shares" not in entry, str(entry))


def test_sec_is_outvoted_when_the_statement_and_yahoo_agree():
    """Berkshire B: an SEC figure in Class A equivalents would be 1,500x too small."""
    entry = one(quarter("2026-Q2", "2026-06-30", 2_154_663_423), {"2026-06-30": 1_436_443}, yahoo=2_115_851_312)
    check("outvoted", entry["status"] == "sec_outvoted" and entry["shares"] is None, str(entry))


def test_a_foreign_filer_in_local_shares_is_put_in_new_york_shares():
    """HDB: statement 2,566M local shares, the price is per ADS (2 shares each)."""
    entry = one(quarter("2025-Q2", "2025-06-30", 2_565_958_018), foreign=True, yahoo=5_083_565_141)
    check("x2", abs(entry["shares"] - 5_131_916_036) < 1 and entry["status"] == "adr_basis", str(entry))


def test_a_foreign_filer_without_a_yahoo_figure_keeps_todays_count():
    """KOF: no Yahoo market cap, no SEC quarter. The owner: no review list, so nothing changes."""
    out = sc.correct_quarters([quarter("2026-Q2", "2026-06-30", 1_680_670_000)], {}, {}, True, None)
    check("nothing", out == {}, str(out))


def test_a_us_company_with_no_sec_quarter_keeps_todays_count():
    out = sc.correct_quarters([quarter("2026-Q2", "2026-06-30", 100_000_000)], {}, {}, False, 300_000_000)
    check("nothing", out == {}, str(out))


# ── what readers see ─────────────────────────────────────────────────────────

FINANCIALS = [
    {"period": "2026-Q2", "period_end": "2026-06-30", "freq": "Q",
     "income": {"Diluted Average Shares": 4_699_480.0, "Basic Average Shares": 4_683_590.0,
                "Net Income Common Stockholders": 64_394_000.0}},
    {"period": "2026-Q1", "period_end": "2026-03-31", "freq": "Q",
     "income": {"Diluted Average Shares": 476_900_000.0}},
    {"period": "2025-FY", "period_end": "2025-12-31", "freq": "FY",
     "income": {"Diluted Average Shares": 490_000_000.0}},
]
RECORD = {"quarters": {
    "2026-Q2": {"source": "sec", "status": "sec", "shares": 469_948_000.0},
    "2026-Q1": {"source": "sec", "status": "sec_outvoted", "shares": None},
    "2025-FY": {"source": "sec", "status": "sec", "shares": 1.0},
}}


def test_the_overlay_corrects_the_quarter_and_its_eps():
    import terminal
    out = sc.apply(FINANCIALS, RECORD)
    q2 = out[0]["income"]
    check("diluted", q2["Diluted Average Shares"] == 469_948_000.0, str(q2))
    check("basic follows a change of basis", abs(q2["Basic Average Shares"] - 4_683_590 * 469_948_000 / 4_699_480) < 1, str(q2))
    eps = terminal.computed_eps(q2)
    check("TTD EPS about $0.14, not $13.70", round(eps, 2) == 0.14, str(eps))
    check("source recorded", out[0]["share_count_source"] == "sec", str(out[0]))


def test_the_overlay_leaves_the_rest_alone():
    out = sc.apply(FINANCIALS, RECORD)
    check("outvoted quarter untouched", out[1] is FINANCIALS[1])
    check("annual untouched", out[2] is FINANCIALS[2])
    check("statements never changed", FINANCIALS[0]["income"]["Diluted Average Shares"] == 4_699_480.0)


def test_readers_see_nothing_new_until_it_is_switched_on(monkeypatch):
    """Watch-only by default: the website, the company pages and Kilby read the statements as before."""
    monkeypatch.delenv("SHARE_COUNTS_READ", raising=False)
    monkeypatch.setattr(sc, "record_for", lambda s, db=None: RECORD)
    check("off", sc.overlay("TTD", FINANCIALS) is FINANCIALS)
    monkeypatch.setenv("SHARE_COUNTS_READ", "on")
    check("on", sc.overlay("TTD", FINANCIALS)[0]["income"]["Diluted Average Shares"] == 469_948_000.0)


def test_market_cap_uses_the_corrected_latest_quarter(monkeypatch):
    import storage
    import terminal
    monkeypatch.setenv("SHARE_COUNTS_READ", "on")
    monkeypatch.setattr(sc, "record_for", lambda s, db=None: RECORD)
    monkeypatch.setattr(storage, "get_all_financials", lambda s: [dict(f) for f in FINANCIALS])
    monkeypatch.setattr(terminal, "_has_firestore", lambda: True)
    fundamentals = terminal._firestore_fundamentals.__wrapped__("TTD", {}) \
        if hasattr(terminal._firestore_fundamentals, "__wrapped__") else terminal._firestore_fundamentals("TTD", {})
    check("shares", fundamentals["shares"] == 469_948_000.0, str(fundamentals.get("shares")))


def test_kilbys_market_cap_check_still_holds_when_switched_on(monkeypatch):
    """Kilby (report_evidence.normalize_observation) refuses a market cap unless
    price x fundamentals.diluted_shares equals valuation.market_cap, the
    definition text is unchanged and no quality warning is added. The corrected
    count must therefore be the one in BOTH places."""
    import partner_api
    import storage
    import terminal
    monkeypatch.setenv("SHARE_COUNTS_READ", "on")
    monkeypatch.setattr(sc, "record_for", lambda s, db=None: RECORD)
    monkeypatch.setattr(storage, "get_all_financials", lambda s: [dict(f) for f in FINANCIALS])
    monkeypatch.setattr(terminal, "_has_firestore", lambda: True)
    monkeypatch.setattr(terminal, "_firestore_meta", lambda s: {"symbol": "TTD", "active": True, "currency": "USD"})
    monkeypatch.setattr(terminal, "_live_quote", lambda s: {"price": 12.75})
    snap = terminal._market_snapshot("TTD")
    check("shares", snap["shares"] == 469_948_000.0, str(snap.get("shares")))
    check("price x shares == cap", snap["market_cap"] == 12.75 * snap["shares"], str(snap.get("market_cap")))
    check("definition unchanged",
          "Live price x diluted average shares, computed at request time" in open(partner_api.__file__, encoding="utf-8").read())


# ── SEC requests: never enough to be throttled ───────────────────────────────

class _Response:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data or {}

    def json(self):
        return self._data


def _sec(answers=None):
    asked = []

    def get(url, headers=None, timeout=None):
        asked.append(url)
        check("named", headers and "TEK2day" in headers.get("User-Agent", ""), headers)
        status = (answers or {}).get(len(asked), 200)
        return _Response(status, {"data": [{"cik": 1671933, "end": "2026-06-30", "val": 469948000}]})
    return asked, get


def test_the_quarters_are_the_last_eight_finished():
    q = sc.calendar_quarters(date(2026, 10, 30))
    check("newest", q[0] == "CY2026Q3", str(q))
    check("oldest", q[-1] == "CY2024Q4" and len(q) == 8, str(q))
    check("january", sc.calendar_quarters(date(2027, 1, 5))[0] == "CY2026Q4")


def test_first_run_sixteen_requests_then_four_a_week():
    db = FakeDB()
    asked, get = _sec()
    pauses = []
    sc.fetch_frames(db, date(2026, 10, 30), get=get, sleep=pauses.append)
    check("first run", len(asked) == 16, str(len(asked)))
    check("one a second", pauses == [1.0] * 15, str(pauses))
    asked2, get2 = _sec()
    frames = sc.fetch_frames(db, date(2026, 11, 5), get=get2, sleep=lambda s: None)
    check("weekly", len(asked2) == 4, str(asked2))
    check("older quarters kept", len(frames) == 16, str(len(frames)))


def test_the_first_refusal_stops_every_sec_request():
    asked, get = _sec({3: 429})
    sc.fetch_frames(FakeDB(), date(2026, 10, 30), get=get, sleep=lambda s: None)
    check("stopped at the refusal", len(asked) == 3, str(len(asked)))


def test_a_quarter_nobody_has_filed_yet_is_not_a_refusal():
    asked, get = _sec({1: 404})
    sc.fetch_frames(FakeDB(), date(2026, 10, 30), get=get, sleep=lambda s: None)
    check("carried on", len(asked) == 16, str(len(asked)))


def test_never_more_than_the_cap(monkeypatch):
    monkeypatch.setattr(sc, "QUARTERS", 20)
    asked, get = _sec()
    sc.fetch_frames(FakeDB(), date(2026, 10, 30), get=get, sleep=lambda s: None)
    check("cap", len(asked) == sc.MAX_SEC_REQUESTS, str(len(asked)))


def test_the_diluted_count_is_preferred():
    frames = {(sc.TAGS[1], "CY2026Q2"): {"0000000001": ["2026-06-30", 90.0]},
              (sc.TAGS[0], "CY2026Q2"): {"0000000001": ["2026-06-30", 100.0]}}
    check("diluted", sc.sec_by_company(frames)["0000000001"]["2026-06-30"] == 100.0)


# ── splits, from the price download already made ─────────────────────────────

class _SplitTicker:
    history_metadata = {}

    def history(self, **kw):
        idx = pd.to_datetime(["2024-06-07", "2024-06-10", "2024-06-11"])
        return pd.DataFrame({"Open": [1.0] * 3, "High": [1.0] * 3, "Low": [1.0] * 3, "Close": [1.0] * 3,
                             "Volume": [1] * 3, "Stock Splits": [0.0, 10.0, 0.0]}, index=idx)


def test_the_price_download_hands_over_its_splits(monkeypatch):
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _SplitTicker())
    splits = {}
    rows = fetchers.fetch_prices("NVDA", period="5y", splits=splits)
    check("rows as before", len(rows) == 3 and "Stock Splits" not in rows[0], str(rows[0]))
    check("split", splits == {"2024-06-10": 10.0}, str(splits))
    check("callers that do not ask are unchanged", len(fetchers.fetch_prices("NVDA", period="5y")) == 3)


def test_a_long_download_records_even_no_splits():
    db = FakeDB()
    sc.save_splits(db, "AAPL", {}, "5y", today=date(2026, 11, 5))
    check("recorded", db.store["splits/AAPL"]["events"] == {} and db.store["splits/AAPL"]["period"] == "5y")


def test_an_ordinary_night_touches_nothing_unless_it_found_a_split():
    class NoDB:
        def collection(self, name):
            raise AssertionError("no write on an ordinary night")
    sc.save_splits(NoDB(), "AAPL", {}, "5d")
    db = FakeDB({"splits/NVDA": {"events": {"2024-06-10": 10.0}, "period": "5y"}})
    sc.save_splits(db, "NVDA", {"2026-11-03": 2.0}, "5d")
    check("added", db.store["splits/NVDA"]["events"] == {"2024-06-10": 10.0, "2026-11-03": 2.0}, str(db.store))


if __name__ == "__main__":
    from testkit import run_all
    run_all(globals())
