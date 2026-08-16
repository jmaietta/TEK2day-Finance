#!/usr/bin/env python3
"""Tests for where the balance sheet comes from, and what happens without one.

    python test_balance_sheet.py

No network, no Firestore.

WHY THIS FILE EXISTS — Oracle, 16 August 2026.

Yahoo had opened ORCL's May quarter but not yet sent its figures. Revenue was
fine, because `_statement_value` already falls back through prior quarters and
then annuals. Cash and debt were not, because they were read from the LATEST
period regardless of whether that period held a balance sheet. So they came
back empty.

Empty cash and debt were then read as ZERO, which made enterprise value come out
exactly equal to market cap — and EV feeds EV/Rev, EV/EBITDA, EV/OpCF and
EV/FCF. One quarter Yahoo had not populated yet published FIVE wrong figures,
on the website, the terminal and the partner API at once, with nothing saying
anything was missing.

Two rules are pinned here:

1. A balance sheet FALLS BACK, like the income statement already did. It is a
   point-in-time statement and is always "as of last reported", so taking the
   most recent one we hold is what the figure means rather than an approximation
   of it.
2. No balance sheet at all means NO ENTERPRISE VALUE. Not market cap. An empty
   cell is a true statement; market cap wearing EV's label is not.
"""
import sys

import terminal

_passed = 0
_failed = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
    else:
        _failed.append(f"{name}{(' — ' + detail) if detail else ''}")


def period(period_end, freq="Q", balance=None, revenue=None):
    """One stored financial record, shaped as Firestore holds it."""
    record = {"period_end": period_end, "freq": freq}
    record["balance_sheet"] = {} if balance is None else balance
    record["income"] = {} if revenue is None else {"Total Revenue": revenue}
    return record


SHEET = {"Total Debt": 96_000_000_000.0, "Cash And Cash Equivalents": 11_000_000_000.0}


# ── _balance_period: which period the balance sheet comes from ───────────────

