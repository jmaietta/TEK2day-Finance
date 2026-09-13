"""Read-only first-party BK/BNY checks against verified native financial data.

Never calls the partner API. Responses and independent quote evidence stay private.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sys
from unittest.mock import patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_sec_repair import read, save
from security_identity import digest, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--native", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--price-plan", type=Path, help="Optional approved price plan adds expected new bars; financial baseline remains independently verified")
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]),
            "Use a new private receipt path")
    native = read(args.native)
    require(native["verification"]["exact_two_payloads"], "Native verification required")
    applied = native["verification"]["server_commit_time"]
    require((datetime.now(timezone.utc) - applied).total_seconds() > 300, "Wait for existing financial cache TTL")
    financials = [r["data"] for p, r in native["financial_tree"]["records"].items()
                  if p.split('/')[-2] == "financials" and r["exists"]]
    import app
    import terminal
    sections = {"inc": ("income", terminal.INCOME_FIELDS, "Income Statement"),
                "bal": ("balance_sheet", terminal.BALANCE_FIELDS, "Balance Sheet"),
                "cf": ("cash_flow", terminal.CASHFLOW_FIELDS, "Cash Flow")}
    with patch.object(terminal, "_all_financials", return_value=financials):
        expected = {kind: app._financial_payload("BNY", *spec) for kind, spec in sections.items()}
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "responses": {}, "checks": []}
    session = requests.Session()
    base = "https://finance.tek2dayholdings.com"
    def fetch(key, path, command=None):
        require(path == "/api/command" or path in {"/api/ticker/BK", "/api/ticker/BNY", "/api/prices/BNY", "/api/estimates/BNY"},
                "First-party path only")
        response = (session.post(base + path, json={"command": command, "width": 120}, timeout=60) if command else
                    session.get(base + path, params={"limit": 2000} if "/prices/" in path else None, timeout=60))
        body = response.json()
        result["responses"][key] = {"status": response.status_code, "body": body,
                                    "sha256": hashlib.sha256(response.content).hexdigest(),
                                    "retrieved_at": datetime.now(timezone.utc).isoformat()}
        require(response.status_code == 200, key + ": HTTP failure")
        return body
    try:
        for symbol in ("BK", "BNY"):
            for kind in sections:
                body = fetch(symbol + ":" + kind, "/api/command", "/" + symbol + " " + kind)
                require(body.get("symbol") == "BNY" and body.get("kind") == kind, "Wrong canonical response")
                require(digest(body.get("data")) == digest(expected[kind]), "Serving financial view differs from verified storage: " + symbol + ":" + kind)
                require(any("2026-06-30" in s["periods"] for s in body["data"]["sections"]), "June absent from view")
                require("SEC EDGAR" in body["data"]["source"], "Missing SEC attribution")
                result["checks"].append(symbol + ":" + kind + ":exact_verified_financials")
            meta = fetch(symbol + ":metadata", "/api/ticker/" + symbol)
            expected_meta = next(r["data"] for p, r in native["protected_metadata"].items() if p.startswith("security_data/"))
            require(digest(meta) == digest(expected_meta), "Website metadata mismatch")
            result["checks"].append(symbol + ":metadata:unchanged")
        estimates = fetch("estimates", "/api/estimates/BNY")
        native_estimates = [d["data"] for d in native["datasets"]["estimates"].values()]
        require(sorted(map(digest, estimates)) == sorted(map(digest, native_estimates)), "Estimate observations disconnected")
        result["checks"].append("estimates:all_observations_retained")
        prices = fetch("prices", "/api/prices/BNY")
        stored = sorted([d["data"] for d in native["datasets"]["prices"].values()], key=lambda d: d["date"])
        if args.price_plan:
            from price_repair import validate
            plan = read(args.price_plan)
            validate(plan)
            require(plan['symbol'] == 'BNY', 'BNY price expectation required')
            require(not ({r['date'] for r in stored} & {w['data']['date'] for w in plan['writes']}), 'Price expectation overwrites existing observation')
            stored = sorted(stored + [w['data'] for w in plan['writes']], key=lambda d: d['date'])
            result['expected_price_plan_sha256'] = digest(plan)
        earlier = {r["time"]: r for r in prices if r["time"] < stored[-1]["date"]}
        for row in stored[:-1]:
            require(row["date"] in earlier and all(earlier[row["date"]].get(k) == row.get(k)
                    for k in ("open", "high", "low", "close", "volume")), "Historical chart values disconnected")
        result["checks"].append("prices:stored_history_connected")
        quote = terminal._live_quote("BNY")  # Free Yahoo observation, no Firestore writes.
        result["independent_quote"] = {**quote, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                       "scope": "Yahoo provider check, not a live partner response"}
        require(quote.get("price") is not None and quote.get("observed_at") and quote.get("currency") == "USD",
                "Quote observation/currency unavailable")
        latest = [r for r in prices if r["time"] == quote["date"]]
        require(len(latest) == 1 and abs(latest[0]["close"] - quote["price"]) < 0.001, "Chart/current quote mismatch")
        result["checks"].append("quote:matched_price_with_separate_observation_time")
        repeat = fetch("BNY:inc:repeat", "/api/command", "/BNY inc")
        require(repeat["data"] == expected["inc"], "Repeated financial read differs")
        result["checks"].append("financial_repeat:stable")
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
    finally:
        save(args.output, result)
    print({"checks": result["checks"], "completed_at": result["completed_at"],
           "quote": result["independent_quote"], "private_receipt_sha256": digest(result)})


if __name__ == "__main__":
    main()
