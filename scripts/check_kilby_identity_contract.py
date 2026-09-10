"""Exercise TEK2day producer envelopes against the pinned real Kilby guard offline."""
import argparse
import hashlib
import json
from pathlib import Path
import runpy
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
POLICY_SHA256 = "4fe8485849398dc13972425540cd5e9e99b706bd960bc8b2ed5c6a44c557e137"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-file", required=True, type=Path)
    args = parser.parse_args()
    assert hashlib.sha256(args.policy_file.read_bytes()).hexdigest() == POLICY_SHA256
    guard = runpy.run_path(str(args.policy_file))["response_problem"]
    import socket
    import requests
    import yfinance
    import storage
    import terminal
    import partner_api
    from security_identity import RetiredSymbol
    attempts = []
    def blocked(*a, **k):
        attempts.append(True)
        raise AssertionError("Network forbidden")
    meta = {"symbol": "BNY", "name": "Synthetic fixture issuer", "security_id": "synthetic-only"}
    snap = {"symbol": "BNY", "name": meta["name"], "price": 10,
            "quote_observed_at": "2026-09-09T20:00:00+00:00", "quote_currency": "USD"}
    financials = [{"symbol": "BNY", "period": "2026-Q2", "period_end": "2026-06-30",
                   "income": {"Total Revenue": 100, "Net Income": 10, "Diluted EPS": 1},
                   "balance_sheet": {"Total Assets": 200}, "cash_flow": {"Operating Cash Flow": 12}}]
    estimates = [{"symbol": "BNY", "date": "2026-09-09", "eps_avg": {"0q": 1},
                  "horizons": {"0q": "2026-09-30", "+1q": "2026-12-31"}, "provider_observed_at": None}]
    def metadata(symbol):
        if symbol == "BK":
            raise RetiredSymbol("BK retired; explicitly request BNY")
        return meta
    from contextlib import ExitStack
    with ExitStack() as stack:
        for obj, name, value in [(socket.socket, "connect", blocked), (requests.sessions.Session, "request", blocked),
                                  (yfinance, "Ticker", blocked), (storage, "get_db", blocked),
                                  (storage, "get_ticker_meta", metadata), (terminal, "_market_snapshot", lambda _: snap),
                                  (terminal, "_estimate_history", lambda _: estimates), (terminal, "_all_financials", lambda _: financials),
                                  (partner_api, "require_kilby", lambda _: None)]:
            stack.enter_context(patch.object(obj, name, value))
        for producer in (partner_api.resolve_symbol, partner_api.equity_summary, partner_api.equity_estimates,
                         lambda request, symbol: partner_api.equity_financials(request, symbol, "income", "quarterly")):
            response = producer(None, "BNY")
            body = json.loads(json.dumps(response))
            assert guard(body, "BNY") == ""
            assert guard(body, "BK")
            assert "security_resolution" not in body
            refused = producer(None, "BK")
            assert refused.status_code == 409
            assert json.loads(refused.body).get("data") is None
        assert guard({"data": {"symbol": "BNY", "renamed_to": "OTHER"}}, "BNY")
        assert guard({"security_resolution": {}}, "BNY")
    assert not attempts
    print("PASS: 22 producer/consumer identity checks across resolution, summary, estimates and financials; no network, paid work or live partner requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
