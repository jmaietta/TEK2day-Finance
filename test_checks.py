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

from testkit import check, run_all

import envelope
import proposals

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



# ── stub detection: a skeleton is not a statement ───────────────────────────
#
# Yahoo posts a record within hours of a release with the field NAMES present
# and the numbers missing, then fills them in over days or weeks. Firestore
# stores those blanks as `nan`, NOT as absent keys — so a section is full of
# labels and empty of data.
#
# The old test asked whether a section had any fields at all. Oracle's quarter
# ending 2024-11-30 has 66 balance-sheet field names of which 2 hold a number,
# so it read as complete, and the repair job skipped it on every run since
# November 2024. The column is still blank on the website.

def _sheet(**overrides):
    """A balance sheet shaped like Yahoo's: many keys, mostly NaN."""
    nan = float("nan")
    block = {name: nan for name in (
        "Total Assets", "Total Debt", "Cash And Cash Equivalents",
        "Common Stock Equity", "Net PPE", "Goodwill", "Working Capital",
    )}
    block.update(overrides)
    return block


def _doc(income=None, balance=None, cash_flow=None):
    return {
        "income": {"Total Revenue": 1.0, "Net Income": 1.0} if income is None else income,
        "balance_sheet": _sheet(**{"Total Assets": 1.0}) if balance is None else balance,
        "cash_flow": {"Operating Cash Flow": 1.0} if cash_flow is None else cash_flow,
    }


def test_a_complete_record_is_not_a_stub():
    check("complete record passes", not proposals.is_stub(_doc()))


def test_an_entirely_empty_section_is_a_stub():
    """The case the old test already caught. It must keep working."""
    check("empty balance sheet", proposals.is_stub(_doc(balance={})))
    check("empty income", proposals.is_stub(_doc(income={})))
    check("empty cash flow", proposals.is_stub(_doc(cash_flow={})))


def test_a_section_of_field_names_with_no_numbers_is_a_stub():
    """ORACLE 2024-Q4, THE CASE THE OLD TEST MISSED. Seven field names, every
    value NaN. Reads as populated, contains nothing."""
    check("all-NaN balance sheet is a stub", proposals.is_stub(_doc(balance=_sheet())))


def test_two_obscure_lines_do_not_make_a_balance_sheet():
    """Oracle's record is not all-NaN — it holds two deferred-tax figures. So
    "does it contain ANY number" is also the wrong question; counting is."""
    sheet = _sheet()
    sheet["Non Current Deferred Taxes Liabilities"] = 2_864_000_000.0
    sheet["Non Current Deferred Liabilities"] = 2_864_000_000.0
    check("real numbers without Total Assets", proposals.is_stub(_doc(balance=sheet)))


def test_the_anchor_line_alone_is_enough():
    """The inverse. A sparse statement that HAS its headline figure is usable,
    and repairing it would only overwrite nothing."""
    check("Total Assets alone", not proposals.is_stub(_doc(balance={"Total Assets": 5.0})))


def test_income_accepts_either_headline():
    """Not every issuer reports "Total Revenue" under that name — banks in
    particular — and net income is universal."""
    check("net income only", not proposals.is_stub(_doc(income={"Net Income": 1.0})))
    check("total revenue only", not proposals.is_stub(_doc(income={"Total Revenue": 1.0})))
    check("neither is a stub", proposals.is_stub(_doc(income={"Gross Profit": 1.0})))


def test_a_nan_anchor_is_missing():
    check("NaN Total Assets", proposals.is_stub(_doc(balance=_sheet(**{"Total Assets": float("nan")}))))
    check("None Total Assets", proposals.is_stub(_doc(balance=_sheet(**{"Total Assets": None}))))


def test_a_missing_section_key_is_a_stub():
    doc = _doc()
    del doc["cash_flow"]
    check("absent section", proposals.is_stub(doc))


# ── the safety trip counts COMPANIES, not records ───────────────────────────

def test_the_trip_measures_tickers_not_records():
    """The trip disables repairs when detection looks broken. Measured per
    RECORD it is not bounded by 1, so a change that finds more stubs per company
    would trip it and switch the repair off — which is what recognising
    skeletons would have done: 0.4 to 1.3 reachable stubs per ticker."""
    import pull_quarterly_financials as pull
    pull._tripped = False
    pull._tickers_seen = 100
    pull._reviewed = 130          # 1.3 records per ticker — over 0.80 per RECORD
    pull._tickers_populated = 40  # but only 40% of companies
    check("does not trip on records", not pull._safety_trip(100))

    pull._tickers_populated = 90  # 90% of companies really do look broken
    check("trips on companies", pull._safety_trip(100))
    pull._tripped = False


def test_the_trip_stays_quiet_on_a_small_run():
    import pull_quarterly_financials as pull
    pull._tripped = False
    pull._tickers_seen = 10
    pull._tickers_populated = 10
    check("below the minimum", not pull._safety_trip(10))
    pull._tripped = False



# ── completeness: "complete" must mean usable ───────────────────────────────
#
# Kilby acts on this one word: `complete` -> quote it normally, `stub` -> "we
# hold no usable data for this quarter". The old test asked only whether a
# section held ANY real number, so ORCL's quarter ending 2024-11-30 — 7 real
# figures out of 176, with Total Assets, Total Debt and Cash all blank —
# reported itself `complete`.

def _cw(income=None, balance=None, cash_flow=None, **extra):
    doc = {
        "income": {"Total Revenue": 1.0, "Net Income": 1.0} if income is None else income,
        "balance_sheet": {"Total Assets": 1.0} if balance is None else balance,
        "cash_flow": {"Operating Cash Flow": 1.0} if cash_flow is None else cash_flow,
    }
    doc.update(extra)
    return doc


