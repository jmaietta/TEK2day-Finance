#!/usr/bin/env python3
"""Tests for the sanity checks and for what a partner is allowed to see.

    python test_checks.py

No network, no Firestore. Two things are pinned here:

1. A two-decimal EPS is a BAND, not a point. Standard public-company reporting
   carries EPS to two places, so a company earning under half a cent a share
   reports 0.00 — correctly. The old point comparison called that a 100% error
   and wrote a warning onto a clean record.

2. Internal check results never reach a partner. Telling a portfolio manager
   there is an error in the system invites them to distrust every other number
   we show them.
"""
import sys

import envelope
import proposals

_passed = 0
_failed = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
    else:
        _failed.append(f"{name}{(' — ' + detail) if detail else ''}")


def eps_check(eps, shares, ni):
    """Run the checks and return the EPS one, or None if it did not run."""
    doc = {
        "income": {
            "Diluted EPS": eps,
            "Diluted Average Shares": shares,
            "Net Income": ni,
        },
        "balance_sheet": {},
        "cash_flow": {},
    }
    for c in proposals.run_checks(doc, {}, "2025-Q3"):
        if c["name"].startswith("Diluted EPS"):
            return c
    return None


# ── the real record this was found on ────────────────────────────────────────

def test_abtc_real_record_passes():
    """ABTC 2025-Q3, exactly as stored in production on 2026-08-14.

    3,475,000 / 899,489,426 = 0.00386, which reports as 0.00. Nothing about
    this record is wrong, and the old check failed it at "100.0% apart".
    """
    c = eps_check(0.0, 899489426.0, 3475000.0)
    check("ABTC 2025-Q3 passes", c is not None and c["pass"],
          c["detail"] if c else "check did not run")


def test_sub_penny_earners_pass():
    """Any company whose true EPS rounds to 0.00 reports 0.00 correctly."""
    for shares, ni in ((900_000_000, 3_475_000), (500_000_000, 1_000_000),
                       (2_000_000_000, 8_000_000)):
        c = eps_check(0.0, shares, ni)
        check(f"sub-penny {shares:,}sh / {ni:,} passes", c is not None and c["pass"],
              c["detail"] if c else "did not run")


def test_normal_caps_still_pass():
    for eps, shares, ni in ((2.50, 1_000_000_000, 2_500_000_000),
                            (0.97, 15_200_000_000, 14_744_000_000),
                            (11.95, 2_490_000_000, 29_760_000_000)):
        c = eps_check(eps, shares, ni)
        check(f"eps {eps} passes", c is not None and c["pass"],
              c["detail"] if c else "did not run")


def test_genuinely_wrong_still_fails():
    """The band is only half a cent wide, so a real error is still caught."""
    for label, eps, shares, ni in (
        ("eps 100x too high", 5.00, 1_000_000_000, 1_000_000),
        ("eps off by 25x", 0.10, 899_489_426.0, 3_475_000.0),
        ("sign flipped", -2.50, 1_000_000_000, 2_500_000_000),
    ):
        c = eps_check(eps, shares, ni)
        check(f"{label} fails", c is not None and not c["pass"],
              c["detail"] if c else "did not run")


def test_rounding_band_scales_with_share_count():
    """Half a cent on 15bn shares is 76m of net income; on 1m shares it is
    5,000. The allowance must follow the share count, not be a flat number."""
    big = eps_check(0.0, 15_000_000_000, 70_000_000)
    check("0.00 on 15bn shares tolerates 70m", big is not None and big["pass"],
          big["detail"] if big else "did not run")
    small = eps_check(0.0, 1_000_000, 70_000_000)
    check("0.00 on 1m shares does NOT tolerate 70m",
          small is not None and not small["pass"],
          small["detail"] if small else "did not run")


def test_zero_shares_does_not_divide_by_zero():
    check("zero shares runs without error", eps_check(0.0, 0, 1_000_000) is None)


# ── what a partner may see ───────────────────────────────────────────────────

def test_internal_diagnostics_never_reach_a_partner():
    """HIS RULE: a Kilby user must never be told there is an error."""
    record = {
        "income": {"Total Revenue": 1.0},
        "balance_sheet": {"Total Assets": 1.0},
        "cash_flow": {"Free Cash Flow": 1.0},
        "data_warnings": [
            {"code": "Diluted EPS x shares vs net income",
             "detail": "0m vs 3m (100.0% apart)"},
            {"code": "Assets = Liabilities + Equity",
             "detail": "289,607m vs 282,962m (2.29% apart)"},
        ],
    }
    check("warnings_from emits nothing", envelope.warnings_from(record) == [])

    built = envelope.build("financial_statement", {"x": 1}, {"symbol": "WMT"},
                           {"symbol": "WMT"}, record=record)
    blob = str(built)
    check("no check name leaks", "Diluted EPS x shares" not in blob)
    check("no diagnostic leaks", "100.0% apart" not in blob)
    check("no balance diagnostic leaks", "2.29% apart" not in blob)
    check("quality reports ok", built["quality"]["status"] == "ok",
          str(built["quality"]))
    check("no warnings listed", built["quality"]["warnings"] == [])


def test_absence_still_travels():
    """Saying we do not hold something is allowed, and must keep working."""
    built = envelope.build("symbol_resolution", {"symbol": "NEWCO"},
                           {"symbol": "NEWCO"}, {"symbol": "NEWCO"},
                           warnings=[{"code": "not_covered",
                                      "note": "Not covered by TEK2day Finance."}])
    notes = [w["note"] for w in built["quality"]["warnings"]]
    check("absence note survives", notes == ["Not covered by TEK2day Finance."], str(notes))
    check("absence note names no fault", "error" not in str(notes).lower())


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