def test_a_populated_latest_quarter_is_used():
    """The fallback must not fire when there is nothing to fall back from."""
    quarterly = [period("2026-05-31", balance=SHEET), period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("latest populated quarter wins", (found or {}).get("period_end") == "2026-05-31",
          str(found))


def test_an_empty_latest_quarter_falls_back_to_the_previous_one():
    """THE ORACLE CASE. Yahoo opened the May quarter without figures."""
    quarterly = [period("2026-05-31"), period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("falls back one quarter", (found or {}).get("period_end") == "2026-02-28",
          str(found))


def test_it_falls_back_as_far_as_it_has_to():
    quarterly = [period("2026-05-31"), period("2026-02-28"),
                 period("2025-11-30", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("falls back two quarters", (found or {}).get("period_end") == "2025-11-30",
          str(found))


def test_quarterly_is_exhausted_before_annual():
    """An annual sheet is older than any quarterly one we hold. Only reach for
    it when no quarter has a balance sheet at all."""
    quarterly = [period("2026-05-31"), period("2026-02-28", balance=SHEET)]
    annual = [period("2025-05-31", freq="FY", balance=SHEET)]
    found = terminal._balance_period(quarterly, annual)
    check("quarterly preferred to annual", (found or {}).get("period_end") == "2026-02-28",
          str(found))


def test_annual_is_used_when_no_quarter_has_one():
    quarterly = [period("2026-05-31"), period("2026-02-28")]
    annual = [period("2025-05-31", freq="FY", balance=SHEET)]
    found = terminal._balance_period(quarterly, annual)
    check("annual used as last resort", (found or {}).get("period_end") == "2025-05-31",
          str(found))


def test_no_balance_sheet_anywhere_returns_none():
    quarterly = [period("2026-05-31"), period("2026-02-28")]
    check("nothing found is None", terminal._balance_period(quarterly, []) is None)


def test_a_sheet_of_nulls_is_not_a_sheet():
    """A record can carry the KEYS with no values. Counting that as a balance
    sheet would reintroduce the whole bug behind a populated-looking record."""
    quarterly = [period("2026-05-31", balance={"Total Debt": None, "Cash And Cash Equivalents": None}),
                 period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("null-only sheet is skipped", (found or {}).get("period_end") == "2026-02-28",
          str(found))


def test_a_partially_populated_sheet_counts():
    """One real value is a balance sheet. Falling past it would discard data we
    actually hold."""
    quarterly = [period("2026-05-31", balance={"Total Debt": 5.0}),
                 period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("partial sheet is used", (found or {}).get("period_end") == "2026-05-31", str(found))


def test_a_missing_balance_sheet_key_does_not_raise():
    quarterly = [{"period_end": "2026-05-31", "freq": "Q"},
                 period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("absent key is skipped", (found or {}).get("period_end") == "2026-02-28", str(found))


def test_empty_inputs_are_safe():
    check("no periods at all", terminal._balance_period([], []) is None)
    check("None inputs", terminal._balance_period(None, None) is None)


# ── enterprise value: the figure the fallback exists to protect ──────────────

def install(records, price=250.0, shares=2_800_000_000.0):
    terminal._firestore_meta = lambda s: {"name": "Oracle Corporation",
                                          "shares_outstanding": shares}
    terminal._live_quote = lambda s: {"price": price}
    terminal._all_financials = lambda s: records
    terminal._latest_forward_eps = lambda s: None


def test_enterprise_value_survives_an_unpopulated_quarter():
    """THE REGRESSION. Before the fix this returned market cap exactly."""
    install([period("2026-05-31", revenue=14_000_000_000.0),
             period("2026-02-28", balance=SHEET)])
    snap = terminal._market_snapshot("ORCL")
    market_cap = snap["market_cap"]
    expected = market_cap + 96_000_000_000.0 - 11_000_000_000.0
    check("EV uses the fallback sheet", snap["enterprise_value"] == expected,
          f"{snap['enterprise_value']} != {expected}")
    check("EV is not market cap", snap["enterprise_value"] != market_cap)
    check("balance sheet date is reported", snap.get("balance_sheet_as_of") == "2026-02-28",
          str(snap.get("balance_sheet_as_of")))


def test_no_balance_sheet_means_no_enterprise_value():
    """Not market cap. An empty cell is true; market cap labelled EV is not."""
    install([period("2026-05-31", revenue=14_000_000_000.0),
             period("2026-02-28", revenue=13_000_000_000.0)])
    snap = terminal._market_snapshot("ORCL")
    check("EV is withheld", snap["enterprise_value"] is None, str(snap["enterprise_value"]))
    check("EV is not market cap", snap["enterprise_value"] != snap["market_cap"])
    check("no date claimed", snap.get("balance_sheet_as_of") is None)


def test_withholding_ev_withholds_every_multiple_built_on_it():
    """EV feeds four ratios. A None EV that still produced ratios would leak the
    same wrong number four more times."""
    install([period("2026-05-31", revenue=14_000_000_000.0)])
    snap = terminal._market_snapshot("ORCL")
    for field in ("ev_revenue", "ev_ebitda", "ev_opcf", "ev_fcf"):
        check(f"{field} withheld with EV", snap[field] is None, str(snap[field]))


def test_market_cap_is_unaffected_by_a_missing_balance_sheet():
    """Market cap is price times shares and owes the balance sheet nothing. It
    must still be published — withholding it would be the opposite mistake."""
    install([period("2026-05-31", revenue=14_000_000_000.0)])
    snap = terminal._market_snapshot("ORCL")
    check("market cap still published", snap["market_cap"] == 250.0 * 2_800_000_000.0,
          str(snap["market_cap"]))


def test_a_debt_free_company_still_gets_an_enterprise_value():
    """An absent LINE inside a real balance sheet is zero — the statement is
    there and does not report debt. That is a different claim from an absent
    STATEMENT, and it must not cost the company its EV."""
    install([period("2026-05-31", balance={"Cash And Cash Equivalents": 11_000_000_000.0})])
    snap = terminal._market_snapshot("ORCL")
    expected = snap["market_cap"] - 11_000_000_000.0
    check("EV published without a debt line", snap["enterprise_value"] == expected,
          f"{snap['enterprise_value']} != {expected}")


def test_no_price_still_means_no_enterprise_value():
    """EV needs market cap as well as a balance sheet. The new condition must
    not have replaced the old one."""
    install([period("2026-05-31", balance=SHEET)], price=None)
    snap = terminal._market_snapshot("ORCL")
    check("EV withheld without a price", snap["enterprise_value"] is None,
          str(snap["enterprise_value"]))


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = _passed + len(_failed)
    if _failed:
        print(f"FAILED {len(_failed)}/{total}")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print(f"{_passed}/{total} tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
