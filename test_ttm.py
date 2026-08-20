#!/usr/bin/env python3
"""Tests for the trailing-twelve-month window.

    python test_ttm.py

No network, no Firestore.

WHY THIS FILE EXISTS — Amazon, 16 August 2026.

The comp card showed AMZN revenue of $716.92B and net income of $77.67B under
headings reading "(TTM)". Those are Amazon's FISCAL 2025 figures — a period that
ended on 31 December, nearly eight months earlier.

Nothing was corrupt. `_sum_recent` took the four newest RECORDS, the newest was
a stub because Yahoo had opened Amazon's June quarter without filling it, the
sum gave up on the first iteration, and `_statement_value` fell through to the
latest annual figure — which was then published with no label saying it was
annual. The four good quarters sitting immediately behind that stub sum to
$742.78B.

WHY IT WENT UNNOTICED FOR SO LONG. Measured across four companies the same day:

    MSFT    TTM 331.84B   4 quarters 331.84B   annual 331.84B
    ORCL    TTM  67.36B   4 quarters  67.36B   annual  67.36B
    GOOGL   TTM 445.87B   4 quarters 445.87B   annual 402.84B
    AMZN    TTM 775.68B   4 quarters 742.78B   annual 716.92B

MSFT and ORCL agree three ways because their fiscal years END on their most
recent quarter, so trailing-twelve-months and fiscal-year are the same twelve
months. Checking either one proves nothing at all.

Three rules are pinned here:

1. A stub in the newest slot MOVES THE WINDOW BACK, it does not abandon it.
   Still a true trailing twelve months, ending a quarter earlier.
2. The four quarters must be CONSECUTIVE IN TIME. Adjacency in the stored list
   is not adjacency in the calendar — a quarter missing from Firestore entirely
   closes the list up, and four apparently neighbouring records can span fifteen
   months.
3. The figure is whichever genuine twelve-month window ends LATER — four
   consecutive quarters or the fiscal year — and it carries the date it ends
   on. A fiscal year IS four consecutive quarters; the only question is which
   window is more recent. Publishing either one UNDATED is what caused this.
"""
import sys

from testkit import check, run_all

import terminal

def q(period_end, revenue=None):
    """One stored quarterly record. No revenue means a stub."""
    return {
        "period_end": period_end,
        "freq": "Q",
        "income": {} if revenue is None else {"Total Revenue": revenue},
    }


REV = ["Total Revenue"]

# Amazon exactly as stored on 16 Aug 2026: a stub June quarter in front of four
# good ones.
AMZN = [
    q("2026-06-30"),
    q("2026-03-31", 181_519_000_000.0),
    q("2025-12-31", 213_386_000_000.0),
    q("2025-09-30", 180_169_000_000.0),
    q("2025-06-30", 167_702_000_000.0),
]
AMZN_TTM = 742_776_000_000.0


# ── the window ───────────────────────────────────────────────────────────────

def test_four_good_quarters_sum_normally():
    total, end = terminal._ttm_window(AMZN[1:], "income", REV)
    check("sums four quarters", total == AMZN_TTM, str(total))
    check("dated to the newest in the window", end == "2026-03-31", str(end))


def test_a_stub_in_front_moves_the_window_back():
    """THE AMAZON CASE. $742.78B ending March, not $716.92B ending last
    December."""
    total, end = terminal._ttm_window(AMZN, "income", REV)
    check("stub is stepped over", total == AMZN_TTM, str(total))
    check("window ends a quarter earlier", end == "2026-03-31", str(end))


def test_two_stubs_in_front_move_it_back_twice():
    periods = [q("2026-09-30")] + AMZN + [q("2025-03-31", 143_313_000_000.0)]
    total, end = terminal._ttm_window(periods, "income", REV)
    check("steps over both", total == AMZN_TTM, str(total))
    check("still dated correctly", end == "2026-03-31", str(end))


def test_a_missing_quarter_in_the_middle_disqualifies_the_window():
    """The one that would be worse than the bug. A quarter absent from Firestore
    closes the list up, so four records that look neighbouring span fifteen
    months — and the sum would look entirely ordinary."""
    holed = [q("2026-06-30", 90e9), q("2026-03-31", 80e9),
             q("2025-09-30", 70e9), q("2025-06-30", 60e9)]
    total, end = terminal._ttm_window(holed, "income", REV)
    check("gap refuses to sum", total is None, str(total))
    check("and returns no date", end is None, str(end))


def test_a_gap_is_skipped_but_a_later_valid_window_is_still_found():
    periods = [q("2026-06-30", 90e9), q("2025-12-31", 80e9)] + AMZN[1:]
    total, _end = terminal._ttm_window(periods, "income", REV)
    check("finds the clean run behind the gap", total == AMZN_TTM, str(total))


def test_fewer_than_four_quarters_is_no_figure():
    total, end = terminal._ttm_window(AMZN[1:4], "income", REV)
    check("three quarters is not a year", total is None, str(total))
    check("no date either", end is None, str(end))


def test_no_periods_at_all():
    check("empty list", terminal._ttm_window([], "income", REV) == (None, None))


