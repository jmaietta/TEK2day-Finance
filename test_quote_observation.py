"""Offline quote -> snapshot -> partner-envelope provenance regressions.

Run separately from the legacy script suites, which mutate module globals.
No provider, Firestore, credential lookup or paid model call is permitted.
"""
import json
import socket
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

import envelope
import fetchers
import partner_api
import storage
import terminal

EPOCH = 1787169600
OBSERVED = "2026-08-19T20:00:00+00:00"
META = {"regularMarketPrice": 25, "previousClose": 24,
        "regularMarketTime": EPOCH, "currency": "USD", "gmtoffset": -14400}
RAW_QUOTE = terminal._live_quote.__wrapped__
RAW_INFO = terminal._yahoo.__wrapped__
SNAPSHOT = terminal._market_snapshot


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("External access forbidden in quote metadata tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(requests.sessions.Session, "request", blocked)
    monkeypatch.setattr(storage, "get_db", blocked)
    monkeypatch.setattr(terminal, "_yf", blocked)
    monkeypatch.setattr(terminal, "_yahoo", blocked)
    yield
    assert not attempts, "An external call was attempted, even if its exception was swallowed"


def yahoo(monkeypatch, meta=None, fast=None, info=None, history_error=False):
    calls = []

    class Ticker:
        def history(self, **kwargs):
            calls.append("history")
            if history_error:
                raise RuntimeError("synthetic outage")

        @property
        def history_metadata(self):
            return dict(META if meta is None else meta)

        @property
        def fast_info(self):
            calls.append("fast")
            return dict(fast or {})

        @property
        def info(self):
            calls.append("info")
            return dict(info or {})

    monkeypatch.setattr(terminal, "_yf", lambda: SimpleNamespace(Ticker=lambda s: Ticker()))
    monkeypatch.setattr(terminal, "_yahoo", terminal._ttl_cache(600)(RAW_INFO))
    return calls


@pytest.mark.parametrize("value", [EPOCH, float(EPOCH), str(EPOCH),
    datetime.fromtimestamp(EPOCH, timezone.utc),
    datetime.fromtimestamp(EPOCH, timezone(timedelta(hours=-4))),
    pd.Timestamp(EPOCH, unit="s", tz="UTC"),
    "2026-08-19T16:00:00-04:00", "2026-08-19T20:00:00Z"])
def test_utc_observation_conversion(value):
    assert fetchers.yahoo_observed_at(value) == OBSERVED


@pytest.mark.parametrize("value", [None, True, False, "", "bad", "2026-08-19",
    "2026-08-19T20:00:00", datetime(2026, 8, 19, 20), pd.Timestamp("2026-08-19"),
    pd.NaT, float("nan"), float("inf"), float("-inf"), 0, -1, 10**20, [], {}])
def test_unknown_observation_is_not_invented(value):
    assert fetchers.yahoo_observed_at(value) is None


def test_primary_quote_carries_matching_time_currency_and_local_date(monkeypatch):
    calls = yahoo(monkeypatch, meta={**META, "gmtoffset": 32400})
    quote = RAW_QUOTE("TEST")
    assert quote["price"] == 25
    assert quote["observed_at"] == OBSERVED
    assert quote["currency"] == "USD"
    assert quote["date"] == "2026-08-20"  # offset affects only the chart date
    assert calls == ["history"]


@pytest.mark.parametrize("timestamp", [None, "2026-08-19", "bad", True, pd.NaT])
def test_primary_price_survives_missing_or_bad_time(monkeypatch, timestamp):
    yahoo(monkeypatch, meta={**META, "regularMarketTime": timestamp})
    quote = RAW_QUOTE("TEST")
    assert quote["price"] == 25
    assert quote["observed_at"] is None


@pytest.mark.parametrize("currency", ["USD", "CAD", "EUR", "GBP", "GBp", "ZAc"])
def test_currency_is_from_price_source_without_case_conversion(monkeypatch, currency):
    yahoo(monkeypatch, meta={**META, "currency": currency})
    assert RAW_QUOTE("TEST")["currency"] == currency


@pytest.mark.parametrize("currency", [None, "", "$", "US Dollars", 123, True, "ＵＳＤ"])
def test_unknown_currency_never_defaults_to_usd(monkeypatch, currency):
    yahoo(monkeypatch, meta={**META, "currency": currency})
    assert RAW_QUOTE("TEST")["currency"] is None


def test_fast_price_does_not_borrow_primary_observation(monkeypatch):
    yahoo(monkeypatch, meta={**META, "regularMarketPrice": None},
          fast={"last_price": 26, "currency": "CAD", "regularMarketTime": EPOCH})
    quote = RAW_QUOTE("TEST")
    assert (quote["price"], quote["currency"], quote["observed_at"], quote["date"]) == (
        26, "CAD", None, None)


def test_previous_close_fallback_does_not_replace_primary_provenance(monkeypatch):
    yahoo(monkeypatch, meta={**META, "previousClose": None},
          fast={"last_price": 99, "previous_close": 23, "currency": "CAD"})
    quote = RAW_QUOTE("TEST")
    assert (quote["price"], quote["currency"], quote["observed_at"]) == (25, "USD", OBSERVED)
    assert quote["previous_close"] == 23


def test_info_regular_price_uses_its_own_time_and_currency(monkeypatch):
    yahoo(monkeypatch, meta={**META, "regularMarketPrice": None},
          info={"regularMarketPrice": 27, "regularMarketTime": EPOCH - 3600, "currency": "EUR"})
    quote = RAW_QUOTE("TEST")
    assert (quote["price"], quote["currency"], quote["observed_at"]) == (
        27, "EUR", "2026-08-19T19:00:00+00:00")


def test_info_current_price_cannot_claim_regular_market_time(monkeypatch):
    yahoo(monkeypatch, meta={}, info={"currentPrice": 27,
          "regularMarketTime": EPOCH, "currency": "USD"})
    quote = RAW_QUOTE("TEST")
    assert quote["price"] == 27
    assert quote["observed_at"] is None


def test_info_previous_close_fallback_preserves_unstamped_fast_price(monkeypatch):
    yahoo(monkeypatch, meta={}, fast={"last_price": 26, "currency": "CAD"},
          info={**META, "regularMarketPrice": 99, "regularMarketPreviousClose": 24})
    quote = RAW_QUOTE("TEST")
    assert (quote["price"], quote["currency"], quote["observed_at"]) == (26, "CAD", None)
    assert quote["previous_close"] == 24


def test_provider_outage_does_not_manufacture_provenance(monkeypatch):
    yahoo(monkeypatch, history_error=True)
    quote = RAW_QUOTE("TEST")
    assert quote["price"] is None
    assert quote["currency"] is None
    assert quote["observed_at"] is None


def test_quote_cache_preserves_time_then_refetches_after_expiry(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(terminal.time, "monotonic", lambda: clock[0])
    calls = yahoo(monkeypatch)
    cached = terminal._ttl_cache(30, should_cache=lambda q: q["price"] is not None)(RAW_QUOTE)
    first = cached("TEST")
    clock[0] = 29
    assert cached("TEST") == first
    assert calls == ["history"]
    clock[0] = 31
    assert cached("TEST")["observed_at"] == OBSERVED
    assert calls == ["history", "history"]


def test_info_cache_retains_old_observation_across_quote_cache_expiry(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(terminal.time, "monotonic", lambda: clock[0])
    calls = yahoo(monkeypatch, meta={}, info={**META, "regularMarketPreviousClose": 24})
    cached = terminal._ttl_cache(30)(RAW_QUOTE)
    assert cached("TEST")["observed_at"] == OBSERVED
    clock[0] = 31
    assert cached("TEST")["observed_at"] == OBSERVED
    assert calls.count("info") == 1
    clock[0] = 601
    assert cached("TEST")["observed_at"] == OBSERVED
    assert calls.count("info") == 2


def summary(monkeypatch):
    """Real snapshot calculation and real partner endpoint; only I/O is stubbed."""
    monkeypatch.setattr(terminal, "_live_quote", terminal._ttl_cache(30)(RAW_QUOTE))
    monkeypatch.setattr(terminal, "_market_snapshot", SNAPSHOT)
    meta = {"symbol": "TEST", "name": "Synthetic Company", "active": True}
    monkeypatch.setattr(storage, "get_ticker_meta", lambda s: dict(meta))
    monkeypatch.setattr(terminal, "_firestore_meta", lambda s: dict(meta))
    monkeypatch.setattr(terminal, "_firestore_fundamentals", lambda s, m: {
        "shares": 10, "net_income": 5, "balance_sheet_as_of": "2026-06-30",
        "ttm_as_of": "2026-06-30", "debt": 30, "cash": 10})
    monkeypatch.setattr(terminal, "_latest_forward_eps", lambda s: 1)
    monkeypatch.setattr(partner_api, "require_kilby", lambda r: "offline-test")
    monkeypatch.setattr(partner_api, "_estimates", lambda s: None)

    def call():
        body = partner_api.equity_summary(SimpleNamespace(headers={}), "TEST")
        return json.loads(json.dumps(body, allow_nan=False))

    return call


def test_real_summary_preserves_formula_periods_and_provenance(monkeypatch):
    calls = yahoo(monkeypatch)
    call = summary(monkeypatch)
    body = call()
    data = body["data"]
    assert body["api_version"] == "1.0.0"
    assert body["units"] == {"currency": "USD", "scale": "units"}
    assert body["quality"] == {"status": "ok", "warnings": []}
    assert data["quote"]["observed_at"] == OBSERVED
    assert data["quote"]["currency"] == "USD"
    assert data["valuation"]["market_cap"] == 250
    assert data["valuation"]["enterprise_value"] == 270
    assert data["fundamentals"]["diluted_shares"] == 10
    assert data["definitions"]["market_cap"] == "Live price x diluted average shares, computed at request time"
    assert data["periods"]["ttm_as_of"] == "2026-06-30"
    assert data["display"]["valuation"]["market_cap"] == "$250.00"
    assert "observed_at" not in data["display"]["quote"]
    monkeypatch.setattr(envelope, "now_iso", lambda: "2030-01-01T00:00:00+00:00")
    later = call()
    assert later["retrieved_at"] == "2030-01-01T00:00:00+00:00"
    assert later["data"]["quote"]["observed_at"] == OBSERVED
    assert calls == ["history"]


@pytest.mark.parametrize("currency", ["CAD", "EUR", "GBp", None])
def test_summary_does_not_relabel_other_or_unknown_units_usd(monkeypatch, currency):
    yahoo(monkeypatch, meta={**META, "currency": currency})
    body = summary(monkeypatch)()
    assert body["units"]["currency"] == currency
    assert body["data"]["quote"]["currency"] == currency
    assert body["data"]["valuation"]["market_cap"] == 250  # no conversion
    assert body["quality"]["status"] == "warning"


def test_old_snapshot_emits_nulls_and_warnings_not_response_time(monkeypatch):
    yahoo(monkeypatch)
    call = summary(monkeypatch)
    monkeypatch.setattr(terminal, "_market_snapshot", lambda s: {
        "symbol": "TEST", "price": 25, "shares": 10, "market_cap": 250})
    body = call()
    assert body["data"]["quote"]["observed_at"] is None
    assert body["data"]["quote"]["currency"] is None
    assert body["units"]["currency"] is None
    assert {w["code"] for w in body["quality"]["warnings"]} == {
        "quote_observation_unavailable", "quote_currency_unavailable"}


def test_summary_fast_fallback_retains_price_but_warns_about_time(monkeypatch):
    yahoo(monkeypatch, meta={}, fast={"last_price": 26, "currency": "USD", "previous_close": 24})
    body = summary(monkeypatch)()
    assert body["data"]["valuation"]["market_cap"] == 260
    assert body["data"]["quote"]["observed_at"] is None
    assert body["quality"]["warnings"][0]["code"] == "quote_observation_unavailable"
