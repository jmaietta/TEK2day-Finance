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
import os
import random
import sys
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

# Override for catch-up runs after an outage, e.g. PRICE_PULL_PERIOD=1mo.
PERIOD = os.environ.get("PRICE_PULL_PERIOD", "5d")

# ⚠️ A RUN THAT WRITES NOTHING MUST NOT REPORT SUCCESS.
#
# 13-19 Aug 2026: every one of 9,911 tickers failed, this job exited 0 every
# night, and Cloud Run recorded SIX SUCCESSES per run for a week while
# Firestore's prices sat frozen. The bug itself was one line; the SILENCE is
# what turned one bad night into seven. Nothing here was unrecoverable — not
# noticing was.
#
# THE FLOOR IS MEASURED, NOT GUESSED. On healthy days ~87% of tickers return
# data (many of the rest are delisted shells, warrants and thin OTC lines that
# legitimately have no bar). During the outage it was 0.0%. Anything from ~20%
# to ~50% separates those two with a wide margin on both sides, so 50% trips
# hard on a wipeout and cannot be reached by an ordinary night's dead tickers.
#
# ⚠️ DELIBERATELY A FLOOR, NOT A DELTA against yesterday. A delta needs state,
# and state that is itself stale is how this class of bug hides.
MIN_SUCCESS_RATE = float(os.environ.get("PRICE_PULL_MIN_SUCCESS_RATE", "0.5"))

# Smoke testing. Empty/0 means the full universe — the scheduled run is unaffected.
SYMBOLS = os.environ.get("PRICE_PULL_SYMBOLS", "").strip()
LIMIT = int(os.environ.get("PRICE_PULL_LIMIT", "0") or 0)


def _select(universe):
    """The tickers this run should cover, and whether it is a smoke test.

    ⚠️ THIS EXISTS BECAUSE THERE WAS NO WAY TO TRY ANYTHING SMALL. The job was
    all 9,911 tickers or nothing, so every change — a dependency bump, a fix, a
    new field — was tested in production at full scale, against the one upstream
    the whole platform depends on. During the 13-19 Aug 2026 outage, confirming
    a theory cost 9,911 Yahoo requests, which is why it was not confirmed for a
    week. Prove it on three companies first.

        PRICE_PULL_SYMBOLS=NVDA,AAPL,MSFT   exactly these, in that order
        PRICE_PULL_LIMIT=25                 the first 25 of the universe

    SYMBOLS wins if both are set — naming companies is a more specific
    instruction than counting them.
    """
    if SYMBOLS:
        wanted = [s.strip().upper() for s in SYMBOLS.split(",") if s.strip()]
        known = set(universe)
        picked = [s for s in wanted if s in known]
        unknown = [s for s in wanted if s not in known]
        if unknown:
            # Loudly, not silently: a typo must not read as "that ticker failed".
            logger.warning("PRICE_PULL_SYMBOLS names %d ticker(s) not under "
                           "coverage, skipping them: %s", len(unknown), ",".join(unknown))
        if not picked:
            logger.error("PRICE_PULL_SYMBOLS matched NO tickers under coverage: %r", SYMBOLS)
        return picked, True

    if LIMIT > 0:
        return universe[:LIMIT], True

    return universe, False


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
    logger.info("Daily price pull starting (period=%s)", PERIOD)

    universe = sorted(storage.list_active_tickers() or [])
    tickers, smoke = _select(universe)
    if smoke:
        # ⚠️ SHOUTED, and repeated at the end. A partial run that reads like a
        # full one in the logs is how "the pull ran fine last night" becomes
        # false. Coverage is NOT refreshed by this run.
        logger.warning(
            "*** SMOKE TEST RUN: %d of %d tickers. COVERAGE IS NOT REFRESHED BY "
            "THIS RUN. Triggered by %s. ***",
            len(tickers), len(universe),
            "PRICE_PULL_SYMBOLS=%s" % SYMBOLS if SYMBOLS else "PRICE_PULL_LIMIT=%d" % LIMIT,
        )

    # Cloud Run TASK PARALLELISM: each task processes its slice so the FULL universe
    # still refreshes EVERY run (prices must be daily, unlike estimates/financials
    # which use weekday tranches). Driven by the job's --tasks/--parallelism; single
    # task locally. Cloud Run injects CLOUD_RUN_TASK_INDEX / CLOUD_RUN_TASK_COUNT.
    #
    # ⚠️ SHARDING COMES AFTER THE SMOKE-TEST SELECTION, so PRICE_PULL_LIMIT=3
    # means three tickers ACROSS THE WHOLE JOB, not three per shard. The other
    # shards get nothing and exit quietly, which is correct.
    task_index = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
    task_count = int(os.getenv("CLOUD_RUN_TASK_COUNT", "1"))
    tickers = [t for i, t in enumerate(tickers) if i % task_count == task_index]
    logger.info("Task %d of %d: %d tickers this task", task_index, task_count, len(tickers))
    total = len(tickers)
    logger.info("%d active tickers", total)

    success = 0
    failed = 0

    for i, symbol in enumerate(tickers, 1):
        yahoo_sym = symbol.replace(".", "-")

        rows = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_prices(s, period=PERIOD),
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
    # ASCII deliberately: log lines should not depend on the console's encoding.
    logger.info("%sDaily price pull complete: %d success, %d failed, %.1f minutes",
                "SMOKE TEST - " if smoke else "", success, failed, elapsed)
    if smoke:
        logger.warning(
            "*** THIS WAS A SMOKE TEST OVER %d TICKER(S), NOT THE NIGHTLY PULL. "
            "The universe has NOT been refreshed. ***", total)

    rate = (success / total) if total else 0.0
    if total and rate < MIN_SUCCESS_RATE:
        logger.error(
            "PRICE PULL FAILED: only %d of %d tickers returned data (%.1f%%, "
            "floor is %.0f%%). Nothing useful was written. Exiting non-zero so "
            "this execution is recorded as FAILED.%s",
            success, total, rate * 100, MIN_SUCCESS_RATE * 100,
            "  [SMOKE TEST RUN, not the nightly pull]" if smoke else "",
        )
        sys.exit(1)
    return 0


if __name__ == "__main__":
    main()