def test_a_stub_behind_the_window_is_irrelevant():
    """Only the four in the window matter."""
    periods = AMZN[1:] + [q("2025-03-31")]
    total, _end = terminal._ttm_window(periods, "income", REV)
    check("older stub ignored", total == AMZN_TTM, str(total))


def test_fourteen_week_quarters_are_still_consecutive():
    """Retail calendars run 13 or 14 weeks. A 98-day gap is a normal quarter,
    not a missing one."""
    periods = [q("2026-05-02", 10e9), q("2026-01-24", 10e9),
               q("2025-10-18", 10e9), q("2025-07-12", 10e9)]
    total, _end = terminal._ttm_window(periods, "income", REV)
    check("long quarters accepted", total == 40e9, str(total))


def test_an_unparseable_period_end_disqualifies_the_window():
    periods = [q("not-a-date", 10e9)] + AMZN[1:]
    total, _end = terminal._ttm_window(periods, "income", REV)
    check("bad date does not crash", total == AMZN_TTM, str(total))


# ── quarters vs fiscal year: whichever twelve months ends LATER ─────────────
#
# Measured across 55 large caps, 16 Aug 2026: THIRTEEN were publishing a fiscal
# year under a (TTM) heading, ten of them stale, including all four big banks.
# Both directions occur, which is why neither source can simply be preferred.

def fy(period_end, revenue):
    return {"period_end": period_end, "freq": "FY",
            "income": {"Total Revenue": revenue}}


def test_quarters_win_when_they_end_later():
    """AMAZON. Fiscal year ended 31 December; a stub June quarter pushes the
    window back to March — which is still later than December."""
    annual = [fy("2025-12-31", 716_920_000_000.0)]
    value, end = terminal._ttm_value(AMZN, annual, "income", REV)
    check("quarters chosen", value == AMZN_TTM, str(value))
    check("dated to March", end == "2026-03-31", str(end))
    check("not the annual", value != 716_920_000_000.0)


def test_the_fiscal_year_wins_when_it_ends_later():
    """ORACLE. Its year ends 31 MAY, later than the February its quarterly
    window reaches once its own stub is stepped over. Dropping the annual would
    swap a correct figure for a three-month-older one."""
    quarterly = [q("2026-05-31"), q("2026-02-28", 16e9), q("2025-11-30", 16e9),
                 q("2025-08-31", 16e9), q("2025-05-31", 16e9)]
    annual = [fy("2026-05-31", 67_360_000_000.0)]
    value, end = terminal._ttm_value(quarterly, annual, "income", REV)
    check("annual chosen", value == 67_360_000_000.0, str(value))
    check("dated to May", end == "2026-05-31", str(end))


def test_the_fiscal_year_is_used_when_there_is_no_quarterly_window():
    """BERKSHIRE. No complete run of four quarters; without the annual it has
    no revenue row at all."""
    annual = [fy("2025-12-31", 410_520_000_000.0)]
    quarterly = [q("2026-06-30"), q("2026-03-31", 181e9)]   # cannot make four
    value, end = terminal._ttm_value(quarterly, annual, "income", REV)
    check("annual rescues the row", value == 410_520_000_000.0, str(value))
    check("and is dated", end == "2025-12-31", str(end))


def test_a_tie_is_the_same_figure_either_way():
    """HOME DEPOT. Fiscal year end and newest quarter close the same day."""
    quarterly = [q("2026-01-31", 41e9), q("2025-10-31", 41e9),
                 q("2025-08-01", 41e9), q("2025-05-02", 41.68e9)]
    annual = [fy("2026-01-31", 164_680_000_000.0)]
    value, end = terminal._ttm_value(quarterly, annual, "income", REV)
    check("tie goes to the quarters", value == 164_680_000_000.0, str(value))
    check("same date regardless", end == "2026-01-31", str(end))


def test_nothing_anywhere_is_nothing():
    check("no data at all", terminal._ttm_value([], [], "income", REV) == (None, None))


def test_an_annual_record_with_no_value_is_not_chosen():
    annual = [{"period_end": "2026-12-31", "freq": "FY", "income": {}}]
    value, end = terminal._ttm_value(AMZN, annual, "income", REV)
    check("empty annual ignored", value == AMZN_TTM, str(value))
    check("quarters keep the date", end == "2026-03-31", str(end))


# ── consecutiveness helper ───────────────────────────────────────────────────

def test_consecutive_quarters():
    good = [q("2026-03-31"), q("2025-12-31"), q("2025-09-30"), q("2025-06-30")]
    check("normal calendar is consecutive", terminal._are_consecutive_quarters(good))


def test_a_skipped_quarter_is_not_consecutive():
    bad = [q("2026-03-31"), q("2025-09-30"), q("2025-06-30"), q("2025-03-31")]
    check("six-month gap rejected", not terminal._are_consecutive_quarters(bad))


def test_two_records_closing_the_same_day_are_not_consecutive():
    """A fiscal-year record and a quarterly record can share a period end —
    ORCL's 2026-Q2 and 2026-FY both close 2026-05-31. Summing both would
    double-count a quarter."""
    same = [q("2026-05-31"), q("2026-05-31"), q("2026-02-28"), q("2025-11-30")]
    check("zero-day gap rejected", not terminal._are_consecutive_quarters(same))


def main():
    return run_all(globals())


if __name__ == "__main__":
    sys.exit(main())
