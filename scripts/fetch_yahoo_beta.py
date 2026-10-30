"""One-time: Yahoo's beta for the companies worth $1 billion or more, plus named tickers.

Decided 30 Oct 2026. The weekly job saves Yahoo's beta as it reaches each
company (a sixth each morning, Mon-Sat), which takes a week. The owner would
not wait that long for the big names, nor make a request for every company:
"get those names and anything over $1 billion cap" (the names being his and
Randy's Kilby watchlists). Everyone else fills in through the weekly job.

ONE Yahoo request per company (fetchers.fetch_beta), at the scheduled jobs'
pace. Writes `beta`, `beta_source` ("yahoo") and `beta_as_of`. Yahoo's number
only; a company Yahoo has no beta for is left blank. Without --apply it only
lists and counts the companies and makes NO Yahoo request.

    python scripts/fetch_yahoo_beta.py --symbols A,B                    # count only
    python scripts/fetch_yahoo_beta.py --symbols A,B --apply --backup F.json --out F.csv

Stops by itself after 10 failed companies in a row: Yahoo refusing us is not
something to push through.
"""
import argparse
import csv
import json
from pathlib import Path
import random
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import envelope
import fetchers
import identity_storage
import storage
import universe_sync

FIELDS = ("beta", "beta_source", "beta_as_of")
DELAY = 2               # seconds between companies, as the price job
TRIES = 3
STOP_AFTER = 10         # failed companies in a row


def wanted(db, min_cap, named):
    """Active companies with a stored market cap >= min_cap, then named ones not already in."""
    skip = set(universe_sync.skip_list(recheck=False) or [])
    big = []
    for doc in db.collection(storage.COLLECTION_ROOT).where("active", "==", True).stream():
        cap = (doc.to_dict() or {}).get("market_cap")
        if doc.id not in skip and envelope.finite(cap) and cap >= min_cap:
            big.append(doc.id)
    big = sorted(big)
    extra = [s for s in dict.fromkeys(named) if s not in set(big)]
    return big, extra


def fetch(symbol):
    for attempt in range(1, TRIES + 1):
        try:
            return fetchers.fetch_beta(symbol.replace(".", "-")), None
        except Exception as exc:  # noqa: BLE001
            if attempt == TRIES:
                return None, str(exc)[:200]
            time.sleep(DELAY * attempt + random.uniform(1, 5))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-cap", type=float, default=1e9)
    parser.add_argument("--symbols", default="", help="comma-separated tickers to include as well")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.apply and not (args.backup and args.out):
        parser.error("--apply needs --backup and --out")
    if args.backup and args.backup.exists():
        parser.error("use a new backup path")

    db = storage.get_db()
    named = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    big, extra = wanted(db, args.min_cap, named)
    symbols = big + extra
    print(f"{len(big)} companies worth ${args.min_cap / 1e9:g}B or more, plus {len(extra)} named "
          f"and not already in: {len(symbols)} Yahoo requests")
    if not args.apply:
        print("count only: no Yahoo request made")
        return

    before = {}
    for s in symbols:
        meta = storage.get_ticker_meta(s) or {}
        before[s] = {k: meta.get(k) for k in FIELDS}
    with args.backup.open("x", encoding="utf-8") as handle:
        json.dump(before, handle, indent=1, default=str, sort_keys=True)

    rows, saved, blank, failed, in_a_row = [], {}, 0, 0, 0
    for i, symbol in enumerate(symbols, 1):
        fields, error = fetch(symbol)
        if fields is None:
            failed += 1
            in_a_row += 1
            rows.append([symbol, "failed", None, error])
            if in_a_row >= STOP_AFTER:
                print(f"STOPPED after {STOP_AFTER} failures in a row, at {i} of {len(symbols)}: {error}")
                break
        else:
            in_a_row = 0
            if fields:
                if not identity_storage.guarded_write(db, symbol, [("", fields)], merge=True):
                    db.collection(storage.COLLECTION_ROOT).document(symbol).update(fields)
                saved[symbol] = fields["beta"]
                rows.append([symbol, "saved", fields["beta"], None])
            else:
                blank += 1
                rows.append([symbol, "Yahoo has no beta", None, None])
        if i % 250 == 0:
            print(f"[{i}/{len(symbols)}] saved {len(saved)}, no beta {blank}, failed {failed}", flush=True)
        time.sleep(DELAY)

    with args.out.open("w", newline="", encoding="utf-8") as handle:
        out = csv.writer(handle)
        out.writerow(["Ticker", "Result", "Yahoo beta", "Error"])
        out.writerows(rows)

    # Read back what was saved: every stored beta must equal what Yahoo sent.
    wrong = [s for s, b in saved.items() if (storage.get_ticker_meta(s) or {}).get("beta") != b]
    print(f"saved {len(saved)}; Yahoo has no beta {blank}; failed {failed}; of {len(symbols)}. "
          f"Read back: {len(saved) - len(wrong)} of {len(saved)} match" + (f"; MISMATCH {wrong[:20]}" if wrong else ""))


if __name__ == "__main__":
    main()
