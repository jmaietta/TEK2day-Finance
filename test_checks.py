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


def eps_check(eps, shares, ni, ni_common=None):
    """Run the checks and return the EPS one, or None if it did not run."""
    income = {
        "Diluted EPS": eps,
        "Diluted Average Shares": shares,
        "Net Income": ni,
    }
    if ni_common is not None:
        income["Net Income Common Stockholders"] = ni_common
    doc = {
        "income": income,
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

def test_agnc_2024_passes_with_preferred_dividends():
    """EPS is net income to COMMON divided by diluted shares:

        $731,000,000 / 786,000,000 shares = $0.93

    AGNC is a mortgage REIT and carries heavy preferred stock. Checked against
    total net income of $863,000,000 it failed by exactly its preferred
    dividends -- $132,000,000 -- every period, forever."""
    c = eps_check(0.93, 786_000_000, 863_000_000, ni_common=731_000_000)
    check("AGNC FY2024 passes", c is not None and c["pass"],
          c["detail"] if c else "did not run")


def test_preferred_heavy_company_fails_without_the_common_figure():
    """Proves the fix is doing the work: the same record checked against total
    net income is what used to fail."""
    c = eps_check(0.93, 786_000_000, 863_000_000)
    check("without NI-common it fails", c is not None and not c["pass"],
          c["detail"] if c else "did not run")


def test_common_figure_is_preferred_when_both_exist():
    c = eps_check(1.00, 1_000_000_000, 5_000_000_000, ni_common=1_000_000_000)
    check("uses NI-common not NI", c is not None and c["pass"],
          c["detail"] if c else "did not run")


def test_falls_back_to_net_income_when_common_is_absent():
    """Most companies have no preferred stock and store no common figure."""
    c = eps_check(2.50, 1_000_000_000, 2_500_000_000)
    check("falls back to Net Income", c is not None and c["pass"],
          c["detail"] if c else "did not run")


def test_nan_common_figure_falls_back():
    c = eps_check(2.50, 1_000_000_000, 2_500_000_000, ni_common=float("nan"))
    check("NaN common figure falls back", c is not None and c["pass"],
          c["detail"] if c else "did not run")


def test_a_genuinely_wrong_eps_still_fails():
    """AERA FY2025, a real record: $1,455,448 / 2,396,505 shares = $0.61, but
    the stored EPS is 0.00. Sixty-one cents does not round to zero. The check
    must keep catching this."""
    c = eps_check(0.0, 2_396_505, 1_455_448, ni_common=1_455_448)
    check("AERA's wrong EPS still fails", c is not None and not c["pass"],
          c["detail"] if c else "did not run")


def balance_check(assets, liabilities, equity, minority=None):
    bs = {
        "Total Assets": assets,
        "Total Liabilities Net Minority Interest": liabilities,
        "Stockholders Equity": equity,
    }
    if minority is not None:
        bs["Minority Interest"] = minority
    doc = {"income": {}, "balance_sheet": bs, "cash_flow": {}}
    for c in proposals.run_checks(doc, {}, "2023-FY"):
        if c["name"].startswith("Assets"):
            return c
    return None


def test_walmart_2023_reconciles_exactly():
    """The real record. `Stockholders Equity` is the PARENT's share only;
    Walmart consolidates Walmex and Flipkart and carries the rest in Minority
    Interest. Without it:

        159,206 + 76,693          = 235,899  vs assets 243,197  -> "3% off"
        159,206 + 76,693 + 7,298  = 243,197  vs assets 243,197  -> exact

    Nine of Walmart's eleven periods were flagged as broken balance sheets.
    None of them were."""
    c = balance_check(243_197e6, 159_206e6, 76_693e6, 7_298e6)
    check("WMT 2023-FY passes", c is not None and c["pass"], c["detail"] if c else "did not run")
    check("WMT 2023-FY is exact", c is not None and "0.00%" in c["detail"],
          c["detail"] if c else "")


def test_exxon_2023_reconciles_exactly():
    c = balance_check(376_317e6, 163_779e6, 204_802e6, 7_736e6)
    check("XOM 2023-FY passes", c is not None and c["pass"], c["detail"] if c else "did not run")


def test_companies_without_minority_interest_are_unaffected():
    """Eight of the golden ten have none and always passed. They must keep
    passing — the fix must not depend on the field being present."""
    c = balance_check(1_000e6, 600e6, 400e6)
    check("no minority interest still passes", c is not None and c["pass"],
          c["detail"] if c else "did not run")


def test_a_genuinely_broken_balance_sheet_still_fails():
    """The point is to stop flagging SOUND records, not to stop checking."""
    c = balance_check(1_000e6, 600e6, 100e6, 10e6)
    check("real imbalance still fails", c is not None and not c["pass"],
          c["detail"] if c else "did not run")


def test_minority_interest_cannot_mask_a_real_imbalance():
    """A small minority interest must not paper over a large gap."""
    c = balance_check(1_000e6, 300e6, 300e6, 5e6)
    check("small MI does not rescue a big gap", c is not None and not c["pass"],
          c["detail"] if c else "did not run")


def test_nan_minority_interest_is_treated_as_absent():
    """Firestore stores Yahoo's NaN verbatim; adding it would poison the sum."""
    c = balance_check(1_000e6, 600e6, 400e6, float("nan"))
    check("NaN minority interest ignored", c is not None and c["pass"],
          c["detail"] if c else "did not run")


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
