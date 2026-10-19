#!/usr/bin/env python3
"""Exchange rates, and valuation ratios for companies that file in another currency.

    pytest test_fx_rates.py

No network, no Firestore.

WHY THIS FILE EXISTS — 18-19 October 2026.

TSM's price is in US dollars and its statements in Taiwan dollars. TEK2day
divided one by the other unconverted: P/E 1.0x for a company on about 27x,
price-to-sales 0.5x for about 14x, and the same for 699 companies. The owner's
rulings: rates from Yahoo, one lookup per currency per night ("use yahoo for
fx"); figures shown as filed and labelled ("If the number comes in as TWD, and
it is TWD, then label it TWD"); ratios in US dollars; and Kilby shows the rate
under the P/E ("I like that note. Add it.").
"""
import json
from datetime import date

import pandas as pd
import pytest

from testkit import check

import fetchers
import fx_rates
import partner_api
import terminal


# ── a small stand-in for Firestore ───────────────────────────────────────────

class _Doc:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def get(self):
        data = self.store.get(self.path)
        return type("Snap", (), {"to_dict": lambda s: dict(data) if data else None, "exists": data is not None})()

    def set(self, data):
        self.store[self.path] = dict(data)

    def collection(self, name):
        return _Col(self.store, f"{self.path}/{name}")


class _Col:
    def __init__(self, store, path, where=None):
        self.store, self.path, self._where = store, path, where

    def document(self, doc_id):
        return _Doc(self.store, f"{self.path}/{doc_id}")

    def where(self, field, op, value):
        return _Col(self.store, self.path, (field, value))

    def select(self, fields):
        return self

    def stream(self):
        prefix = self.path + "/"
        for path, data in list(self.store.items()):
            rest = path[len(prefix):] if path.startswith(prefix) else None
            if rest is None or "/" in rest:
                continue
            if self._where and data.get(self._where[0]) != self._where[1]:
                continue
            yield type("Snap", (), {"id": rest, "to_dict": lambda s, d=data: dict(d)})()


class FakeDB:
    def __init__(self, store=None):
        self.store = store or {}

    def collection(self, name):
        return _Col(self.store, name)


def universe(**currencies):
    store = {}
    for symbol, cur in currencies.items():
        store[f"tickers/{symbol}"] = {"active": True, **({"financial_currency": cur} if cur else {})}
    return store


# ── the nightly lookup ───────────────────────────────────────────────────────

class _FxTicker:
    def __init__(self, symbol, closes):
        self.symbol, self.closes = symbol, closes

    def history(self, **kw):
        idx = pd.to_datetime(["2026-10-15", "2026-10-16", "2026-10-19"])
        return pd.DataFrame({"Close": self.closes}, index=idx)


def test_one_lookup_reads_the_currency_per_dollar(monkeypatch):
    asked = []
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: asked.append(s) or _FxTicker(s, [31.9, 31.88, 31.86]))
    quote = fetchers.fetch_fx_rate("TWD")
    check("Yahoo symbol", asked == ["TWD=X"], str(asked))
    check("latest close", quote["rate"] == 31.86 and quote["rate_date"] == "2026-10-19", str(quote))


def test_a_missing_last_close_falls_back_to_the_one_before(monkeypatch):
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _FxTicker(s, [31.9, 31.88, float("nan")]))
    quote = fetchers.fetch_fx_rate("TWD")
    check("previous close", quote["rate"] == 31.88 and quote["rate_date"] == "2026-10-16", str(quote))


def fake_quotes(monkeypatch, rates, calls=None):
    def fetch(currency):
        if calls is not None:
            calls.append(currency)
        rate = rates.get(currency)
        return None if rate is None else {"currency": currency, "rate": rate, "rate_date": "2026-10-19",
                                          "source": f"Yahoo Finance {currency}=X"}
    monkeypatch.setattr(fetchers, "fetch_fx_rate", fetch)


def test_one_lookup_per_filing_currency_and_none_for_dollars(monkeypatch):
    db = FakeDB(universe(TSM="TWD", UMC="TWD", NVO="DKK", AAPL="USD", MSFT=None))
    calls = []
    fake_quotes(monkeypatch, {"TWD": 31.86, "DKK": 6.58}, calls)
    summary = fx_rates.run(db)
    check("each currency once, no USD", calls == ["DKK", "TWD"], str(calls))
    check("stored", db.store["fx_rates/TWD"]["rate"] == 31.86 and summary["stored"] == 2, str(summary))
    check("history kept", db.store["fx_rates/TWD/history/2026-10-19"]["rate"] == 31.86)


