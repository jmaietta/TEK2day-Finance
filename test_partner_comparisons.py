#!/usr/bin/env python3
"""Tests for /partner/v1/comparisons.

    python test_partner_comparisons.py

No network, no Firestore.

The gate for this step is FS1 ON A SET, which is a stronger promise than FS1 on
a single symbol: every requested symbol must be accounted for, in order,
whether or not we hold it. A comparison that quietly drops one is the dangerous
shape — a reader sees a complete-looking table, counts columns without
thinking, and never notices the company they cared about is absent.
"""
import sys

from testkit import check, run_all

import partner_api
import storage
import terminal

class Req:
    headers: dict = {}


UNIVERSE = {
    "NVDA": {"symbol": "NVDA", "name": "NVIDIA Corporation", "active": True},
    "AMD": {"symbol": "AMD", "name": "Advanced Micro Devices, Inc.", "active": True},
    "INTC": {"symbol": "INTC", "name": "Intel Corporation", "active": True},
}

SNAPS = {
    "NVDA": {"symbol": "NVDA", "price": 225.16, "market_cap": 5.49e12, "revenue": 2.53e11,
             "pe_ttm": 34.4, "eps_ttm": 6.54, "enterprise_value": 5.49e12,
             "ttm_as_of": "2026-04-30", "balance_sheet_as_of": "2026-04-30"},
    "AMD": {"symbol": "AMD", "price": 514.39, "market_cap": 8.53e11, "revenue": 4.13e10,
            "pe_ttm": 132.5, "eps_ttm": 3.88, "enterprise_value": 8.52e11},
    "INTC": {"symbol": "INTC", "price": 102.50, "market_cap": 5.23e11, "revenue": 5.70e10,
             "pe_ttm": None, "eps_ttm": -2.21, "enterprise_value": 5.60e11},
}


def install():
    storage.get_ticker_meta = lambda s: UNIVERSE.get(s)
    terminal._market_snapshot = lambda s: SNAPS.get(s)
    partner_api.require_kilby = lambda r: "test"


def call(symbols):
    result = partner_api.comparisons(Req(), symbols=symbols)
    if hasattr(result, "body"):
        import json
        return result.status_code, json.loads(result.body)
    return 200, result


# ── FS1 on a set ─────────────────────────────────────────────────────────────

def test_every_requested_symbol_gets_a_column():
    install()
    _, body = call("NVDA,AMD,VOO,INTC")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("all four present", got == ["NVDA", "AMD", "VOO", "INTC"], str(got))


def test_an_uncovered_symbol_is_not_dropped():
    """The dangerous shape: a reader counts columns and never notices."""
    install()
    _, body = call("NVDA,VOO")
    symbols = [c["symbol"] for c in body["data"]["companies"]]
    check("VOO keeps its column", "VOO" in symbols, str(symbols))
    voo = next(c for c in body["data"]["companies"] if c["symbol"] == "VOO")
    check("VOO marked uncovered", voo["covered"] is False)
    check("VOO listed in not_covered", body["data"]["not_covered"] == ["VOO"])


def test_uncovered_values_are_null_never_zero():
    install()
    _, body = call("NVDA,VOO")
    for row in body["data"]["rows"]:
        check(f"{row['field']} VOO is null", row["values"][1] is None, str(row["values"][1]))
        check(f"{row['field']} VOO display is null", row["display"][1] is None)


def test_uncovered_symbols_are_named_in_a_note():
    install()
    _, body = call("NVDA,VOO")
    notes = [w["note"] for w in body["quality"]["warnings"]]
    check("note names VOO", any("VOO" in n for n in notes), str(notes))
    check("note names the platform", any("TEK2day Finance" in n for n in notes))


def test_order_is_the_order_requested():
    """Not alphabetical, not by size — the order asked for is the order shown."""
    install()
    _, body = call("INTC,NVDA,AMD")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("order preserved", got == ["INTC", "NVDA", "AMD"], str(got))


def test_duplicates_collapse_but_keep_position():
    install()
    _, body = call("NVDA,AMD,NVDA")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("no duplicate column", got == ["NVDA", "AMD"], str(got))


def test_lowercase_and_spaces_are_accepted():
    install()
    _, body = call("nvda, amd")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("normalised", got == ["NVDA", "AMD"], str(got))


# ── wrong-company risk ───────────────────────────────────────────────────────
#
# Everything above tests that columns are PRESENT and in ORDER. None of it
# tests that column three's numbers belong to company three. A wrong-company
# column is worse in a comparison than on a single card: it sits under the
# right heading, beside companies that ARE right, so nothing looks odd.

