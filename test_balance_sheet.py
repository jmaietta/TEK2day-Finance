#!/usr/bin/env python3
"""Tests for where the balance sheet comes from, and what happens without one.

    python test_balance_sheet.py

No network, no Firestore.

WHY THIS FILE EXISTS — Oracle, 16 August 2026.

ORCL's enterprise value came back exactly equal to its market cap. The first two
explanations were both wrong, and each was only ruled out by looking:

  "Yahoo has not sent the May quarter."   Yahoo had it, fully populated.
  "TEK2day has not ingested it."          TEK2day had it too — in 2026-FY.

Oracle's fiscal year ends 31 May, so `2026-Q2` and `2026-FY` BOTH end
2026-05-31. The quarterly record's balance sheet is empty; the annual one holds
all seventy fields. The code read the quarterly one, found nothing, and

    debt = fundamentals.get("debt") or 0

turned "we have no balance sheet" into "this company has no debt". EV feeds
EV/Rev, EV/EBITDA, EV/OpCF and EV/FCF, so a record that was merely in the wrong
place published FIVE wrong figures at once, on the website, the terminal and the
partner API, every one of them plausible.

Three rules are pinned here:

1. The balance sheet is chosen by DATE, across quarterly and annual alike. It is
   a point-in-time statement, so two records closing the same day describe the
   same sheet. This is safe ONLY for the balance sheet — MSFT's 2026-FY and
   2026-Q2 also share a date and their revenues differ by 3.7x.
2. It FALLS BACK, like the income statement already did, to the most recent
   period that actually holds one.
3. Absent means absent: no balance sheet, no enterprise value, and none of the
   four multiples built on it. Not market cap. An empty cell is a true
   statement; market cap wearing EV's label is not.

And the trap that nearly undid all three: Firestore holds `nan`, not None, where
Yahoo sent a blank. A presence test counts that as a sheet.
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

# ORCL's real 2026-FY balance sheet, as stored, read 16 Aug 2026.
MAY_SHEET = {"Total Debt": 156_189_000_000.0, "Cash And Cash Equivalents": 31_289_000_000.0,
             "Total Assets": 261_759_000_000.0}


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


def test_a_newer_quarterly_sheet_beats_an_older_annual_one():
    """Selection is by DATE, not by frequency. A February quarterly sheet is
    more recent than last May's annual one and wins on that alone."""
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


