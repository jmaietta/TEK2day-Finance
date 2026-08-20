#!/usr/bin/env python3
"""EPS: computed from the record's own components, shown in accounting form.

    python test_eps.py

No network, no Firestore.

WHY — 18 August 2026.

SQNS 2025-Q4 stored a Diluted EPS of +5.62 against a net loss of $87,127,000
over 15,504,809 shares. The company lost $5.62 a share; the record read as a
profit. Two causes, and only one of them is Yahoo's: SQNS is still positive on
Yahoo's own site, while NKTR was corrected by Yahoo to -1.87 and TEK2day still
held +1.87 — ingestion fills empty fields and never overwrites, so a figure
wrong on arrival stayed wrong.

Then share counts were made to follow Yahoo's current basis (a split makes the
old count WRONG, not merely old). A stored per-share figure frozen on an older
basis would openly contradict the count beside it, so EPS is now DERIVED and the
two cannot disagree.

⚠️ THE UNITS WERE PROVEN BEFORE ANY OF THIS WAS BUILT — his instruction, and the
right one. Across all 9,911 tickers, |EPS x shares| / |net income| lands at ~1
for 56,274 of 56,778 records (99.1%). A denomination fault would show a fat
cluster at 1,000x; ten scattered records sit there. Net income and share counts
are in the same units, so the decimal point is where it should be.
"""
import sys

from testkit import check, run_all

import terminal

# ── the computation ──────────────────────────────────────────────────────────

def test_a_loss_computes_negative():
    """SQNS 2025-Q4, exactly as stored."""
    got = terminal.computed_eps({"Diluted Average Shares": 15_504_809.0,
                                 "Net Income": -87_127_000.0})
    check("loss is negative", round(got, 2) == -5.62, str(got))


def test_a_profit_computes_positive():
    got = terminal.computed_eps({"Diluted Average Shares": 1_000.0,
                                 "Net Income": 2_500.0})
    check("profit is positive", got == 2.5, str(got))


def test_net_income_to_common_is_preferred():
    """After preferred holders are paid. Using total net income fails every
    company with preferred stock, by exactly its preferred dividends, in every
    period."""
    got = terminal.computed_eps({"Diluted Average Shares": 100.0,
                                 "Net Income": 1000.0,
                                 "Net Income Common Stockholders": 800.0})
    check("common is used", got == 8.0, str(got))


def test_basic_eps_uses_the_basic_share_count():
    income = {"Basic Average Shares": 200.0, "Diluted Average Shares": 250.0,
              "Net Income": 1000.0}
    check("basic", terminal.computed_eps(income, "Basic EPS") == 5.0)
    check("diluted", terminal.computed_eps(income, "Diluted EPS") == 4.0)


def test_missing_components_return_none():
    """The caller then keeps whatever was reported rather than inventing one."""
    check("no shares", terminal.computed_eps({"Net Income": 100.0}) is None)
    check("no income", terminal.computed_eps({"Diluted Average Shares": 100.0}) is None)
    check("zero shares", terminal.computed_eps(
        {"Diluted Average Shares": 0.0, "Net Income": 100.0}) is None)
    check("not a dict", terminal.computed_eps(None) is None)


def test_nan_components_return_none():
    """Firestore stores Yahoo's blanks as nan, not as absent keys."""
    nan = float("nan")
    check("nan shares", terminal.computed_eps(
        {"Diluted Average Shares": nan, "Net Income": 100.0}) is None)
    check("nan income", terminal.computed_eps(
        {"Diluted Average Shares": 100.0, "Net Income": nan}) is None)


# ── the accounting convention ────────────────────────────────────────────────

def test_positive_shows_as_dollars():
    check("plain", terminal._eps(5.62) == "$5.62", terminal._eps(5.62))
    check("zero", terminal._eps(0) == "$0.00", terminal._eps(0))


