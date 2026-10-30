"""Offline producer/consumer check against an explicitly selected Kilby tree."""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kilby-root", required=True, type=Path)
    args = parser.parse_args()
    upstream = Path(__file__).resolve().parents[1]
    kilby = args.kilby_root.resolve()
    if not (kilby / "kilby_backend" / "report_evidence.py").is_file():
        parser.error("Selected Kilby tree must contain the issue #233 evidence verifier")
    sys.path.insert(0, str(kilby))
    sys.path.insert(0, str(upstream))

    import requests
    import beta
    import storage
    import terminal
    import partner_api
    from kilby_backend.report_evidence import normalize_observation

    attempts = []

    def blocked(*args, **kwargs):
        attempts.append(True)
        raise AssertionError("External access forbidden in producer/consumer check")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
    requests.sessions.Session.request = blocked
    storage.get_db = blocked

    # Deterministic provider clock, independent of the machine's date. A weekday
    # with the market open (Tue 2026-09-08, 11:00 New York): since Kilby's
    # 2026-09-06 change its checker also applies the trading calendar, and the old
    # clock (Sunday 2026-09-06) is rightly refused as a "live" quote (#9).
    now = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
    # The listing details Kilby's checker now requires (market_identity, #9).
    listing = {"symbol": "TEST", "exchange": "NMS", "quoteType": "EQUITY",
               "exchangeTimezoneName": "America/New_York", "currency": "USD"}
    meta = {"regularMarketPrice": 25, "previousClose": 24, "currency": "USD",
            "regularMarketTime": (now - timedelta(minutes=1)).timestamp()}
    ticker = SimpleNamespace(history=lambda **kw: None, history_metadata=meta, fast_info={})
    company = {"symbol": "TEST", "name": "Synthetic Company", "active": True}
    fundamentals = {"shares": 10, "net_income": 5}

    with patch.object(terminal, "_yf", return_value=SimpleNamespace(Ticker=lambda s: ticker)), \
         patch.object(terminal, "_yahoo", return_value={}), \
         patch.object(terminal, "_live_quote", terminal._live_quote.__wrapped__), \
         patch.object(terminal, "_firestore_meta", return_value=company), \
         patch.object(terminal, "_firestore_fundamentals", return_value=fundamentals), \
         patch.object(terminal, "_latest_forward_eps", return_value=None), \
         patch.object(storage, "get_ticker_meta", return_value=company),          patch.object(beta, "for_symbol", return_value=dict.fromkeys(beta.FIELDS)), \
         patch.object(partner_api, "require_kilby", return_value="offline"), \
         patch.object(partner_api, "_estimates", return_value=None):

        def response():
            return json.loads(json.dumps(partner_api.equity_summary(
                SimpleNamespace(headers={}), "TEST"), allow_nan=False))

        valid = normalize_observation(response(), "TEST", fetched_at=now, market_identity=listing)
        assert valid["value"] == "250.0"
        assert valid["currency"] == "USD"
        assert valid["observed_at"] == "2026-09-08T14:59:00+00:00"
        assert valid["freshness"]["mode"] == "current_regular_session"
        print("PASS: actual TEK2day producer -> JSON -> actual Kilby verifier")

        for label, timestamp, currency in [
            ("missing observation", None, "USD"),
            ("stale observation", (now - timedelta(days=2)).timestamp(), "USD"),
            ("future observation", (now + timedelta(hours=1)).timestamp(), "USD"),
            ("missing currency", meta["regularMarketTime"], None),
            ("pence are not pounds", meta["regularMarketTime"], "GBp"),
        ]:
            meta["regularMarketTime"] = timestamp
            meta["currency"] = currency
            try:
                normalize_observation(response(), "TEST", fetched_at=now, market_identity=listing)
            except ValueError:
                print(f"PASS: rejects {label}")
            else:
                raise AssertionError(f"Consumer accepted {label}")

    assert not attempts, "An external call was attempted"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