def test_each_column_holds_its_own_companys_numbers():
    """Alignment relies on pool.map preserving input order. It does — but a
    guarantee nobody tests is a guarantee that can be refactored away."""
    install()
    _, body = call("NVDA,AMD,INTC")
    companies = [c["symbol"] for c in body["data"]["companies"]]
    price = next(r for r in body["data"]["rows"] if r["field"] == "price")
    expected = {"NVDA": 225.16, "AMD": 514.39, "INTC": 102.50}
    for i, sym in enumerate(companies):
        check(f"{sym} column has {sym}'s price",
              price["values"][i] == expected[sym],
              f"column {i} ({sym}) = {price['values'][i]}")


def test_alignment_survives_an_uncovered_symbol_in_the_middle():
    """The easiest way to shift every column by one is a gap in the middle."""
    install()
    _, body = call("NVDA,VOO,AMD")
    companies = [c["symbol"] for c in body["data"]["companies"]]
    check("order", companies == ["NVDA", "VOO", "AMD"], str(companies))
    price = next(r for r in body["data"]["rows"] if r["field"] == "price")
    check("NVDA still first", price["values"][0] == 225.16, str(price["values"][0]))
    check("gap in the middle", price["values"][1] is None, str(price["values"][1]))
    check("AMD still third", price["values"][2] == 514.39, str(price["values"][2]))


def test_a_snapshot_for_the_wrong_company_is_discarded():
    """FS1 on the way out. If the store ever answers with another company, that
    data must NOT appear under the requested ticker."""
    install()
    def _wrong(s):
        if s == "AMD":
            return {**SNAPS["INTC"]}          # INTC's numbers, asked for AMD
        return SNAPS.get(s)
    terminal._market_snapshot = _wrong
    _, body = call("NVDA,AMD")
    amd = next(c for c in body["data"]["companies"] if c["symbol"] == "AMD")
    check("AMD marked uncovered", amd["covered"] is False)
    price = next(r for r in body["data"]["rows"] if r["field"] == "price")
    check("INTC's price is NOT shown as AMD's", price["values"][1] is None,
          str(price["values"][1]))
    check("AMD is named as not covered", "AMD" in body["data"]["not_covered"])


def test_a_wrong_company_never_leaks_into_any_row():
    """Not just price — no row may carry the other company's figure."""
    install()
    terminal._market_snapshot = lambda s: ({**SNAPS["INTC"]} if s == "AMD" else SNAPS.get(s))
    _, body = call("NVDA,AMD")
    for row in body["data"]["rows"]:
        check(f"{row['field']} AMD column empty", row["values"][1] is None,
              f"{row['field']} = {row['values'][1]}")


def test_the_caller_is_not_told_why_a_column_is_empty():
    """A discarded wrong-company answer is an internal event. The caller sees a
    figure it does not have, never a diagnostic — his absolute rule."""
    install()
    terminal._market_snapshot = lambda s: ({**SNAPS["INTC"]} if s == "AMD" else SNAPS.get(s))
    _, body = call("NVDA,AMD")
    blob = str(body).lower()
    check("no mismatch wording", "mismatch" not in blob)
    check("no integrity wording", "integrity" not in blob)


# ── the frame ────────────────────────────────────────────────────────────────

def test_all_fifteen_metrics_are_always_present():
    """Unlike a single-company statement, a row is KEPT even when every value is
    missing. The metric list is the comparison's frame: a row that vanishes for
    one set of companies and appears for another makes two comparisons
    impossible to read against each other."""
    install()
    _, body = call("VOO")
    check("15 rows even with nothing to show", len(body["data"]["rows"]) == 15,
          str(len(body["data"]["rows"])))


def test_metric_order_matches_the_website():
    install()
    _, body = call("NVDA")
    labels = [r["label"] for r in body["data"]["rows"]]
    check("starts with Price", labels[0] == "Price", labels[0])
    check("Market Cap second", labels[1] == "Market Cap", labels[1])
    check("ends with EV/FCF", labels[-1] == "EV/FCF (TTM)", labels[-1])


def test_values_are_raw_and_display_is_rendered():
    install()
    _, body = call("NVDA")
    mc = next(r for r in body["data"]["rows"] if r["field"] == "market_cap")
    check("raw is a number", isinstance(mc["values"][0], float), str(mc["values"][0]))
    check("display renders", mc["display"][0] == "$5.49T", str(mc["display"][0]))