def test_a_failed_lookup_keeps_yesterdays_rate(monkeypatch):
    db = FakeDB({**universe(TSM="TWD"), "fx_rates/TWD": {"rate": 31.9, "rate_date": "2026-10-16"}})
    fake_quotes(monkeypatch, {})
    summary = fx_rates.run(db)
    check("kept", db.store["fx_rates/TWD"]["rate"] == 31.9)
    check("reported", summary["failed"] == ["TWD"], str(summary))


def test_an_implausible_overnight_move_is_held_not_stored(monkeypatch):
    db = FakeDB({**universe(TSM="TWD"), "fx_rates/TWD": {"rate": 31.9, "rate_date": "2026-10-16"}})
    fake_quotes(monkeypatch, {"TWD": 0.0319})   # inverted, or a bad print
    summary = fx_rates.run(db)
    check("not stored", db.store["fx_rates/TWD"]["rate"] == 31.9)
    check("held", summary["held"] == ["TWD"], str(summary))


def test_never_more_than_forty_lookups_a_night(monkeypatch):
    codes = [f"{chr(65 + i // 26)}{chr(65 + i % 26)}X" for i in range(45)]
    db = FakeDB(universe(**{f"S{i}": c for i, c in enumerate(codes)}))
    calls = []
    fake_quotes(monkeypatch, {c: 2.0 for c in codes}, calls)
    summary = fx_rates.run(db)
    check("forty", len(calls) == 40, str(len(calls)))
    check("the rest reported", len(summary["skipped_over_limit"]) == 5)


def test_a_rate_is_used_for_five_days_then_not():
    db = FakeDB({"fx_rates/TWD": {"rate": 31.86, "rate_date": "2026-10-19"}})
    check("fresh", fx_rates.latest("TWD", db, date(2026, 10, 24))["rate"] == 31.86)
    check("stale", fx_rates.latest("TWD", db, date(2026, 10, 25)) is None)
    check("none held", fx_rates.latest("DKK", db, date(2026, 10, 20)) is None)
    check("dollars need none", fx_rates.latest("USD", db) is None)


# ── the nightly job ──────────────────────────────────────────────────────────

def test_only_task_zero_of_a_real_night_fetches_rates(monkeypatch):
    import pull_daily_prices as pdp
    runs = []
    monkeypatch.setattr(pdp.storage, "list_active_tickers", lambda: ["AAA", "BBB"])
    monkeypatch.setattr(pdp.fetchers, "fetch_prices", lambda s, period="5d": [{"date": "2026-10-19", "close": 1.0}])
    monkeypatch.setattr(pdp.storage, "write_prices_batch", lambda s, r: None)
    monkeypatch.setattr(pdp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pdp.universe_sync, "run", lambda: None)
    monkeypatch.setattr(pdp.universe_sync, "backfill_pending", lambda: set())
    monkeypatch.setattr(pdp.fx_rates, "run", lambda: runs.append(1))
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", "2")
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "1")
    pdp.main()
    check("task 1 does not", runs == [])
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "0")
    pdp.main()
    check("task 0 does, once", runs == [1])


def test_a_failure_in_the_rates_never_fails_the_price_job(monkeypatch):
    import pull_daily_prices as pdp
    monkeypatch.setattr(pdp.storage, "list_active_tickers", lambda: ["AAA"])
    monkeypatch.setattr(pdp.fetchers, "fetch_prices", lambda s, period="5d": [{"date": "2026-10-19", "close": 1.0}])
    monkeypatch.setattr(pdp.storage, "write_prices_batch", lambda s, r: None)
    monkeypatch.setattr(pdp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pdp.universe_sync, "run", lambda: None)
    monkeypatch.setattr(pdp.universe_sync, "backfill_pending", lambda: set())

    def boom():
        raise RuntimeError("synthetic")
    monkeypatch.setattr(pdp.fx_rates, "run", boom)
    monkeypatch.delenv("CLOUD_RUN_TASK_INDEX", raising=False)
    monkeypatch.delenv("CLOUD_RUN_TASK_COUNT", raising=False)
    check("still succeeds", pdp.main() == 0)


