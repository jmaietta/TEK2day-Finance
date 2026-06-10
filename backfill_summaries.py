"""
One-time backfill of company descriptions into Firestore.

Pulls longBusinessSummary from Yahoo Finance for every active ticker that
does not already have a `summary` field, and merge-writes ONLY that field
(plus a timestamp). Existing metadata fields and all subcollections
(prices, estimates, financials) are never touched.

Safe properties:
  - set(..., merge=True) writes only the named fields
  - tickers with an existing summary are skipped (never overwritten)
  - resume-safe: rerunning skips everything already backfilled

Usage:
    python backfill_summaries.py --dry-run          # show what would happen
    python backfill_summaries.py --limit 5          # small live test
    python backfill_summaries.py                    # full run (~2-3 hours)
"""
import argparse
import logging
import time
from datetime import datetime, timezone

import yfinance as yf

import storage
from config import COLLECTION_ROOT, FETCH_DELAY

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ydp.backfill_summaries")

MIN_SUMMARY_LEN = 50  # ignore junk/placeholder strings


def fetch_summary(symbol: str) -> str | None:
    """Fetch longBusinessSummary for a single ticker from Yahoo Finance."""
    try:
        info = yf.Ticker(symbol).info or {}
        text = (info.get("longBusinessSummary") or "").strip()
        if len(text) >= MIN_SUMMARY_LEN:
            return text
        logger.warning("%s: no usable summary returned", symbol)
        return None
    except Exception as exc:
        logger.error("%s: summary fetch failed: %s", symbol, exc)
        return None


def write_summary(symbol: str, text: str) -> None:
    """Merge-write ONLY the summary field; nothing else in the doc changes."""
    db = storage.get_db()
    db.collection(COLLECTION_ROOT).document(symbol).set(
        {
            "summary": text,
            "summary_updated_at": datetime.now(timezone.utc).isoformat(),
        },
        merge=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill company summaries into Firestore.")
    parser.add_argument("--dry-run", action="store_true", help="fetch nothing, write nothing; just report")
    parser.add_argument("--limit", type=int, default=0, help="stop after N writes (0 = no limit)")
    parser.add_argument("--delay", type=float, default=FETCH_DELAY, help="seconds between Yahoo fetches")
    args = parser.parse_args()

    db = storage.get_db()

    # One pass over the collection, reading only the summary field,
    # to build the skip list without a per-ticker read later.
    logger.info("scanning %s collection for existing summaries...", COLLECTION_ROOT)
    todo: list[str] = []
    already = 0
    for doc in db.collection(COLLECTION_ROOT).select(["summary", "active"]).stream():
        data = doc.to_dict() or {}
        if data.get("active") is False:
            continue
        if len((data.get("summary") or "").strip()) >= MIN_SUMMARY_LEN:
            already += 1
        else:
            todo.append(doc.id)
    todo.sort()
    logger.info("%d tickers already have summaries; %d to backfill", already, len(todo))

    if args.dry_run:
        logger.info("dry run — first 20 pending: %s", ", ".join(todo[:20]))
        return

    written = 0
    failed = 0
    for i, symbol in enumerate(todo, 1):
        if args.limit and written >= args.limit:
            logger.info("reached --limit %d, stopping", args.limit)
            break
        text = fetch_summary(symbol)
        if text:
            write_summary(symbol, text)
            written += 1
        else:
            failed += 1
        if i % 100 == 0:
            logger.info("progress: %d/%d (written=%d, failed=%d)", i, len(todo), written, failed)
        time.sleep(args.delay)

    logger.info("done: written=%d, failed=%d, skipped(existing)=%d", written, failed, already)


if __name__ == "__main__":
    main()
