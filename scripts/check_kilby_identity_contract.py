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
        # Real public SEC facts, still strictly offline. This is a producer
        # adapter fixture, not a frozen live partner response or API grant.
        from datetime import datetime, timezone
        from sec_fallback import filings_from, build_candidate
        from sec_mapping import BINDINGS
        evidence = json.loads((ROOT / "tests/fixtures/sec_bny_2026.json").read_text())
        extra = json.loads((ROOT / "tests/fixtures/sec_bny_2026_additional.json").read_text())
        evidence['facts']['facts']['us-gaap'].update(extra['us-gaap'])
        filings, _ = filings_from(evidence["submissions"], BINDINGS["BNY"], datetime(2026, 9, 10, 23, tzinfo=timezone.utc))
        recovered = build_candidate(evidence["facts"], evidence["source_capture"], BINDINGS["BNY"], filings[-1], filings)
        financials[:] = [recovered]
        for statement in ("income", "balance_sheet", "cash_flow"):
            body = partner_api.equity_financials(None, "BNY", statement, "quarterly")
            assert guard(json.loads(json.dumps(body)), "BNY") == ""
            assert body["provenance"]["upstream"] == "SEC EDGAR"
            assert body["data"]["filing_sources"][0]["filing"]["reportDate"] == "2026-06-30"
            assert body["completeness"]["source"] == "sec_fallback"
            assert guard(body, "BK")
        # Successor identity remains internal. Kilby's current exact-symbol
        # policy still sees requested/resolved/payload XOM, without redirects.
        from registrant_succession import XOM_SUCCESSION
        meta.clear()
        meta.update(symbol='XOM', name='Synthetic successor adapter fixture',
                    cik=XOM_SUCCESSION['to']['cik'], issuer_id=XOM_SUCCESSION['to']['issuer_id'],
                    security_id=XOM_SUCCESSION['to']['security_id'],
                    reporting_series_id=XOM_SUCCESSION['series_id'])
        snap.update(symbol='XOM', name=meta['name'])
        financials[:] = [{'symbol': 'XOM', 'period': '2026-Q2', 'period_end': '2026-06-30',
                         'income': {'Total Revenue': 100, 'Net Income': 0},
                         'balance_sheet': {'Total Assets': 200}, 'cash_flow': {'Operating Cash Flow': 12}}]
        estimates[:] = [{'symbol': 'XOM', 'date': '2026-09-09', 'eps_avg': {'0q': 1},
                         'horizons': {'0q': '2026-09-30', '+1q': '2026-12-31'}}]
        for producer in (partner_api.resolve_symbol, partner_api.equity_summary, partner_api.equity_estimates,
                         lambda request, symbol: partner_api.equity_financials(request, symbol, 'income', 'quarterly')):
            body = json.loads(json.dumps(producer(None, 'XOM')))
            assert guard(body, 'XOM') == ''
            assert guard(body, 'BNY')
            assert body['data']['symbol'] == 'XOM'
            assert 'security_resolution' not in body
    assert not attempts
    print("PASS: 22 rename, 15 SEC and 16 XOM successor producer/consumer assertions; no network, paid work or live partner requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
