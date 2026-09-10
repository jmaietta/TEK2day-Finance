#!/usr/bin/env python3
"""
Weekly estimate pull for all active tickers.

Fetches consensus EPS and revenue estimates and writes a new dated
snapshot to Firestore. Each pull creates a new document — this builds
the proprietary estimate history over time.

Also refreshes ticker metadata (market cap, shares outstanding, etc.)
on each pass since we're already hitting Yahoo for each ticker.

Designed to run as a Cloud Run Job triggered by Cloud Scheduler, weekly.
"""
import logging
import os
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
logger = logging.getLogger("ydp.weekly_estimates")

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
            fn()
            return True
        except ResourceExhausted:
            wait = 60 * attempt
            logger.info("%s: Firestore quota hit, waiting %ds (attempt %d/%d)", label, wait, attempt, MAX_FIRESTORE_RETRIES)
            time.sleep(wait)
        except Exception as exc:
            logger.warning("%s: write error: %s", label, exc)
            return False
    logger.warning("%s: gave up after %d Firestore retries", label, MAX_FIRESTORE_RETRIES)
    return False


def main():
    start = datetime.now(timezone.utc)
    logger.info("Weekly estimate pull starting")

    tickers = storage.list_active_tickers()
    # Daily tranche: process 1/N of the universe per weekday so one run finishes
    # within the task timeout. Schedulers fire Mon–Sat, so every ticker refreshes
    # across the week. Override the slice for manual runs with TRANCHE_INDEX.
    _tcount = int(os.getenv("TRANCHE_COUNT", "6"))
    _tidx = (int(os.getenv("TRANCHE_INDEX")) if os.getenv("TRANCHE_INDEX") else datetime.now(timezone.utc).weekday()) % _tcount
    tickers = [t for i, t in enumerate(sorted(tickers)) if i % _tcount == _tidx]
    logger.info("Tranche %d of %d: %d tickers this run", _tidx, _tcount, len(tickers))
    total = len(tickers)
    logger.info("%d active tickers", total)

    est_success = 0
    est_skipped = 0
    meta_success = 0
    failed = 0
    write_failed = 0
    continuity_failed = []

    for i, symbol in enumerate(tickers, 1):
        yahoo_sym = symbol.replace(".", "-")

        # Estimates
        est = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_estimates(s),
            f"{symbol} estimates",
        )
        if est:
            from security_identity import freshness_warnings
            issues = [w for w in freshness_warnings(symbol, estimate=est, check_estimates=True)
                      if w["code"].startswith("estimate")]
            if issues:
                logger.warning("%s: %s", symbol, issues)
                if any(w["code"] != "estimate_post_results_provenance_unverified" for w in issues):
                    continuity_failed.append(symbol + ":estimate_horizon_or_results_unverified")
            est["symbol"] = symbol
            written = firestore_write_with_retry(
                lambda s=symbol, e=est: storage.write_estimates(s, e["date"], e),
                f"{symbol} estimates write",
            )
            if written:
                est_success += 1
            else:
                failed += 1
                write_failed += 1
        else:
            est_skipped += 1
            if storage.event_for(symbol):
                continuity_failed.append(symbol + ":estimates")

        time.sleep(DELAY)

        # Metadata refresh
        meta = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_ticker_info(s),
            f"{symbol} meta",
        )
        if meta:
            meta["symbol"] = symbol
            written = firestore_write_with_retry(
                lambda s=symbol, m=meta: storage.write_ticker_meta(s, m),
                f"{symbol} meta write",
            )
            if written:
                meta_success += 1
            else:
                failed += 1
                write_failed += 1
        else:
            failed += 1
            if storage.event_for(symbol):
                continuity_failed.append(symbol + ":metadata")

        if i % 100 == 0:
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 3600
            rate = i / elapsed if elapsed > 0 else 0
            remaining = (total - i) / rate if rate > 0 else 0
            logger.info(
                "CHECKPOINT [%d/%d]: %d estimates, %d no_estimates, %d meta, %d failed (%.0f/hr, ETA %.1fh)",
                i, total, est_success, est_skipped, meta_success, failed, rate, remaining,
            )

        time.sleep(DELAY)

    elapsed = (datetime.now(timezone.utc) - start).total_seconds() / 60
    logger.info(
        "Weekly estimate pull complete: %d estimates, %d no_estimates, %d meta refreshed, %d failed, %.1f minutes",
        est_success, est_skipped, meta_success, failed, elapsed,
    )
    if total and (est_success == 0 or meta_success == 0 or write_failed or continuity_failed):
        logger.error("Incomplete reviewed-security estimates/metadata: %s; failed writes=%d", continuity_failed, write_failed)
        raise RuntimeError("Estimate/metadata maintenance incomplete; inspect per-dataset logs")


if __name__ == "__main__":
    main()