def test_a_usable_record_is_complete():
    check("complete", envelope.completeness_block(_cw())["status"] == envelope.COMPLETE)


def test_the_oracle_record_is_a_stub():
    """7 real figures out of 176. Two obscure liability lines are not a balance
    sheet."""
    nan = float("nan")
    sheet = {n: nan for n in ("Total Assets", "Total Debt", "Cash And Cash Equivalents")}
    sheet["Non Current Deferred Taxes Liabilities"] = 2_864_000_000.0
    sheet["Non Current Deferred Liabilities"] = 2_864_000_000.0
    out = envelope.completeness_block(_cw(balance=sheet))
    check("stub", out["status"] == envelope.STUB, out["status"])
    check("counts stay honest", out["sections"]["balance_sheet"] == "2/5",
          out["sections"]["balance_sheet"])


def test_a_summary_statement_is_not_a_stub():
    """HIS RULING, 16 Aug: "we can't afford mistakes where summary statements
    are flagged and withheld." CMBT's cash flow holds 8 fields — but they are
    every headline total, and a reader can use it. A retention-based rule was
    built and abandoned because it marked this `stub`."""
    cmbt = {n: 1.0 for n in (
        "Operating Cash Flow", "Investing Cash Flow", "Financing Cash Flow",
        "Free Cash Flow", "Beginning Cash Position", "End Cash Position",
        "Changes In Cash", "Effect Of Exchange Rate Changes")}
    check("summary cash flow survives",
          envelope.completeness_block(_cw(cash_flow=cmbt))["status"] == envelope.COMPLETE)


def test_the_direct_method_is_not_a_stub():
    """Foreign issuers reporting under the DIRECT method label operating cash
    flow differently. Measured 17 Aug: CILJF, CYATY and PTXKY are 70-100%
    populated and were flagged purely on the label."""
    direct = {"Cash Flowsfromusedin Operating Activities Direct": 1.0,
              "Classesof Cash Receiptsfrom Operating Activities": 1.0}
    check("direct method survives",
          envelope.completeness_block(_cw(cash_flow=direct))["status"] == envelope.COMPLETE)


def test_a_missing_anchor_is_a_stub():
    check("no Total Assets", envelope.completeness_block(
        _cw(balance={"Goodwill": 1.0, "Net PPE": 1.0}))["status"] == envelope.STUB)
    check("no operating cash flow", envelope.completeness_block(
        _cw(cash_flow={"Free Cash Flow": 1.0}))["status"] == envelope.STUB)


def test_income_accepts_either_anchor():
    check("net income only", envelope.completeness_block(
        _cw(income={"Net Income": 1.0}))["status"] == envelope.COMPLETE)
    check("revenue only", envelope.completeness_block(
        _cw(income={"Total Revenue": 1.0}))["status"] == envelope.COMPLETE)


def test_a_nan_anchor_does_not_count():
    """Firestore stores Yahoo's blanks as nan, not as absent keys."""
    check("nan anchor", envelope.completeness_block(
        _cw(balance={"Total Assets": float("nan"), "Goodwill": 1.0}))["status"] == envelope.STUB)


def test_an_existing_record_is_never_absent():
    """`absent` means "we hold no record" and makes Kilby say "coverage begins
    March 2025" — false for a company we hold years of. An existing but empty
    record is a `stub`: "we hold no usable data for this quarter". His ruling,
    16 Aug. AMZN's June 2026 quarter is empty here AND at Yahoo, so no pull can
    ever fill it, and Kilby has to answer about it today."""
    out = envelope.completeness_block(_cw(income={}, balance={}, cash_flow={}))
    check("empty record is a stub", out["status"] == envelope.STUB, out["status"])


def test_no_record_at_all_is_absent():
    check("None is absent", envelope.completeness_block(None)["status"] == envelope.ABSENT)


def test_a_warning_makes_it_partial():
    check("partial", envelope.completeness_block(
        _cw(data_warnings=[{"code": "x"}]))["status"] == envelope.PARTIAL)


def test_the_repair_job_and_the_contract_share_one_rule():
    """Two copies of "did the statement arrive?" would drift."""
    check("one source of truth", proposals.ANCHOR_FIELDS is envelope.ANCHOR_FIELDS)



def test_every_status_carries_its_own_definition():
    """FS5 — the description travels with the value. A partner receiving the
    word "stub" must not have to look up what it means: these four words lived
    only in code comments and in our own planning documents, neither of which a
    consumer can see."""
    out = envelope.completeness_block(_cw())
    meaning = out.get("meaning") or {}
    for state in (envelope.COMPLETE, envelope.PARTIAL, envelope.STUB, envelope.ABSENT):
        check(f"{state} defined", bool(meaning.get(state)), str(list(meaning)))
    check("the status returned is one of them", out["status"] in meaning, out["status"])


def test_the_legend_ships_even_when_there_is_no_record():
    """`absent` is exactly the case a consumer is most likely to misread."""
    out = envelope.completeness_block(None)
    check("legend present on absent", envelope.ABSENT in (out.get("meaning") or {}), str(out))


def test_complete_admits_its_own_limit():
    """`complete` means as complete as our SOURCE has. Matching the filing needs
    SEC.gov or a data partner, which TEK2day does not have, and claiming
    otherwise is the one overstatement an institutional user cannot forgive."""
    text = envelope.STATUS_MEANING[envelope.COMPLETE].lower()
    check("says 'as complete as our source has'", "our source" in text, text)


def main():
    return run_all(globals())


if __name__ == "__main__":
    sys.exit(main())
