"""One-time: a stand-in beta for every company, from our stored closes, until Yahoo's arrives.

The weekly pull keeps Yahoo's beta as it reaches each company, which takes a
week (one sixth of the companies each morning, Mon-Sat). This fills the gap
the same way Yahoo works it out (5 years of monthly moves), against SPY. No
Yahoo call is made. See beta.py for why it is close to Yahoo's but not equal.

Reads SPY's dates once, then one price document per month end per company
(about 60), not five years of days. Writes `beta`, `beta_source` and
`beta_as_of`, and NEVER over a beta Yahoo has given. Without --apply nothing
is written.

    python scripts/seed_standin_beta.py                         # dry run
    python scripts/seed_standin_beta.py --apply --backup F.json # write

--backup FILE saves each company's beta fields, as they stand, before anything
is written (a new file only). --only KO,NVDA limits the run to those tickers.
--out FILE writes every company's result as CSV.
"""
import argparse
import csv
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import beta
import identity_storage
import storage

FIELDS = ("beta", "beta_source", "beta_as_of")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--only", help="comma-separated tickers")
    parser.add_argument("--out", type=Path, help="every company's result, as CSV")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    if args.backup and args.backup.exists():
        parser.error("use a new backup path")
    if args.apply and not args.backup:
        parser.error("--apply needs --backup")

    db = storage.get_db()
    spy = {r["date"][:10]: float(r["close"]) for r in storage.get_prices_history(beta.BENCHMARK, limit=1400)
           if r.get("date") and r.get("close")}
    ends = beta.month_ends(spy)[-(beta.MONTHS + 1):]
    print(f"SPY: {len(spy)} days, month ends {ends[0]} to {ends[-1]} ({len(ends) - 1} monthly moves)")

    symbols = storage.list_active_tickers()
    if args.only:
        only = {t.strip().upper() for t in args.only.split(",") if t.strip()}
        symbols = [s for s in symbols if s in only]

    def one(symbol):
        meta = storage.get_ticker_meta(symbol) or {}
        held = {k: meta.get(k) for k in FIELDS}
        if meta.get("beta_source") == beta.YAHOO_SOURCE:
            return symbol, held, None, "yahoo_held"
        result = beta.monthly_beta(beta.month_end_closes(db, symbol, ends), spy)
        return symbol, held, result, "written" if result else "too_little_history"

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(one, symbols))

    if args.backup:
        with args.backup.open("x", encoding="utf-8") as handle:
            json.dump({s: held for s, held, _, _ in results}, handle, indent=1, default=str, sort_keys=True)
        print(f"saved {len(results)} records to {args.backup}")

    counts = {}
    for symbol, _, result, status in results:
        counts[status] = counts.get(status, 0) + 1
        if status != "written" or not args.apply:
            continue
        fields = {"beta": result["beta"], "beta_source": beta.STANDIN_SOURCE, "beta_as_of": result["window_end"]}
        if not identity_storage.guarded_write(db, symbol, [("", fields)], merge=True):
            db.collection(storage.COLLECTION_ROOT).document(symbol).update(fields)

    if args.out:
        with args.out.open("w", newline="", encoding="utf-8") as handle:
            out = csv.writer(handle)
            out.writerow(["Ticker", "Status", "Beta", "R squared", "Months", "From", "To"])
            for symbol, _, r, status in results:
                r = r or {}
                out.writerow([symbol, status, r.get("beta"), r.get("r_squared"), r.get("months"),
                              r.get("window_start"), r.get("window_end")])

    verb = "wrote" if args.apply else "would write"
    print(f"{verb} {counts.get('written', 0)}; Yahoo's already held {counts.get('yahoo_held', 0)}; "
          f"too little history {counts.get('too_little_history', 0)}; of {len(symbols)} companies")


if __name__ == "__main__":
    main()
