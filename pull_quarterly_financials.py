#!/usr/bin/env python3
"""
Quarterly financial pull for all active tickers.

Fetches quarterly and annual income statements, balance sheets, and
cash flow statements. Write-once guard in storage.write_financials()
ensures existing periods are never overwritten — only new periods
(newly reported quarters/years) get written.

Designed to run as a Cloud Run Job triggered by Cloud Scheduler.
Run weekly or biweekly to catch new filings as they appear.
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
logger = logging.getLogger("ydp.quarterly_financials")

DELAY = 3
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
    logger.info("Quarterly financial pull starting")

    tickers = storage.list_active_tickers()
    total = len(tickers)
    logger.info("%d active tickers", total)

    q_written = 0
    a_written = 0
    skipped = 0
    failed = 0

    for i, symbol in enumerate(tickers, 1):
        yahoo_sym = symbol.replace(".", "-")

        # Quarterly financials
        q_docs = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_financials(s),
            f"{symbol} quarterly",
        )
        if q_docs:
            for doc in q_docs:
                doc["symbol"] = symbol
                firestore_write_with_retry(
                    lambda s=symbol, d=doc: storage.write_financials(s, d["period"], d),
                    f"{symbol} quarterly {doc['period']}",
                )
            q_written += len(q_docs)
        time.sleep(DELAY)

        # Annual financials
        a_docs = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_annual_financials(s),
            f"{symbol} annual",
        )
        if a_docs:
            for doc in a_docs:
                doc["symbol"] = symbol
                firestore_write_with_retry(
                    lambda s=symbol, d=doc: storage.write_financials(s, d["period"], d),
                    f"{symbol} annual {doc['period']}",
                )
            a_written += len(a_docs)
        elif q_docs is None and a_docs is None:
            failed += 1
        else:
            skipped += 1

        if i % 100 == 0:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 3600
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            logger.info(
                "CHECKPOINT [%d/%d]: %d quarterly docs, %d annual docs, %d failed (%.0f/hr, ETA %.1fh)",
                i, total, q_written, a_written, failed, rate, remaining,
            )

        time.sleep(DELAY)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 60
    logger.info(
        "Quarterly financial pull complete: %d quarterly docs, %d annual docs, %d failed, %.1f minutes",
        q_written, a_written, failed, elapsed,
    )


if __name__ == "__main__":
    main()
