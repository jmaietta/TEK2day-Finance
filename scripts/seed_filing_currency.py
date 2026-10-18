"""One-time: record each company's filing currency from SEC filings, so labels are right today.

The weekly pull records Yahoo's `financialCurrency` as it reaches each company,
which takes a week (one sixth of the companies each morning, Mon-Sat). This
fills the gap from the SEC instead. No Yahoo call is made.

Input is a CSV with `Ticker` and `Reports in` columns, built from the SEC's
financial statement data sets (2025q4-2026q3) by matching each active company's
CIK to the currency of its latest 10-K/10-Q/20-F/40-F. Only companies that
file in a currency other than US dollars are listed; an unknown stays unknown.

Writes ONLY `financial_currency`, and never over a value already held: once
Yahoo has given one, it stands. Without --apply nothing is written.

    python scripts/seed_filing_currency.py --csv companies.csv           # dry run
    python scripts/seed_filing_currency.py --csv companies.csv --apply   # write
"""
import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import envelope
import identity_storage
import storage


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    with args.csv.open(encoding="utf-8") as handle:
        wanted = [(row["Ticker"].strip(), envelope.filing_currency(row["Reports in"]))
                  for row in csv.DictReader(handle)]
    bad = [t for t, c in wanted if not c]
    if bad:
        parser.error(f"not a currency code for: {', '.join(bad[:10])}")

    db = storage.get_db()
    written = held = missing = 0
    for symbol, currency in wanted:
        meta = storage.get_ticker_meta(symbol)
        if not meta:
            missing += 1
            print(f"{symbol}: no company record, skipped")
            continue
        if meta.get("financial_currency"):
            held += 1
            continue
        if args.apply:
            fields = {"financial_currency": currency}
            if not identity_storage.guarded_write(db, symbol, [("", fields)], merge=True):
                db.collection(storage.COLLECTION_ROOT).document(symbol).update(fields)
        written += 1

    verb = "wrote" if args.apply else "would write"
    print(f"{verb} {written}; already held {held}; no record {missing}; of {len(wanted)} listed")


if __name__ == "__main__":
    main()
