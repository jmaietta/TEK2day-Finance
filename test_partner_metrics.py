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
import json
import sys
from copy import deepcopy

from testkit import check, run_all

import beta
import partner_api
import storage
import watchlist_metrics


REAL_FOR_SYMBOLS = beta.for_symbols   # conftest blanks it for each test


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
    beta.for_symbol = lambda s, meta=None: dict.fromkeys(beta.FIELDS)
    beta.for_symbols = lambda symbols, metas: {s: dict.fromkeys(beta.FIELDS) for s in symbols}
    beta.industry_standin = lambda industry, window_end=None: {
        "industry_beta_median": None, "industry_beta_peers": 0, "industry_beta_industry": industry or None,
        "industry_beta_reason": None}


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




# ── beta (30 Oct 2026, beta.py) ──────────────────────────────────────────────

def test_beta_is_ours_with_the_fit_it_came_from():
    install()
    fits = {"NVDA": {"beta": 1.83, "r_squared": 0.41, "weeks_used": 104, "years": 2.0, "reason": None,
                     "window_start": "2024-10-25", "window_end": "2026-10-23"},
            "AMD": {**dict.fromkeys(beta.FIELDS), "weeks_used": 60, "reason": "short_history"}}
    beta.for_symbols = lambda symbols, metas: {s: dict(fits.get(s) or dict.fromkeys(beta.FIELDS)) for s in symbols}
    _, body = call("NVDA,AMD")
    nvda, amd = _row(body, "NVDA"), _row(body, "AMD")
    check("NVDA", (nvda["beta"], nvda["beta_r_squared"], nvda["beta_weeks_used"], nvda["beta_window_end"])
          == (1.83, 0.41, 104, "2026-10-23"), str(nvda))
    check("ours", (nvda["beta_source"], nvda["beta_method"], nvda["beta_years"])
          == ("tek2day", "weekly_vs_spy_up_to_2y", 2.0), str(nvda))
    check("AMD blank with why", (amd["beta"], amd["beta_reason"]) == (None, "short_history"), str(amd))
    check("no Yahoo date", "beta_as_of" not in nvda)
    for key in ("beta", "beta_r_squared", "beta_weeks_used", "beta_years", "beta_reason"):
        check(f"{key} defined", key in body["data"]["definitions"])


def _held_semis():
    held = {**UNIVERSE, "NVDA": {**UNIVERSE["NVDA"], "industry": "Semiconductors", "quote_type": "EQUITY"},
            "AMD": {**UNIVERSE["AMD"], "industry": "Semiconductors", "quote_type": "EQUITY"}}
    storage.get_ticker_meta = lambda s: held.get(s)
    beta.for_symbols = lambda symbols, metas: {
        s: {**dict.fromkeys(beta.FIELDS), "beta": 1.8 if s == "NVDA" else None} for s in symbols}


def _call_with(symbols, include):
    result = partner_api.equity_metrics(Req(), symbols=symbols, include=include)
    return json.loads(result.body) if hasattr(result, "body") else result


def test_a_stock_without_a_beta_gets_its_industrys_when_asked():
    install()
    _held_semis()
    asked = []

    def standin(industry, window_end=None):
        asked.append(industry)
        return {"industry_beta_median": 1.6, "industry_beta_peers": 41, "industry_beta_industry": industry,
                "industry_beta_reason": None}
    beta.industry_standin = standin
    body = _call_with("NVDA,AMD,INTC", "industry_beta")
    nvda, amd, intc = _row(body, "NVDA"), _row(body, "AMD"), _row(body, "INTC")
    check("own beta: no stand-in", (nvda["industry_beta_median"], nvda["industry_beta_peers"]) == (None, None), str(nvda))
    check("stand-in", (amd["industry_beta_median"], amd["industry_beta_peers"], amd["industry_beta_industry"])
          == (1.6, 41, "Semiconductors"), str(amd))
    check("no industry: null, 0", (intc["industry_beta_median"], intc["industry_beta_peers"],
                                   intc["industry_beta_industry"]) == (None, 0, None), str(intc))
    check("asked for the stock without a beta only", asked == ["Semiconductors"], str(asked))
    check("type passed through", nvda["quote_type"] == "EQUITY")
    check("no type is null, not a guess", intc["quote_type"] is None, str(intc))
    check("no working field leaks", "_industry" not in amd and "_held" not in amd)
    check("no warning", not any(w["code"] == "beta_unavailable" for w in body["quality"]["warnings"]), str(body["quality"]["warnings"]))
    for key in ("industry_beta_median", "industry_beta_peers", "industry_beta_reason", "quote_type"):
        check(f"{key} defined", key in body["data"]["definitions"])