def test_a_missing_metric_on_a_covered_company_is_null():
    """Intel has no meaningful P/E when it is not earning. That is a fact about
    Intel, not a gap — and it must never render as zero."""
    install()
    _, body = call("NVDA,INTC")
    pe = next(r for r in body["data"]["rows"] if r["field"] == "pe_ttm")
    check("INTC P/E is null", pe["values"][1] is None, str(pe["values"][1]))
    check("INTC P/E display is null", pe["display"][1] is None)


def test_price_derived_figures_are_declared_live():
    install()
    _, body = call("NVDA")
    check("valuation_basis live", body["provenance"]["valuation_basis"] == "live_quote",
          str(body["provenance"]))


# ── limits ───────────────────────────────────────────────────────────────────

def test_seven_symbols_is_refused():
    install()
    status, body = call("A,B,C,D,E,F,G")
    check("400 for too many", status == 400, str(status))
    check("says the limit", "6" in body["detail"], body.get("detail", ""))
    check("carries no data", "data" not in body)


def test_six_symbols_is_allowed():
    install()
    status, _ = call("NVDA,AMD,INTC,AAA,BBB,CCC")
    check("six is fine", status == 200, str(status))


def test_no_symbols_is_refused():
    install()
    status, _ = call(",")
    check("404 for empty", status == 404, str(status))


def test_a_failing_snapshot_does_not_fail_the_comparison():
    """One bad symbol must not take the whole table down."""
    install()
    def _boom(s):
        if s == "AMD":
            raise RuntimeError("upstream is unhappy")
        return SNAPS.get(s)
    terminal._market_snapshot = _boom
    status, body = call("NVDA,AMD")
    check("still 200", status == 200, str(status))
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("AMD keeps its column", got == ["NVDA", "AMD"], str(got))
    amd = next(c for c in body["data"]["companies"] if c["symbol"] == "AMD")
    check("AMD marked uncovered", amd["covered"] is False)



# ── as-of dates travel per company ───────────────────────────────────────────
#
# A comparison is read ACROSS, so two columns whose TTM windows end on different
# dates are not strictly comparable — and nothing else in the response reveals
# it. Measured 16 Aug 2026: AMZN's window ended 31 March while MSFT's ended
# 30 June, because Yahoo had not filled Amazon's June quarter. Both figures were
# correct. Putting them side by side without saying so was not.

def test_each_company_carries_its_own_as_of_dates():
    install()
    _, body = call("NVDA,AMD")
    nvda = body["data"]["companies"][0]
    check("ttm date present", nvda.get("ttm_as_of") == "2026-04-30", str(nvda))
    check("balance sheet date present",
          nvda.get("balance_sheet_as_of") == "2026-04-30", str(nvda))


def test_columns_may_carry_different_as_of_dates():
    """THE CASE THIS EXISTS FOR. One stale quarter at Yahoo and two columns are
    describing different twelve-month periods."""
    install()
    amd = {**SNAPS["AMD"], "ttm_as_of": "2026-03-31", "balance_sheet_as_of": "2026-03-31"}
    terminal._market_snapshot = lambda s: (amd if s == "AMD" else SNAPS.get(s))
    _, body = call("NVDA,AMD")
    dates = [c.get("ttm_as_of") for c in body["data"]["companies"]]
    check("dates differ and both are stated", dates == ["2026-04-30", "2026-03-31"], str(dates))


def test_an_uncovered_column_claims_no_dates():
    """No data, no period. A date beside null figures would imply we hold
    something for that company."""
    install()
    _, body = call("NVDA,VOO")
    voo = body["data"]["companies"][1]
    check("no ttm date", voo.get("ttm_as_of") is None, str(voo))
    check("no balance sheet date", voo.get("balance_sheet_as_of") is None, str(voo))


def test_a_snapshot_without_dates_does_not_break_the_table():
    """Older cached snapshots predate these fields."""
    install()
    terminal._market_snapshot = lambda s: ({k: v for k, v in SNAPS["NVDA"].items()
                                            if not k.endswith("_as_of")}
                                           if s == "NVDA" else SNAPS.get(s))
    status, body = call("NVDA,AMD")
    check("still 200", status == 200, str(status))
    check("date is simply null",
          body["data"]["companies"][0].get("ttm_as_of") is None, str(body["data"]["companies"][0]))


def test_the_dates_are_defined():
    install()
    _, body = call("NVDA,AMD")
    defs = body["data"]["definitions"]
    check("ttm_as_of defined", "ttm_as_of" in defs, str(list(defs)))
    check("balance_sheet_as_of defined", "balance_sheet_as_of" in defs, str(list(defs)))


def main():
    return run_all(globals())


if __name__ == "__main__":
    sys.exit(main())
