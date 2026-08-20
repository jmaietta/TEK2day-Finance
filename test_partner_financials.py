#!/usr/bin/env python3
"""Tests for /partner/v1/equities/{symbol}/financials.

    python test_partner_financials.py

No network, no Firestore — the store is stubbed.

The one that matters most is the period collision. MSFT's 2026-FY and 2026-Q2
BOTH end 2026-06-30, and their revenue differs by 3.7x. Anything that picks a
period by date can hand back a full year for a quarterly question, confidently
and with no sign that it is wrong.
"""
import sys

from testkit import check, run_all

import partner_api
import terminal

class Req:
    headers: dict = {}


# ── the store, stubbed ───────────────────────────────────────────────────────
# MSFT-shaped: an annual and a quarterly period sharing one period_end.

RECORDS = [
    {"period": "2026-FY", "period_end": "2026-06-30", "freq": "FY",
     "income": {"Total Revenue": 331_839_000_000.0, "Net Income": 101_832_000_000.0,
                "Diluted EPS": 13.64},
     "balance_sheet": {"Total Assets": 620_000_000_000.0},
     "cash_flow": {"Operating Cash Flow": 145_000_000_000.0}},
    {"period": "2026-Q2", "period_end": "2026-06-30", "freq": None,
     "income": {"Total Revenue": 90_007_000_000.0, "Net Income": 27_233_000_000.0,
                "Diluted EPS": 3.65},
     "balance_sheet": {"Total Assets": 620_000_000_000.0},
     "cash_flow": {"Operating Cash Flow": 39_000_000_000.0}},
    {"period": "2026-Q1", "period_end": "2026-03-31", "freq": "Q",
     "income": {"Total Revenue": 85_000_000_000.0, "Net Income": 25_000_000_000.0,
                "Diluted EPS": 3.35},
     "balance_sheet": {"Total Assets": 600_000_000_000.0},
     "cash_flow": {"Operating Cash Flow": 37_000_000_000.0}},
]

META = {"symbol": "MSFT", "name": "Microsoft Corporation", "active": True}


def install(records=None, meta=None):
    import storage
    storage.get_ticker_meta = lambda s: (META if meta is None else meta)
    terminal._all_financials = lambda s: (RECORDS if records is None else records)
    partner_api.require_kilby = lambda r: "test"


def call(**kw):
    kw.setdefault("symbol", "MSFT")
    result = partner_api.equity_financials(Req(), **kw)
    if hasattr(result, "body"):
        import json
        return result.status_code, json.loads(result.body)
    return 200, result


# ── the period collision ─────────────────────────────────────────────────────

def test_quarterly_returns_the_quarter_not_the_year():
    """Both end 2026-06-30. Revenue differs by 3.7x."""
    install()
    _, body = call(statement="income", frequency="quarterly")
    newest = body["data"]["periods"][0]
    check("newest quarterly key", newest["storage_key"] == "2026-Q2", newest["storage_key"])
    rev = next(r for r in body["data"]["rows"] if r["field"] == "Total Revenue")
    check("quarterly revenue is the quarter", rev["values"][0] == 90_007_000_000.0,
          f"{rev['values'][0]:,}")


def test_annual_returns_the_year():
    install()
    _, body = call(statement="income", frequency="annual")
    check("newest annual key", body["data"]["periods"][0]["storage_key"] == "2026-FY")
    rev = next(r for r in body["data"]["rows"] if r["field"] == "Total Revenue")
    check("annual revenue is the year", rev["values"][0] == 331_839_000_000.0,
          f"{rev['values'][0]:,}")


def test_the_annual_period_never_appears_in_quarterly_results():
    install()
    _, body = call(statement="income", frequency="quarterly")
    keys = [p["storage_key"] for p in body["data"]["periods"]]
    check("no FY key in quarterly", not any(k.endswith("-FY") for k in keys), str(keys))