def test_callers_that_do_not_ask_pay_nothing():
    install()
    _held_semis()

    def standin(industry, window_end=None):
        raise AssertionError("looked up an industry beta nobody asked for")
    beta.industry_standin = standin
    body = _call_with("NVDA,AMD", "")
    amd = _row(body, "AMD")
    check("keys there, null", (amd["industry_beta_median"], amd["industry_beta_peers"],
                               amd["industry_beta_industry"]) == (None, None, None), str(amd))


class _Snap:
    def __init__(self, id, data):
        self.id, self._data, self.exists = id, data, data is not None

    def to_dict(self):
        return dict(self._data)


class _BetaDB:
    """beta_weekly/{symbol} docs; counts batched reads."""
    def __init__(self, docs, broken=False):
        self.docs, self.broken, self.calls = docs, broken, []

    def collection(self, name):
        db = self

        class _Coll:
            def document(self, symbol):
                return (name, symbol)
        return _Coll()

    def get_all(self, refs):
        refs = list(refs)
        self.calls.append(refs)
        if self.broken:
            raise RuntimeError("firestore down")
        return [_Snap(s, self.docs.get(s)) for _, s in refs]


def test_the_set_reads_every_beta_in_one_batched_read(monkeypatch):
    install()
    db = _BetaDB({"NVDA": {"symbol": "NVDA", "beta": 1.83, "years": 2.0}})
    monkeypatch.setattr(storage, "get_db", lambda: db)
    beta.for_symbols = REAL_FOR_SYMBOLS
    _, body = call("NVDA,AMD,NOPE")
    check("one read for the set", len(db.calls) == 1 and [s for _, s in db.calls[0]] == ["NVDA", "AMD"],
          str(db.calls))
    check("stored", _row(body, "NVDA")["beta"] == 1.83, str(_row(body, "NVDA")))
    check("not reached yet", _row(body, "AMD")["beta_reason"] == "not_yet_computed", str(_row(body, "AMD")))
    check("no record: no read, all null", _row(body, "NOPE")["beta_reason"] is None)
    check("no warning", not any(w["code"] == "beta_unavailable" for w in body["quality"]["warnings"]))


def test_a_failed_beta_read_says_unavailable_and_warns_but_the_call_succeeds(monkeypatch):
    install()
    monkeypatch.setattr(storage, "get_db", lambda: _BetaDB({}, broken=True))
    beta.for_symbols = REAL_FOR_SYMBOLS
    status, body = call("NVDA,AMD")
    check("200", status == 200, str(status))
    check("every row unavailable", [(_row(body, s)["beta"], _row(body, s)["beta_reason"]) for s in ("NVDA", "AMD")]
          == [(None, "unavailable")] * 2, str(body["data"]["companies"]))
    check("warned", [w["code"] for w in body["quality"]["warnings"]].count("beta_unavailable") == 1, str(body["quality"]["warnings"]))
    check("rest of the row intact", _row(body, "NVDA")["covered"] is True)


def test_a_doc_filed_under_the_wrong_symbol_is_never_served(monkeypatch):
    install()
    monkeypatch.setattr(storage, "get_db", lambda: _BetaDB({"NVDA": {"symbol": "AMD", "beta": 1.1}}))
    beta.for_symbols = REAL_FOR_SYMBOLS
    _, body = call("NVDA")
    check("unavailable", (_row(body, "NVDA")["beta"], _row(body, "NVDA")["beta_reason"]) == (None, "unavailable"))
    check("warned", any(w["code"] == "beta_unavailable" for w in body["quality"]["warnings"]))


def test_a_row_whose_own_beta_could_not_be_read_gets_no_stand_in(monkeypatch):
    install()
    _held_semis()
    beta.for_symbols = lambda symbols, metas: {s: dict(beta.UNAVAILABLE) for s in symbols}

    def standin(industry, window_end=None):
        raise AssertionError("gave an estimate in place of a beta that may exist")
    beta.industry_standin = standin
    body = _call_with("NVDA,AMD", "industry_beta")
    amd = _row(body, "AMD")
    check("no estimate, says why", (amd["industry_beta_median"], amd["industry_beta_peers"],
                                    amd["industry_beta_reason"]) == (None, None, "unavailable"), str(amd))
    check("warned", any(w["code"] == "beta_unavailable" for w in body["quality"]["warnings"]))


def test_unreadable_industry_medians_warn_too():
    install()
    _held_semis()
    beta.industry_standin = lambda industry, window_end=None: {
        "industry_beta_median": None, "industry_beta_peers": None, "industry_beta_industry": industry,
        "industry_beta_reason": "unavailable"}
    body = _call_with("NVDA,AMD", "industry_beta")
    check("reason", _row(body, "AMD")["industry_beta_reason"] == "unavailable")
    check("warned", any(w["code"] == "beta_unavailable" for w in body["quality"]["warnings"]), str(body["quality"]["warnings"]))


if __name__ == "__main__":
    sys.exit(run_all(globals()))
