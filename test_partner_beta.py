#!/usr/bin/env python3
"""Tests for /partner/v1/equities/beta: TEK2day's beta alone, for a set.

    pytest test_partner_beta.py

No network, no Firestore. Agreed with Kilby 31 Oct 2026 (owner's go): the
watchlist shows beta when it opens and must not wait for /equities/metrics.
The fields, names and rules are the metrics rows' own; only covered differs
(a company record is held; prices not checked).
"""
import json
import sys

from testkit import check, run_all

import beta
import partner_api
import storage
import test_partner_metrics as metrics_tests

REAL_FOR_SYMBOLS = beta.for_symbols     # conftest blanks these for each test
REAL_STANDIN = beta.industry_standin


class Req:
    headers: dict = {}


class _Snap:
    def __init__(self, path, data):
        self.id, self._data, self.exists = path.split("/")[-1], data, data is not None

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class _Ref:
    def __init__(self, path):
        self.path, self.id = path, path.split("/")[-1]


class _Coll:
    def __init__(self, name):
        self.name = name

    def document(self, symbol):
        return _Ref(f"{self.name}/{symbol}")


class _DB:
    """tickers/{symbol} and beta_weekly/{symbol}; records every batched read."""
    def __init__(self, docs, broken=()):
        self.docs, self.broken, self.batches = docs, set(broken), []

    def collection(self, name):
        return _Coll(name)

    def get_all(self, refs):
        refs = list(refs)
        self.batches.append([r.path for r in refs])
        if any(r.path.split("/")[0] in self.broken for r in refs):
            raise RuntimeError("firestore down")
        return [_Snap(r.path, self.docs.get(r.path)) for r in refs]


def _fit(symbol, **fields):
    return {"symbol": symbol, **dict.fromkeys(beta.FIELDS), **fields, "method": beta.METHOD,
            "week_ending": "2026-10-30", "computed_at": "2026-10-31T07:21:00+00:00"}


DOCS = {
    "tickers/NVDA": {"symbol": "NVDA", "active": True, "industry": "Semiconductors", "quote_type": "EQUITY"},
    "tickers/AMD": {"symbol": "AMD", "active": True, "industry": "Semiconductors", "quote_type": "EQUITY"},
    "tickers/NEWCO": {"symbol": "NEWCO", "active": True},             # a record, no prices yet
    "tickers/OLDCO": {"symbol": "OLDCO", "renamed_to": "NEWCO"},      # an alias
    "tickers/GONE": {"symbol": "GONE", "active": False, "industry": "Semiconductors"},
    "beta_weekly/GONE": {"symbol": "GONE", **dict.fromkeys(beta.FIELDS), "beta": 1.2, "years": 2.0,
                         "window_end": "2026-09-25", "method": beta.METHOD, "week_ending": "2026-09-25"},
    "beta_weekly/NVDA": _fit("NVDA", beta=1.83, r_squared=0.41, weeks_used=104, years=2.0,
                             window_start="2024-11-01", window_end="2026-10-30"),
    "beta_weekly/AMD": _fit("AMD", reason="short_history", weeks_used=40, years=0.8,
                            window_start="2026-01-02", window_end="2026-10-30"),
}


def _install(monkeypatch, docs=DOCS, broken=()):
    db = _DB(dict(docs), broken)
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr(partner_api, "require_kilby", lambda r: "test")
    monkeypatch.setattr(beta, "for_symbols", REAL_FOR_SYMBOLS)

    def never(*a, **k):
        raise AssertionError("the beta call read history it does not need")
    for name in ("get_prices_history", "get_estimate_history", "get_all_financials", "get_ticker_meta"):
        monkeypatch.setattr(storage, name, never)
    return db


def call(symbols, include=""):
    res = partner_api.equity_beta(Req(), symbols=symbols, include=include)
    return res.status_code if hasattr(res, "status_code") else 200, (
        json.loads(res.body) if hasattr(res, "body") else res)


def _row(body, symbol):
    return next(c for c in body["data"]["companies"] if c["symbol"] == symbol)


def _codes(body):
    return [w["code"] for w in body["quality"]["warnings"]]


def test_every_symbol_in_order_from_two_batched_reads_and_nothing_else(monkeypatch):
    db = _install(monkeypatch)
    status, body = call("AMD,nvda,ZZZZ,NVDA,NEWCO")
    check("200", status == 200, str(status))
    check("in order, once each", [c["symbol"] for c in body["data"]["companies"]]
          == ["AMD", "NVDA", "ZZZZ", "NEWCO"], str(body["data"]["companies"]))
    check("two batched reads: records, then betas",
          [sorted({p.split("/")[0] for p in b}) for b in db.batches] == [["tickers"], ["beta_weekly"]], str(db.batches))
    nvda = _row(body, "NVDA")
    check("NVDA", (nvda["covered"], nvda["beta"], nvda["beta_r_squared"], nvda["beta_years"], nvda["beta_window_end"],
                   nvda["beta_source"], nvda["quote_type"])
          == (True, 1.83, 0.41, 2.0, "2026-10-30", "tek2day", "EQUITY"), str(nvda))
    check("AMD blank with why", (_row(body, "AMD")["beta"], _row(body, "AMD")["beta_reason"]) == (None, "short_history"))
    check("a record and no prices is covered here", _row(body, "NEWCO")["covered"] is True
          and _row(body, "NEWCO")["beta_reason"] == "not_yet_computed", str(_row(body, "NEWCO")))
    check("no record: not covered, all null", (_row(body, "ZZZZ")["covered"], _row(body, "ZZZZ")["beta_reason"])
          == (False, None))
    check("not_covered listed and warned", body["data"]["not_covered"] == ["ZZZZ"] and _codes(body) == ["not_covered"],
          str(body["quality"]))
    check("covered defined", "prices not checked" in body["data"]["definitions"]["covered"])