def test_negative_shows_in_brackets():
    """His call: ($x.xx). A leading minus is easy to miss at 12px in a table of
    forty rows; brackets are not."""
    check("loss", terminal._eps(-5.62) == "($5.62)", terminal._eps(-5.62))
    check("large loss", terminal._eps(-1234.5) == "($1,234.50)", terminal._eps(-1234.5))


def test_missing_renders_empty():
    check("None", terminal._eps(None) == "")
    check("nan", terminal._eps(float("nan")) == "")


def test_thousands_are_separated():
    check("grouping", terminal._eps(12345.678) == "$12,345.68", terminal._eps(12345.678))


# ── the bracket convention, across every financial formatter ─────────────────
#
# His call, 18 Aug: negatives show as () rather than a minus — but for CURRENCY
# only. A negative multiple is not an accounting entry, and "(2.2x)" reads more
# like a footnote marker than a number.

def test_money_uses_brackets():
    check("trillions", terminal._dollar(4.5e12) == "$4.50T", terminal._dollar(4.5e12))
    check("negative millions", terminal._dollar(-87_127_000) == "($87.1M)",
          terminal._dollar(-87_127_000))
    check("negative billions", terminal._dollar(-1.2e9) == "($1.20B)",
          terminal._dollar(-1.2e9))
    check("missing", terminal._dollar(None) == "N/A")


def test_statement_figures_use_brackets():
    """Income and cash flow statements are full of negatives — capex, interest
    expense, a loss-making quarter."""
    check("positive", terminal._fin(1.2e9) == "1.2B", terminal._fin(1.2e9))
    check("negative", terminal._fin(-8.07e6) == "(8.1M)", terminal._fin(-8.07e6))
    check("small negative", terminal._fin(-250) == "(250.00)", terminal._fin(-250))
    check("nan stays blank", terminal._fin(float("nan")) == "")


def test_ratios_keep_the_minus_sign():
    """Brackets are for currency. His call."""
    check("positive", terminal._ratio(34.9) == "34.9x", terminal._ratio(34.9))
    check("negative", terminal._ratio(-2.2) == "-2.2x", terminal._ratio(-2.2))
    check("large negative", terminal._ratio(-132.5) == "-132x", terminal._ratio(-132.5))


def test_the_price_change_keeps_the_minus_sign():
    """A price move is a market convention, not an accounting one, and it is
    already colour-coded."""
    check("negative change", terminal._price(-1.23) == "$-1.23", terminal._price(-1.23))


def test_plain_numbers_use_brackets():
    check("positive", terminal._num(12.5) == "12.50", terminal._num(12.5))
    check("negative", terminal._num(-12.5) == "(12.50)", terminal._num(-12.5))


# ── the analyst count, which was a live 500 ──────────────────────────────────

def test_a_nan_analyst_count_does_not_raise():
    """THIS TOOK WHOLE COMPANY PAGES DOWN. The old line was
    `str(int(val)) if val is not None else "N/A"` — and `nan is not None` is
    True, so `int(nan)` raised. Measured 19 Aug 2026: /CANF, /CRML and /LOT
    returned "cannot convert float NaN to integer" instead of a summary, roughly
    250 companies across the universe. Not a wrong number — no page at all."""
    check("nan", terminal._analyst_count(float("nan")) == "N/A",
          terminal._analyst_count(float("nan")))
    check("none", terminal._analyst_count(None) == "N/A")
    check("inf", terminal._analyst_count(float("inf")) == "N/A")


def test_an_analyst_count_renders_as_a_whole_number():
    check("plain", terminal._analyst_count(40.0) == "40", terminal._analyst_count(40.0))
    check("grouped", terminal._analyst_count(1234.0) == "1,234",
          terminal._analyst_count(1234.0))


def test_the_website_and_terminal_share_one_analyst_formatter():
    """Two copies of this line existed and both carried the same bug."""
    import app
    check("website uses it", "terminal._analyst_count" in
          __import__("inspect").getsource(app._estimates_payload))


def main():
    return run_all(globals())


if __name__ == "__main__":
    sys.exit(main())
