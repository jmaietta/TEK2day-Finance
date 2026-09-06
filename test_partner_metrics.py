#!/usr/bin/env python3
"""Tests for /partner/v1/equities/metrics.

    python test_partner_metrics.py

No network, no Firestore.

Two gates. FS1 ON A SET, as the comparison has: every requested symbol comes
back, in order, whether or not we hold it — a watchlist analysis that silently
dropped a name would report coverage the caller does not have.

And NO SIX-SYMBOL CAP. The comparison caps because each of its columns is a live
quote; nothing here is live, so a real watchlist must go through in one request.
That is the whole reason this endpoint exists rather than reusing /comparisons.
"""
import sys
from copy import deepcopy

from testkit import check, run_all

import partner_api
import storage
import watchlist_metrics


class Req:
    headers: dict = {}


UNIVERSE = {
    "NVDA": {"symbol": "NVDA", "name": "NVIDIA Corporation", "active": True},
    "AMD": {"symbol": "AMD", "name": "Advanced Micro Devices, Inc.", "active": True},
    "INTC": {"symbol": "INTC", "name": "Intel Corporation", "active": True},
}


def _series(symbol, closes, first_day=1):
    return [
        {"symbol": symbol, "date": f"2026-01-{first_day + i:02d}", "close": close}
        for i, close in enumerate(closes)
    ]


# NVDA rises into its average; INTC falls away from it. AMD is held but has only
# a few sessions, which must produce nulls rather than a short-window average.
PRICES = {
    "NVDA": _series("NVDA", [200.0] * 9 + [240.0]),
    "AMD": _series("AMD", [500.0, 505.0, 510.0]),
    "INTC": _series("INTC", [120.0] * 9 + [90.0]),
}

ESTIMATES = {
    "NVDA": [{"symbol": "NVDA", "date": "2026-01-10", "eps_avg": 8.0}],
    "AMD": [{"symbol": "AMD", "date": "2026-01-03", "eps_avg": 4.0}],
    "INTC": [{"symbol": "INTC", "date": "2026-01-10", "eps_avg": -1.5}],
}


FINANCIALS = {
    "NVDA": [
        {"symbol": "NVDA", "period": "2025-FY", "period_end": "2025-01-31",
         "income": {"Diluted EPS": 2.0, "Diluted Average Shares": 100}},
        {"symbol": "NVDA", "period": "2025-Q3", "period_end": "2025-10-31",
         "income": {"Diluted EPS": 0.8, "Diluted Average Shares": 90}},
    ],
    "INTC": [
        {"symbol": "INTC", "period": "2025-FY", "period_end": "2025-12-31",
         "income": {"Diluted EPS": -1.0, "Diluted Average Shares": 20}},
    ],
}


def install():
    storage.get_ticker_meta = lambda s: UNIVERSE.get(s)
    storage.get_prices_history = lambda s, limit=300: PRICES.get(s, [])
    storage.get_estimate_history = lambda s, limit=90: ESTIMATES.get(s, [])
    # The endpoint now reads a fourth dataset. Exercise the real financial
    # calculations with explicit fixtures, never an unstubbed Firestore read.
    storage.get_all_financials = lambda s: deepcopy(FINANCIALS.get(s, []))
    partner_api.require_kilby = lambda r: "test"


def call(symbols):
    result = partner_api.equity_metrics(Req(), symbols=symbols)
    if hasattr(result, "body"):
        import json
        return result.status_code, json.loads(result.body)
    return 200, result


def _row(body, symbol):
    return next(c for c in body["data"]["companies"] if c["symbol"] == symbol)


# ── FS1 on a set ─────────────────────────────────────────────────────────────

def test_every_requested_symbol_comes_back_in_order():
    install()
    _, body = call("NVDA,AMD,VOO,INTC")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("all four present, in order", got == ["NVDA", "AMD", "VOO", "INTC"], str(got))


def test_an_uncovered_symbol_is_marked_not_dropped():
    install()
    _, body = call("NVDA,VOO")
    voo = _row(body, "VOO")
    check("VOO keeps its row", voo is not None)
    check("VOO marked uncovered", voo["covered"] is False)
    check("VOO values null", voo["price"] is None and voo["forward_pe"] is None)
    check("VOO named in not_covered", body["data"]["not_covered"] == ["VOO"])


def test_uncovered_symbols_are_named_in_a_warning():
    install()
    _, body = call("NVDA,VOO")
    notes = [w["note"] for w in body["quality"]["warnings"]]
    check("warning names VOO", any("VOO" in n for n in notes), str(notes))
    check("quality flagged", body["quality"]["status"] == "warning", body["quality"]["status"])


