#!/usr/bin/env python3
"""Tests for /partner/v1/equities/prices (daily closes and volumes for Kilby).

    pytest test_partner_prices.py

No network, no Firestore.

Spec agreed with Kilby on 30 Oct 2026 (chatllm docs/PRICE_STATISTICS_PLAN.md):
every requested symbol back in order, compact lists, days never filled in,
SPY's dates as the trading calendar, a cache that never freezes a half-written
run, and a flag for a split since the last 5-year re-adjust.
"""
import json
from datetime import datetime, timezone

from testkit import check

import fetchers
import partner_api
import storage


class Req:
    headers: dict = {}


def _rows(symbol, dates, volume=1000):
    return [{"symbol": symbol, "date": d, "close": 100.0 + i, "volume": volume} for i, d in enumerate(dates)]


DAYS = ["2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
PRICES = {
    "SPY": _rows("SPY", DAYS),
    "KO": _rows("KO", DAYS),
    # PEP has no close on the 27th: the gap must stay a gap.
    "PEP": _rows("PEP", [d for d in DAYS if d != "2026-10-27"], volume=None),
}
SPLITS = {"KO": {"events": {"2026-10-28": 2.0}, "checked_at": "2026-10-22"},
          "PEP": {"events": {"2024-01-05": 2.0}, "checked_at": "2026-10-22"}}


class _Snap:
    def __init__(self, data):
        self.data = data
        self.exists = data is not None

    def to_dict(self):
        return dict(self.data or {})


class _DB:
    def collection(self, name):
        assert name == "splits", name
        return self

    def document(self, symbol):
        return type("Ref", (), {"get": lambda s: _Snap(SPLITS.get(symbol))})()


def install(monkeypatch, reads=None):
    partner_api._PRICE_CACHE.clear()

    def history(symbol, limit=1260):
        if reads is not None:
            reads.append(symbol)
        return PRICES.get(symbol, [])[-limit:]

    def no_yahoo(*a, **k):
        raise AssertionError("the price feed asked Yahoo")

    monkeypatch.setattr(storage, "get_prices_history", history)
    monkeypatch.setattr(storage, "get_db", lambda: _DB())
    monkeypatch.setattr(fetchers, "fetch_prices", no_yahoo)
    monkeypatch.setattr(partner_api, "require_kilby", lambda r: "test")
    # Outside the run window unless a test says otherwise.
    monkeypatch.setattr(partner_api, "_price_window",
                        lambda now=None: (datetime(2026, 10, 30, 20, 30, tzinfo=timezone.utc), False))


def call(symbols, days=1260):
    result = partner_api.equity_prices(Req(), symbols=symbols, days=days)
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


def _row(body, symbol):
    return next(c for c in body["data"]["companies"] if c["symbol"] == symbol)


def test_every_symbol_comes_back_in_the_order_asked(monkeypatch):
    install(monkeypatch)
    _, body = call("PEP,NOPE,KO,bad!")
    got = [c["symbol"] for c in body["data"]["companies"]]
    check("order", got == ["PEP", "NOPE", "KO", "BAD!"], str(got))
    check("not held", _row(body, "NOPE")["covered"] is False and _row(body, "NOPE")["dates"] == [])
    check("listed", body["data"]["not_covered"] == ["NOPE", "BAD!"], str(body["data"]["not_covered"]))


def test_compact_lists_and_a_missing_day_is_left_out(monkeypatch):
    install(monkeypatch)
    _, body = call("KO,PEP")
    ko, pep = _row(body, "KO"), _row(body, "PEP")
    check("KO dates", ko["dates"] == DAYS, str(ko["dates"]))
    check("KO closes", ko["closes"] == [100.0, 101.0, 102.0, 103.0, 104.0], str(ko["closes"]))
    check("KO volumes", ko["volumes"] == [1000] * 5, str(ko["volumes"]))
    check("PEP gap not filled", "2026-10-27" not in pep["dates"] and len(pep["closes"]) == 4, str(pep))
    check("null volume stays null", pep["volumes"] == [None] * 4, str(pep["volumes"]))
    check("latest close date", ko["latest_close_date"] == "2026-10-29")


def test_spys_dates_are_the_trading_calendar(monkeypatch):
    install(monkeypatch)
    _, body = call("PEP", days=3)
    check("calendar", body["data"]["trading_days"] == DAYS[-3:], str(body["data"]["trading_days"]))
    check("SPY is not a row", [c["symbol"] for c in body["data"]["companies"]] == ["PEP"])


def test_days_limits_the_window(monkeypatch):
    install(monkeypatch)
    _, body = call("KO", days=2)
    check("two days", _row(body, "KO")["dates"] == DAYS[-2:], str(_row(body, "KO")["dates"]))


def test_limits_are_refused_clearly(monkeypatch):
    install(monkeypatch)
    status, body = call(",".join(f"S{i}" for i in range(251)))
    check("251 symbols", status == 400 and body["error"] == "too_many_symbols", str(body))
    status, body = call("KO", days=1261)
    check("1,261 days", status == 400 and body["error"] == "too_many_days", str(body))


def test_a_split_since_the_last_refresh_is_flagged(monkeypatch):
    """KO split on the 28th, after Thursday's re-adjust: older closes are on the
    old basis until the next one. PEP's split was long ago."""
    install(monkeypatch)
    _, body = call("KO,PEP")
    check("KO flagged", _row(body, "KO")["split_since_refresh"] == "2026-10-28")
    check("PEP not", _row(body, "PEP")["split_since_refresh"] is None)


def test_a_repeat_request_reads_nothing(monkeypatch):
    reads = []
    install(monkeypatch, reads)
    call("KO,PEP")
    first = len(reads)
    call("PEP,KO")
    check("cached", len(reads) == first, f"{first} then {len(reads)}")
    call("KO", days=3)
    check("a shorter window is served from the copy", len(reads) == first, str(reads))


def test_the_envelope_says_stored_and_adjusted(monkeypatch):
    install(monkeypatch)
    _, body = call("KO")
    check("stored", body["provenance"]["valuation_basis"] == "stored")
    check("adjusted", body["adjustment_basis"] == "split_and_dividend_adjusted", body.get("adjustment_basis"))
    check("as of", body["as_of"] == "2026-10-29")


# ── when a copy stops being good ─────────────────────────────────────────────

def _et(y, mo, d, h, mi):
    from zoneinfo import ZoneInfo
    return datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/New_York"))


def test_a_copy_from_the_morning_lasts_until_4_30_pm():
    expires, in_run = partner_api._price_window(_et(2026, 10, 29, 10, 0))
    check("4:30 PM ET", expires == _et(2026, 10, 29, 16, 30) and not in_run, str(expires))


def test_a_copy_read_during_the_run_lasts_five_minutes():
    expires, in_run = partner_api._price_window(_et(2026, 10, 29, 16, 35))
    check("5 minutes", expires == _et(2026, 10, 29, 16, 40) and in_run, str(expires))


def test_friday_evening_lasts_until_monday():
    expires, in_run = partner_api._price_window(_et(2026, 10, 30, 20, 0))
    check("Monday 4:30 PM", expires == _et(2026, 11, 2, 16, 30) and not in_run, str(expires))


def test_saturday_afternoon_is_not_a_run():
    expires, in_run = partner_api._price_window(_et(2026, 10, 31, 17, 0))
    check("not in a run", not in_run and expires == _et(2026, 11, 2, 16, 30), str(expires))
