#!/usr/bin/env python3
"""Which record fills a balance-sheet column when two close on the same day.

    python test_balance_display.py

No network, no Firestore.

WHY THIS FILE EXISTS — Alphabet, 18 August 2026.

He opened /GOOGL bal and the 2024-12-31 column was empty except Total Debt.
TEK2day held everything: two records close that day, and the page kept the wrong
one.

    2024-FY  63 balance-sheet fields, Total Assets $450.3B
    2024-Q4   4 balance-sheet fields, Total Assets missing

Yahoo posts a skeleton within hours of a release and fills it in later, so the
two records are routinely NOT equivalent. `_financial_payload` deduped by
period_end and kept whichever it reached first — the 4-field skeleton. Total
Debt survived only because it happened to be one of those four.

The same collision hid Total Debt at 2025-12-31: `2025-FY` carries $59.3B and
`2025-Q4` does not, and both hold 61 fields, so "keep the fuller record" alone
does not settle it. The tie must go to the ANNUAL.

Measured across 700 companies: 281 of them (40%) gain data, 14,166 balance-sheet
figures are recovered, and no period gets worse.

⚠️ NOT EVERY BLANK IS THIS BUG. GOOGL has no quarterly Total Debt at 2025-03-31,
2025-06-30 or 2025-09-30 because YAHOO does not publish it for those periods —
verified against Yahoo directly. Nothing here can or should fill those.
"""
import sys

import app
import terminal

_passed = 0
_failed = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
    else:
        _failed.append(f"{name}{(' — ' + detail) if detail else ''}")


def rec(period, period_end, **balance):
    return {"period": period, "period_end": period_end,
            "balance_sheet": balance, "income": {}, "cash_flow": {}}


FULL = {f"Field {i}": float(i) for i in range(60)}


def picked(all_fins, period_end):
    """The record whose values land in that column."""
    payload = app._financial_payload.__wrapped__ if hasattr(app._financial_payload, "__wrapped__") else app._financial_payload
    terminal._all_financials = lambda s: all_fins
    out = payload("TEST", "balance_sheet", terminal.BALANCE_FIELDS, "Balance Sheet")
    sec = out["sections"][0]
    idx = sec["periods"].index(period_end)
    return {r["label"]: r["values"][idx] for r in sec["rows"]}


def test_the_fuller_record_wins():
    """ALPHABET, EXACTLY AS STORED. A 4-field skeleton must not beat a 63-field
    annual sheet."""
    fins = [
        rec("2024-Q4", "2024-12-31", **{"Total Debt": 22.6e9}),
        rec("2024-FY", "2024-12-31", **{"Total Assets": 450.3e9, "Total Debt": 22.6e9, **FULL}),
    ]
    got = picked(fins, "2024-12-31")
    check("annual sheet is used", got.get("Total Assets") == terminal._fin(450.3e9),
          str(got.get("Total Assets")))


def test_order_does_not_decide_it():
    """The skeleton listed FIRST must still lose. The old code kept whichever it
    reached first, which is why this was a coin toss."""
    full = rec("2024-FY", "2024-12-31", **{"Total Assets": 450.3e9, **FULL})
    thin = rec("2024-Q4", "2024-12-31", **{"Total Debt": 22.6e9})
    for order in ([thin, full], [full, thin]):
        got = picked(list(order), "2024-12-31")
        check("order-independent", got.get("Total Assets") == terminal._fin(450.3e9),
              f"order={[r['period'] for r in order]} got={got.get('Total Assets')}")


def test_a_tie_on_field_count_goes_to_the_annual():
    """ALPHABET AGAIN, 2025-12-31. Both records hold 61 fields, but only the
    annual carries Total Debt — so counting alone cannot settle it."""
    quarterly = rec("2025-Q4", "2025-12-31", **FULL)
    annual = rec("2025-FY", "2025-12-31", **{"Total Debt": 59.3e9},
                 **{k: v for k, v in list(FULL.items())[:-1]})
    got = picked([quarterly, annual], "2025-12-31")
    check("annual wins the tie", got.get("Total Debt") == terminal._fin(59.3e9),
          str(got.get("Total Debt")))


def test_a_quarter_with_no_annual_twin_is_untouched():
    fins = [rec("2026-Q1", "2026-03-31", **{"Total Assets": 703.9e9, **FULL})]
    got = picked(fins, "2026-03-31")
    check("lone quarterly kept", got.get("Total Assets") == terminal._fin(703.9e9),
          str(got.get("Total Assets")))


def test_a_newer_quarterly_still_beats_an_older_annual():
    """Selection is per DATE. An annual record must never displace a different,
    more recent period."""
    fins = [
        rec("2025-FY", "2025-12-31", **{"Total Assets": 595.3e9, **FULL}),
        rec("2026-Q2", "2026-06-30", **{"Total Assets": 922.0e9, **FULL}),
    ]
    got = picked(fins, "2026-06-30")
    check("newest period keeps its own record",
          got.get("Total Assets") == terminal._fin(922.0e9), str(got.get("Total Assets")))


def test_an_entirely_empty_record_is_not_promoted():
    """JPM holds a 0-field balance sheet at 2026-06-30. Nothing to prefer it
    over, and nothing to invent — the column stays blank."""
    fins = [rec("2026-Q2", "2026-06-30")]
    got = picked(fins, "2026-06-30")
    check("empty stays empty", not got.get("Total Assets"), str(got.get("Total Assets")))


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