def test_duplicates_collapse_but_order_is_kept():
    install()
    _, body = call("AMD,NVDA,AMD")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("de-duplicated, order kept", got == ["AMD", "NVDA"], str(got))


# ── the cap this endpoint deliberately does not share ────────────────────────

def test_more_than_six_symbols_is_accepted():
    """/comparisons would 400 here. This must not."""
    install()
    status, body = call("NVDA,AMD,INTC,VOO,SPY,QQQ,IWM,DIA")
    check("not a 400", status == 200, str(status))
    check("eight rows returned", len(body["data"]["companies"]) == 8,
          str(len(body["data"]["companies"])))


def test_beyond_the_ceiling_is_a_clean_400():
    install()
    status, body = call(",".join(f"SYM{i}" for i in range(partner_api._MAX_METRICS_SYMBOLS + 1)))
    check("400", status == 400, str(status))
    check("named error", body.get("error") == "too_many_symbols", str(body.get("error")))


# ── the figures themselves ───────────────────────────────────────────────────

def test_trend_reports_direction_against_the_ten_day_average():
    install()
    _, body = call("NVDA,INTC")
    check("NVDA above", _row(body, "NVDA")["trend"] == "above")
    check("INTC below", _row(body, "INTC")["trend"] == "below")


def test_thin_history_yields_nulls_not_a_short_window_average():
    install()
    _, body = call("AMD")
    amd = _row(body, "AMD")
    check("AMD covered", amd["covered"] is True)
    check("AMD has a price", amd["price"] == 510.0, str(amd["price"]))
    check("no ten-day average", amd["ma_10"] is None)
    check("no trend", amd["trend"] is None)


def test_a_loss_making_estimate_reports_no_multiple():
    install()
    _, body = call("INTC")
    intc = _row(body, "INTC")
    check("forward eps present", intc["forward_eps"] == -1.5)
    check("no forward pe", intc["forward_pe"] is None)


def test_forward_pe_is_price_over_consensus():
    install()
    _, body = call("NVDA")
    check("240 / 8", _row(body, "NVDA")["forward_pe"] == 30.0,
          str(_row(body, "NVDA")["forward_pe"]))


def test_response_declares_stored_not_live_valuation():
    """Stored closes, not quotes — a consumer must not read these as live prices."""
    install()
    _, body = call("NVDA")
    basis = body["provenance"]["valuation_basis"]
    check("stored", basis == "stored", basis)
    check("upstream named", body["provenance"]["upstream"] == "Yahoo Finance",
          str(body["provenance"]["upstream"]))


def test_price_derived_response_declares_its_adjustment_basis():
    """Stored closes are auto-adjusted; every price-derived response says so."""
    install()
    _, body = call("NVDA")
    check("adjustment_basis present", "adjustment_basis" in body, str(sorted(body.keys())))


def test_the_response_carries_one_shared_date_axis():
    install()
    _, body = call("NVDA,AMD,INTC")

    dates = body["data"]["dates"]
    assert dates == sorted(dates)
    for company in body["data"]["companies"]:
        check(f"{company['symbol']} closes match the axis",
              len(company["closes"]) == len(dates),
              f"{len(company['closes'])} vs {len(dates)}")


def test_a_symbol_with_no_history_is_all_nulls_not_a_short_row():
    install()
    _, body = call("NVDA,VOO")

    voo = _row(body, "VOO")
    dates = body["data"]["dates"]
    check("VOO padded to the axis", len(voo["closes"]) == len(dates))
    check("VOO all null", all(value is None for value in voo["closes"]))


def test_empty_request_is_a_clean_miss():
    install()
    status, body = call(",,,")
    check("not a 500", status in (200, 404), str(status))


def test_financial_history_drives_market_cap_and_reported_eps():
    install()
    _, body = call("NVDA,INTC")
    nvda, intc = _row(body, "NVDA"), _row(body, "INTC")
    check("latest quarterly shares, not older annual shares", nvda["market_cap"] == 240 * 90)
    check("second company's own shares", intc["market_cap"] == 90 * 20)
    check("reported annual EPS, not quarterly EPS", nvda["annual_diluted_eps"] == [
        {"fiscal_year": "2025", "period": "2025-FY", "period_end": "2025-01-31",
         "diluted_eps": 2.0}])


def test_absent_financial_history_is_explicitly_empty():
    install()
    _, body = call("AMD")
    amd = _row(body, "AMD")
    check("no assumed market cap", amd["market_cap"] is None)
    check("no invented annual EPS", amd["annual_diluted_eps"] == [])


if __name__ == "__main__":
    sys.exit(run_all(globals()))