# ── the ratios ───────────────────────────────────────────────────────────────
#
# TSM-shaped, from the stored record and the four lookups of 18 Oct 2026:
# price $412.61, TTM net income TWD 2,216.8B over 5.186B shares, revenue TWD
# 4,436B, next-year EPS estimate $22.30 (Yahoo gives TSM's estimates in USD).

RATE = {"currency": "TWD", "rate": 31.864, "rate_date": "2026-10-19"}
TSM_FUNDAMENTALS = {"shares": 5_186_354_200.0, "revenue": 4_436e9, "ebitda": 3_200e9,
                    "net_income": 2_216_808_522_000.0, "operating_cashflow": 2_400e9,
                    "free_cashflow": 1_100e9, "cash": 2_500e9, "debt": 1_000e9,
                    "balance_sheet_as_of": "2026-06-30", "ttm_as_of": "2026-06-30"}


def install(monkeypatch, filing, fundamentals, forward_eps, forward_currency, rates, price=412.61):
    meta = {"symbol": "TSM", "name": "Taiwan Semiconductor", "active": True}
    if filing:
        meta["financial_currency"] = filing
    monkeypatch.setattr(terminal, "_firestore_meta", lambda s: meta)
    monkeypatch.setattr(terminal, "_live_quote", lambda s: {"price": price, "currency": "USD"})
    monkeypatch.setattr(terminal, "_firestore_fundamentals", lambda s, m: dict(fundamentals))
    monkeypatch.setattr(terminal, "_latest_forward_eps", lambda s: forward_eps)
    monkeypatch.setattr(terminal, "_latest_forward_eps_currency", lambda s: forward_currency)
    monkeypatch.setattr(terminal, "_fx_rate", lambda c: rates.get(c))


def test_tsm_ratios_are_calculated_in_dollars(monkeypatch):
    install(monkeypatch, "TWD", TSM_FUNDAMENTALS, 22.30484, "USD", {"TWD": RATE})
    snap = terminal._market_snapshot("TSM")
    eps_usd = 2_216_808_522_000.0 / 5_186_354_200.0 / 31.864
    check("P/E", abs(snap["pe_ttm"] - 412.61 / eps_usd) < 1e-9 and 30 < snap["pe_ttm"] < 31.5, str(snap["pe_ttm"]))
    check("forward P/E, estimate already in $", abs(snap["forward_pe"] - 412.61 / 22.30484) < 1e-9)
    market_cap = 412.61 * 5_186_354_200.0
    check("P/S", abs(snap["ps_ttm"] - market_cap / (4_436e9 / 31.864)) < 1e-9 and 14 < snap["ps_ttm"] < 16,
          str(snap["ps_ttm"]))
    ev = market_cap + (1_000e9 - 2_500e9) / 31.864
    check("EV in $", abs(snap["enterprise_value"] - ev) < 1)
    check("EV/EBITDA", abs(snap["ev_ebitda"] - ev / (3_200e9 / 31.864)) < 1e-9)
    check("the rate travels with them", snap["fx"] == RATE)
    check("figures still as filed", snap["revenue"] == 4_436e9 and snap["statement_currency"] == "TWD")


def test_eps_estimates_in_local_currency_are_converted_for_forward_pe(monkeypatch):
    """Novo: estimates in DKK."""
    rate = {"currency": "DKK", "rate": 6.583, "rate_date": "2026-10-19"}
    install(monkeypatch, "DKK", TSM_FUNDAMENTALS, 22.88814, "DKK", {"DKK": rate}, price=38.83)
    snap = terminal._market_snapshot("NVO")
    check("forward P/E", abs(snap["forward_pe"] - 38.83 / (22.88814 / 6.583)) < 1e-9, str(snap["forward_pe"]))
    check("forward EPS still as given", snap["forward_eps"] == 22.88814)


def test_without_a_usable_rate_the_ratios_are_blank_never_mixed(monkeypatch):
    install(monkeypatch, "TWD", TSM_FUNDAMENTALS, 22.30484, "USD", {})
    snap = terminal._market_snapshot("TSM")
    for key in ("pe_ttm", "ps_ttm", "enterprise_value", "ev_revenue", "ev_ebitda", "ev_opcf", "ev_fcf"):
        check(f"{key} blank", snap[key] is None, str(snap[key]))
    check("no rate shown", snap["fx"] is None)
    check("market cap unaffected", snap["market_cap"] == 412.61 * 5_186_354_200.0)
    check("a $ estimate still gives forward P/E", snap["forward_pe"] is not None)