def test_an_alias_record_is_not_covered(monkeypatch):
    _install(monkeypatch)
    _, body = call("OLDCO")
    check("alias", (_row(body, "OLDCO")["covered"], _row(body, "OLDCO")["beta"]) == (False, None), str(body))


def test_an_inactive_company_is_not_covered_and_its_last_beta_never_served(monkeypatch):
    db = _install(monkeypatch)
    _, body = call("GONE,NVDA")
    gone = _row(body, "GONE")
    check("not covered, no beta", (gone["covered"], gone["beta"], gone["beta_window_end"]) == (False, None, None),
          str(gone))
    check("listed and warned", body["data"]["not_covered"] == ["GONE"] and "not_covered" in _codes(body))
    check("its beta doc not even read", all("beta_weekly/GONE" not in b for b in db.batches), str(db.batches))


def test_a_routed_listing_is_read_as_metrics_reads_it(monkeypatch):
    db = _install(monkeypatch, docs={**DOCS, "beta_weekly/BNY": _fit("BNY", beta=0.88, years=2.0)})
    monkeypatch.setattr(partner_api, "_routed", lambda s: s == "BNY")
    monkeypatch.setattr(storage, "get_ticker_meta",
                        lambda s: {"symbol": "BNY", "active": True, "industry": "Banks - Diversified"})
    _, body = call("BNY,NVDA")
    check("BNY through its route", (_row(body, "BNY")["covered"], _row(body, "BNY")["beta"]) == (True, 0.88),
          str(_row(body, "BNY")))
    check("BNY not in the plain record read", all("tickers/BNY" not in b for b in db.batches), str(db.batches))


def test_unreadable_records_say_unavailable_not_uncovered(monkeypatch):
    _install(monkeypatch, broken={"tickers"})
    status, body = call("NVDA,AMD", "industry_beta")
    check("200", status == 200)
    check("covered null, unavailable", [(_row(body, s)["covered"], _row(body, s)["beta_reason"],
                                         _row(body, s)["industry_beta_reason"]) for s in ("NVDA", "AMD")]
          == [(None, "unavailable", "unavailable")] * 2, str(body["data"]["companies"]))
    check("volatility unavailable too", [_row(body, s)["volatility_1y_reason"] for s in ("NVDA", "AMD")]
          == ["unavailable"] * 2)
    check("warned once each, nothing listed as not covered",
          _codes(body) == ["beta_unavailable", "volatility_unavailable"] and body["data"]["not_covered"] == [],
          str(body["quality"]))


def test_unreadable_betas_say_unavailable(monkeypatch):
    _install(monkeypatch, broken={"beta_weekly"})
    _, body = call("NVDA,ZZZZ")
    check("covered, unavailable", (_row(body, "NVDA")["covered"], _row(body, "NVDA")["beta_reason"])
          == (True, "unavailable"))
    check("both warnings", sorted(_codes(body)) == ["beta_unavailable", "not_covered"], str(_codes(body)))


def test_the_industry_stand_in_by_the_metrics_rules(monkeypatch):
    _install(monkeypatch)
    asked = []

    def standin(industry, window_end=None):
        asked.append((industry, window_end))
        return {"industry_beta_median": 1.6, "industry_beta_peers": 41, "industry_beta_industry": industry,
                "industry_beta_reason": None}
    monkeypatch.setattr(beta, "industry_standin", standin)
    _, body = call("NVDA,AMD", "industry_beta")
    check("only for the stock without a beta", asked == [("Semiconductors", "2026-10-30")], str(asked))
    check("AMD stand-in", (_row(body, "AMD")["industry_beta_median"], _row(body, "AMD")["industry_beta_peers"])
          == (1.6, 41))
    _, body = call("NVDA,AMD")
    check("not asked: null", _row(body, "AMD")["industry_beta_median"] is None and len(asked) == 1)


def test_too_many_symbols_is_refused_as_metrics_refuses(monkeypatch):
    _install(monkeypatch)
    many = ",".join(f"S{i}" for i in range(partner_api._MAX_METRICS_SYMBOLS + 1))
    status, body = call(many)
    check("400 too_many_symbols", (status, body["error"]) == (400, "too_many_symbols"), str(body))


def test_the_fields_and_their_meanings_are_the_metrics_rows_own(monkeypatch):
    """Kilby reads both with one parser: same names, same definitions."""
    _install(monkeypatch)              # the fake database for metrics too: no real client
    metrics_tests.install()
    _, metrics = metrics_tests.call("NVDA")
    mrow = metrics_tests._row(metrics, "NVDA")
    shared = {k for k in mrow if k.startswith(("beta", "industry_beta", "volatility_1y")) or k == "quote_type"}
    _install(monkeypatch)
    _, body = call("NVDA")
    row = _row(body, "NVDA")
    check("same beta fields", set(row) - {"symbol", "covered"} == shared, str(set(row) ^ (shared | {"symbol", "covered"})))
    mdefs, defs = metrics["data"]["definitions"], body["data"]["definitions"]
    check("same definitions", all(defs[k] == mdefs[k] for k in partner_api._BETA_DEFINITIONS)
          and set(partner_api._BETA_DEFINITIONS) <= set(mdefs), str(set(partner_api._BETA_DEFINITIONS) - set(mdefs)))


if __name__ == "__main__":
    sys.exit(run_all(globals()))