def test_frequency_comes_from_the_key_not_from_freq():
    """freq is None on ~9% of quarterly records. The key pattern cannot drift."""
    install()
    _, body = call(statement="income", frequency="quarterly")
    keys = [p["storage_key"] for p in body["data"]["periods"]]
    check("2026-Q2 included despite freq=None", "2026-Q2" in keys, str(keys))
    for p in body["data"]["periods"]:
        check(f"{p['storage_key']} declared quarterly", p["frequency"] == "quarterly")


def test_periods_are_newest_first():
    install()
    _, body = call(statement="income", frequency="quarterly")
    keys = [p["storage_key"] for p in body["data"]["periods"]]
    check("newest first", keys == sorted(keys, reverse=True), str(keys))


# ── the payload ──────────────────────────────────────────────────────────────

def test_values_are_raw_and_display_is_rendered():
    install()
    _, body = call(statement="income", frequency="quarterly")
    rev = next(r for r in body["data"]["rows"] if r["field"] == "Total Revenue")
    check("raw is a number", isinstance(rev["values"][0], float))
    check("display is a string", isinstance(rev["display"][0], str), str(rev["display"][0]))


def test_eps_renders_as_a_price_not_a_magnitude():
    install()
    _, body = call(statement="income", frequency="quarterly")
    eps = next((r for r in body["data"]["rows"] if r["field"] == "Diluted EPS"), None)
    check("EPS row present", eps is not None)
    if eps:
        check("EPS renders as $3.65", eps["display"][0] == "$3.65", str(eps["display"][0]))


def test_rows_nobody_reports_are_dropped():
    """A row of nothing but nulls is noise, not information."""
    install()
    _, body = call(statement="income", frequency="quarterly")
    for row in body["data"]["rows"]:
        check(f"{row['field']} has a value", any(v is not None for v in row["values"]))


def test_each_statement_is_served_separately():
    install()
    for statement in ("income", "balance_sheet", "cash_flow"):
        _, body = call(statement=statement, frequency="quarterly")
        check(f"{statement} returns rows", bool(body["data"]["rows"]), statement)
        check(f"{statement} labelled", body["data"]["statement"] == statement)


# ── the envelope ─────────────────────────────────────────────────────────────

def test_coverage_states_what_is_held():
    install()
    _, body = call(statement="income", frequency="quarterly")
    cov = body["completeness"]["coverage"]
    check("coverage counts the quarters", cov["periods_held"] == 2, str(cov))
    check("earliest", cov["earliest"] == "2026-Q1", str(cov))


def test_no_internal_diagnostics_reach_the_caller():
    """A Kilby user must never be told there is an error in the system."""
    install(records=[{**RECORDS[1], "data_warnings": [
        {"code": "Assets = Liabilities + Equity", "detail": "289,607m vs 282,962m (2.29% apart)"}]}])
    _, body = call(statement="income", frequency="quarterly")
    blob = str(body)
    check("no check name leaks", "Assets = Liabilities" not in blob)
    check("no diagnostic leaks", "2.29% apart" not in blob)
    check("quality reports ok", body["quality"]["status"] == "ok")


def test_symbol_travels_with_the_answer():
    install()
    _, body = call(statement="income", frequency="quarterly")
    check("resolved symbol", body["resolved"]["symbol"] == "MSFT")
    check("requested echoed", body["requested"]["statement"] == "income")


# ── refusals ─────────────────────────────────────────────────────────────────

def test_uncovered_symbol_is_404():
    install(meta=None)
    import storage
    storage.get_ticker_meta = lambda s: None
    status, body = call(statement="income", frequency="quarterly")
    check("404 for uncovered", status == 404, str(status))
    check("carries no data key", "data" not in body)


def test_no_periods_of_that_frequency_is_not_an_error():
    """A company with only annual records is not a failure — it is coverage."""
    install(records=[RECORDS[0]])
    status, body = call(statement="income", frequency="quarterly")
    check("still 200", status == 200, str(status))
    check("data is null", body.get("data") is None, str(body.get("data"))[:60])
    check("coverage says nothing held", body["completeness"]["coverage"]["periods_held"] == 0)


def main():
    return run_all(globals())


if __name__ == "__main__":
    sys.exit(main())
