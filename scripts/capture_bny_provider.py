"""Bounded free Yahoo maintenance capture, BNY only; no Firestore/partner API."""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import fetchers
from security_identity import digest, portable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error("Use a new private output path outside the repository")
    result = {"symbol": "BNY", "started_at": datetime.now(timezone.utc).isoformat(), "datasets": {}}
    for name, call in [("metadata", lambda: fetchers.fetch_ticker_info("BNY")),
                       ("quarterly", lambda: fetchers.fetch_financials("BNY")),
                       ("annual", lambda: fetchers.fetch_annual_financials("BNY")),
                       ("estimates", lambda: fetchers.fetch_estimates("BNY")),
                       ("prices", lambda: fetchers.fetch_prices("BNY", period="3mo"))]:
        value = call()
        result["datasets"][name] = {"data": value, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                    "sha256": digest(value)}
        print(name, "records/fields", len(value or []), flush=True)
        time.sleep(2)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(portable(result), handle, sort_keys=True, indent=2, allow_nan=False)
    print("capture_sha256", digest(result))


if __name__ == "__main__":
    main()