def test_us_companies_are_calculated_exactly_as_before(monkeypatch):
    fundamentals = dict(TSM_FUNDAMENTALS, shares=1e9, revenue=1e11, net_income=2e10, debt=5e9, cash=1e10)
    for filing in ("USD", None):
        install(monkeypatch, filing, fundamentals, 25.0, "USD", {})
        snap = terminal._market_snapshot("AAPL")
        mc = 412.61 * 1e9
        check(f"{filing}: P/E", snap["pe_ttm"] == 412.61 / (2e10 / 1e9))
        check(f"{filing}: P/S", snap["ps_ttm"] == mc / 1e11)
        check(f"{filing}: EV", snap["enterprise_value"] == mc + 5e9 - 1e10)
        check(f"{filing}: forward P/E", snap["forward_pe"] == 412.61 / 25.0)
        check(f"{filing}: no rate", snap["fx"] is None)


# ── what Kilby reads ─────────────────────────────────────────────────────────

class Req:
    headers: dict = {}


def summary(monkeypatch, snap):
    import storage
    monkeypatch.setattr(storage, "get_ticker_meta", lambda s: {"symbol": "TSM", "name": "TSMC", "active": True})
    monkeypatch.setattr(terminal, "_market_snapshot", lambda s: dict(snap))
    monkeypatch.setattr(terminal, "_estimate_history", lambda s: [])
    monkeypatch.setattr(partner_api, "require_kilby", lambda r: "test")
    result = partner_api.equity_summary(Req(), symbol="TSM")
    return json.loads(result.body) if hasattr(result, "body") else result


def test_kilby_gets_the_rate_behind_the_ratios(monkeypatch):
    body = summary(monkeypatch, {"symbol": "TSM", "name": "TSMC", "price": 412.61, "pe_ttm": 30.75, "fx": RATE})
    check("valuation.fx", body["data"]["valuation"]["fx"] == RATE, str(body["data"]["valuation"].get("fx")))
    check("P/E display", body["data"]["display"]["valuation"]["pe_ttm"] == "30.8x",
          body["data"]["display"]["valuation"]["pe_ttm"])
    check("defined", "fx" in body["data"]["definitions"])


def test_no_rate_for_us_companies_or_blank_ratios(monkeypatch):
    body = summary(monkeypatch, {"symbol": "TSM", "name": "TSMC", "price": 412.61, "pe_ttm": None, "fx": None})
    check("null", body["data"]["valuation"]["fx"] is None)


# ── the public company page ──────────────────────────────────────────────────

def test_the_public_page_ratios_are_in_dollars_too(monkeypatch):
    import seo_pages
    import storage
    quarters = [{"period": f"2025-Q{i}", "period_end": f"2025-0{3 * i}-30", "freq": "Q",
                 "income": {"Total Revenue": 1_109e9, "Diluted EPS": 106.86}} for i in (1, 2, 3)]
    quarters.append({"period": "2026-Q1", "period_end": "2026-03-31", "freq": "Q",
                     "income": {"Total Revenue": 1_109e9, "Diluted EPS": 106.86}})
    monkeypatch.setattr(storage, "get_ticker_meta", lambda s: {"symbol": "TSM", "name": "TSMC", "active": True,
                                                               "financial_currency": "TWD",
                                                               "shares_outstanding": 5_186_474_013})
    monkeypatch.setattr(storage, "get_prices_history", lambda s, limit=0: [{"date": "2026-10-16", "close": 412.61}])
    monkeypatch.setattr(storage, "get_all_financials", lambda s: quarters)
    monkeypatch.setattr(terminal, "_fx_rate", lambda c: RATE if c == "TWD" else None)
    html = seo_pages._build_page("TSM")
    pe = 412.61 / (4 * 106.86 / 31.864)
    check("P/E in dollars", terminal._ratio(pe) in html, terminal._ratio(pe))
    monkeypatch.setattr(terminal, "_fx_rate", lambda c: None)
    html = seo_pages._build_page("TSM")
    check("no rate, no P/E", "P/E (TTM)" not in html)