def test_a_sheet_of_nans_is_not_a_sheet():
    """THE ONE THAT ALMOST GOT THROUGH. Firestore holds `nan`, not None,
    wherever Yahoo sent a blank — ORCL's 2024-Q4 is 66 balance-sheet fields of
    it. A presence test written as `value is not None` counts that as a real
    sheet, `_to_float` then turns every figure back into None, and enterprise
    value collapses to market cap through a record that looks complete."""
    nan = float("nan")
    quarterly = [period("2026-05-31", balance={"Total Debt": nan, "Cash And Cash Equivalents": nan}),
                 period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("nan-only sheet is skipped", (found or {}).get("period_end") == "2026-02-28",
          str(found))


def test_a_nan_sheet_does_not_produce_an_enterprise_value():
    """The end-to-end version of the above: the bug this reintroduced was EV
    equalling market cap exactly."""
    nan = float("nan")
    install([period("2026-05-31", balance={"Total Debt": nan, "Cash And Cash Equivalents": nan},
                    revenue=14_000_000_000.0)])
    snap = terminal._market_snapshot("ORCL")
    check("nan sheet withholds EV", snap["enterprise_value"] is None,
          str(snap["enterprise_value"]))
    check("nan sheet EV is not market cap", snap["enterprise_value"] != snap["market_cap"])


def test_a_mixed_sheet_with_one_real_value_counts():
    """Finiteness, not purity. One real figure among NaNs is still a sheet."""
    nan = float("nan")
    quarterly = [period("2026-05-31", balance={"Total Debt": nan, "Cash And Cash Equivalents": 5.0}),
                 period("2026-02-28", balance=SHEET)]
    found = terminal._balance_period(quarterly, [])
    check("one finite value is enough", (found or {}).get("period_end") == "2026-05-31",
          str(found))


# ── the fiscal-year-end collision ────────────────────────────────────────────

def test_the_annual_sheet_wins_when_it_shares_the_quarter_end_date():
    """ORACLE, EXACTLY AS STORED. Its fiscal year ends 31 May, so 2026-Q2 and
    2026-FY both end 2026-05-31 — and the QUARTERLY record's balance sheet is
    empty while the ANNUAL one holds all 70 fields. Preferring quarterly falls
    back to February while May sits in the next record along.

    Safe only because a balance sheet is point-in-time: the two records describe
    the same balance sheet. Doing this to an income statement would be the MSFT
    3.7x error."""
    quarterly = [period("2026-05-31"), period("2026-02-28", balance=SHEET)]
    annual = [period("2026-05-31", freq="FY", balance=MAY_SHEET)]
    found = terminal._balance_period(quarterly, annual)
    check("annual sheet at the same date wins",
          (found or {}).get("period_end") == "2026-05-31" and (found or {}).get("freq") == "FY",
          str(found))


def test_oracles_enterprise_value_uses_mays_figures_not_februarys():
    """The whole point. Before this, ORCL's EV stood on February's balance
    sheet at best, and on market cap at worst."""
    install([period("2026-05-31", revenue=19_184_000_000.0),
             period("2026-05-31", freq="FY", balance=MAY_SHEET),
             period("2026-02-28", balance=SHEET)])
    snap = terminal._market_snapshot("ORCL")
    expected = snap["market_cap"] + 156_189_000_000.0 - 31_289_000_000.0
    check("EV uses May's balance sheet", snap["enterprise_value"] == expected,
          f"{snap['enterprise_value']} != {expected}")
    check("balance sheet date is May", snap.get("balance_sheet_as_of") == "2026-05-31",
          str(snap.get("balance_sheet_as_of")))


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



# ── total debt: the figure enterprise value actually needs ───────────────────
#
# EV needs TOTAL debt, long-term plus short-term. The old chain reached for
# `Long Term Debt` ahead of `Long Term Debt And Capital Lease Obligation` — the
# NARROWEST of three candidates. On GOOGL 2025-Q1 that is $10.9bn against a real
# total nearer $22.6bn.
#
# ENBRIDGE IS WHY THE ANNUAL FALLBACK EXISTS. Its selected balance sheet states
# no debt at all; its 2025 annual reports $105.25bn. Enterprise value was being
# computed as market cap minus cash — $111.95bn instead of $217.20bn, understated
# by 48%, with every EV multiple wrong by the same factor.

def sheet(period_end, **balance):
    return {"period_end": period_end, "freq": "Q", "balance_sheet": balance}


def annual(period_end, **balance):
    return {"period_end": period_end, "freq": "FY", "balance_sheet": balance}


def test_total_debt_is_used_when_stated():
    b = sheet("2026-06-30", **{"Total Debt": 112.8e9, "Long Term Debt": 98.2e9})
    check("stated total wins", terminal._total_debt(b, []) == (112.8e9, "2026-06-30"))


def test_the_parts_are_added_when_the_total_is_missing():
    """Measured over 938 records: long-term + short-term rebuilds Total Debt to
    within 0.5% in 99.5% of them."""
    b = sheet("2026-03-31", **{
        "Long Term Debt And Capital Lease Obligation": 90.0e9,
        "Current Debt And Capital Lease Obligation": 2.5e9})
    value, where = terminal._total_debt(b, [])
    check("parts summed", value == 92.5e9, str(value))
    check("same period", where == "2026-03-31", str(where))


def test_the_wider_long_term_field_beats_the_narrow_one():
    """THE OLD ORDERING TOOK THE NARROWEST. GOOGL 2025-Q1: $10.9bn instead of
    $22.6bn."""
    b = sheet("2025-03-31", **{
        "Long Term Debt": 10.9e9,
        "Long Term Debt And Capital Lease Obligation": 22.6e9})
    value, _w = terminal._total_debt(b, [])
    check("wider field used", value == 22.6e9, str(value))


def test_short_term_alone_is_never_used_as_total_debt():
    """Measured over 1,138 records: short-term is a MEDIAN 24.3% of total debt
    and under a tenth of it in a third of cases. A quarter of the answer wearing
    the whole answer's label is worse than no answer."""
    b = sheet("2026-03-31", **{"Current Debt And Capital Lease Obligation": 2.5e9})
    value, _w = terminal._total_debt(b, [])
    check("short-term alone refused", value is None, str(value))


def test_the_last_annual_total_is_carried_forward():
    """ENBRIDGE. 81.2% of quarters missing Total Debt carry no debt line at all,
    so the choice is last-known-total versus pretending it is zero."""
    b = sheet("2026-06-30", **{"Total Assets": 100e9})
    a = [annual("2025-12-31", **{"Total Debt": 105.25e9}),
         annual("2024-12-31", **{"Total Debt": 99.0e9})]
    value, where = terminal._total_debt(b, a)
    check("carried forward", value == 105.25e9, str(value))
    check("dated to the annual it came from", where == "2025-12-31", str(where))


def test_the_carried_figure_is_the_most_recent_annual_that_states_one():
    """Both candidates must sit inside the one-year window — an annual that
    states nothing does not consume the allowance."""
    b = sheet("2026-06-30", **{"Total Assets": 100e9})
    a = [annual("2026-03-31"), annual("2025-12-31", **{"Total Debt": 99.0e9})]
    value, where = terminal._total_debt(b, a)
    check("skips the silent annual", (value, where) == (99.0e9, "2025-12-31"), str((value, where)))


def test_no_debt_anywhere_stays_unknown():
    check("nothing invented", terminal._total_debt(sheet("2026-06-30"), []) == (None, None))


def test_enterprise_value_uses_the_carried_debt():
    """End to end: the 48% understatement closes."""
    install([period("2026-06-30", balance={"Cash And Cash Equivalents": 5e9},
                    revenue=10e9),
             period("2025-12-31", freq="FY", balance={"Total Debt": 105.25e9})])
    snap = terminal._market_snapshot("ENB")
    expected = snap["market_cap"] + 105.25e9 - 5e9
    check("debt included in EV", snap["enterprise_value"] == expected,
          f"{snap['enterprise_value']} != {expected}")
    check("and the date is reported", snap.get("debt_as_of") == "2025-12-31",
          str(snap.get("debt_as_of")))



def test_a_carried_figure_older_than_a_year_is_refused():
    """HIS RULE, 18 Aug: one year, no further. Carried figures were running to
    5.0 years old, and the fact that the oldest happened to be zero is luck, not
    a reason. A five-year-old debt position is not evidence about a company
    today."""
    b = sheet("2026-06-30", **{"Total Assets": 100e9})
    a = [annual("2021-12-31", **{"Total Debt": 50e9})]
    check("too old is refused", terminal._total_debt(b, a) == (None, None),
          str(terminal._total_debt(b, a)))


def test_a_carried_figure_inside_a_year_is_used():
    """ENBRIDGE sits at 0.5 years."""
    b = sheet("2026-06-30", **{"Total Assets": 100e9})
    a = [annual("2025-12-31", **{"Total Debt": 105.25e9})]
    value, where = terminal._total_debt(b, a)
    check("inside the window", (value, where) == (105.25e9, "2025-12-31"), str((value, where)))


def test_the_window_is_measured_from_the_SHEET_not_the_clock():
    """Measured against the selected sheet's own date so the answer is
    reproducible and these tests do not rot as time passes."""
    b = sheet("2019-06-30", **{"Total Assets": 100e9})
    a = [annual("2018-12-31", **{"Total Debt": 7e9})]
    value, _w = terminal._total_debt(b, a)
    check("old dates still work together", value == 7e9, str(value))


def test_a_stale_annual_is_skipped_for_a_fresh_one():
    b = sheet("2026-06-30", **{"Total Assets": 100e9})
    a = [annual("2021-12-31", **{"Total Debt": 50e9}),
         annual("2025-12-31", **{"Total Debt": 105.25e9})]
    value, where = terminal._total_debt(b, a)
    check("finds the one in range", (value, where) == (105.25e9, "2025-12-31"), str((value, where)))


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
