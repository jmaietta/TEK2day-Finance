#!/usr/bin/env python3
"""
Daily EOD price pull for all active tickers.

Fetches the last 5 trading days of OHLCV data for each active ticker
and writes to Firestore. The 5-day window ensures we catch up after
weekends and holidays. Firestore document IDs are dates, so duplicates
are impossible — existing days get overwritten with the same data.

Designed to run as a Cloud Run Job triggered by Cloud Scheduler, Mon–Fri.
"""
import logging
import random
import time
from datetime import datetime, timezone

from google.api_core.exceptions import ResourceExhausted

import fetchers
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.daily_prices")

DELAY = 2
MAX_YAHOO_RETRIES = 3
MAX_FIRESTORE_RETRIES = 5


def call_with_retry(fn, label):
    for attempt in range(1, MAX_YAHOO_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == MAX_YAHOO_RETRIES:
                logger.warning("%s: failed after %d attempts: %s", label, MAX_YAHOO_RETRIES, exc)
                return None
            backoff = DELAY * attempt + random.uniform(1, 5)
            logger.info("%s: attempt %d failed, retrying in %.0fs: %s", label, attempt, backoff, exc)
            time.sleep(backoff)
    return None


def firestore_write_with_retry(fn, label):
    for attempt in range(1, MAX_FIRESTORE_RETRIES + 1):
        try:
            return fn()
        except ResourceExhausted:
            wait = 60 * attempt
            logger.info("%s: Firestore quota hit, waiting %ds (attempt %d/%d)", label, wait, attempt, MAX_FIRESTORE_RETRIES)
            time.sleep(wait)
        except Exception as exc:
            logger.warning("%s: write error: %s", label, exc)
            return None
    logger.warning("%s: gave up after %d Firestore retries", label, MAX_FIRESTORE_RETRIES)
    return None


def main():
    start = datetime.now(timezone.utc)
    logger.info("Daily price pull starting")

    tickers = storage.list_active_tickers()
    total = len(tickers)
    logger.info("%d active tickers", total)

    success = 0
    failed = 0

    for i, symbol in enumerate(tickers, 1):
        yahoo_sym = symbol.replace(".", "-")

        rows = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_prices(s, period="5d"),
            f"{symbol} prices",
        )

        if rows:
            for r in rows:
                r["symbol"] = symbol
            firestore_write_with_retry(
                lambda s=symbol, r=rows: storage.write_prices_batch(s, r),
                f"{symbol} prices write",
            )
            logger.info("[%d/%d] %s: %d price rows", i, total, symbol, len(rows))
            success += 1
        else:
            logger.warning("[%d/%d] %s: no prices returned", i, total, symbol)
            failed += 1

        if i % 100 == 0:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 3600
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            logger.info("CHECKPOINT [%d/%d]: %d success, %d failed (%.0f/hr, ETA %.1fh)", i, total, success, failed, rate, remaining)

        time.sleep(DELAY)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 60
    logger.info("Daily price pull complete: %d success, %d failed, %.1f minutes", success, failed, elapsed)


if __name__ == "__main__":
    main()
