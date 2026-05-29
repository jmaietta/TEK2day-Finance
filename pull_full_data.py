#!/usr/bin/env python3
"""
One-time full data pull for all tickers with market_cap >= $100M
that don't already have stored data.

Pulls: estimates, 5-year prices, quarterly financials, annual financials.
Skips tickers that already have estimate data (S&P 500 batch).
Sorted by market cap descending — largest companies first.

Designed to run as a Cloud Run Job. Run once, collect data, shut down.
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
logger = logging.getLogger("ydp.fullpull")

DELAY = 5
MAX_YAHOO_RETRIES = 3
MAX_FIRESTORE_RETRIES = 5


def get_already_onboarded():
    db = storage.get_db()
    docs = db.collection("tickers").where("onboard_status", "==", "done").select([]).stream()
    onboarded = set()
    for doc in docs:
        onboarded.add(doc.id)
    logger.info("%d tickers already onboarded", len(onboarded))
    return onboarded


def get_pending_tickers():
    db = storage.get_db()
    onboarded = get_already_onboarded()

    all_docs = list(db.collection("tickers").stream())
    pending = []
    for doc in all_docs:
        d = doc.to_dict()
        mc = d.get("market_cap")
        if mc is None or mc < 100_000_000:
            continue
        if doc.id in onboarded:
            continue
        pending.append((doc.id, mc))

    pending.sort(key=lambda x: x[1], reverse=True)
    logger.info(
        "%d tickers with market_cap >= $100M need full data pull", len(pending)
    )
    return [sym for sym, _ in pending]


def call_with_retry(fn, label):
    for attempt in range(1, MAX_YAHOO_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == MAX_YAHOO_RETRIES:
                logger.warning(
                    "%s: failed after %d attempts: %s",
                    label, MAX_YAHOO_RETRIES, exc,
                )
                return None
            backoff = DELAY * attempt + random.uniform(1, 5)
            logger.info(
                "%s: attempt %d failed, retrying in %.0fs: %s",
                label, attempt, backoff, exc,
            )
            time.sleep(backoff)
    return None


def firestore_write_with_retry(fn, label):
    for attempt in range(1, MAX_FIRESTORE_RETRIES + 1):
        try:
            return fn()
        except ResourceExhausted:
            wait = 60 * attempt
            logger.info(
                "%s: Firestore quota hit, waiting %ds (attempt %d/%d)",
                label, wait, attempt, MAX_FIRESTORE_RETRIES,
            )
            time.sleep(wait)
        except Exception as exc:
            logger.warning("%s: write error: %s", label, exc)
            return None
    logger.warning("%s: gave up after %d Firestore retries", label, MAX_FIRESTORE_RETRIES)
    return None


def pull_ticker(symbol):
    pulled = False
    yahoo_sym = symbol.replace(".", "-")

    # Estimates
    est = call_with_retry(
        lambda: fetchers.fetch_estimates(yahoo_sym), f"{symbol} estimates"
    )
    if est:
        est["symbol"] = symbol
        firestore_write_with_retry(
            lambda: storage.write_estimates(symbol, est["date"], est),
            f"{symbol} estimates write",
        )
        pulled = True
    else:
        db = storage.get_db()
        firestore_write_with_retry(
            lambda: db.collection("tickers").document(symbol)
                     .collection("estimates").document("_no_coverage")
                     .set({"checked_at": datetime.now(timezone.utc).isoformat(), "symbol": symbol}),
            f"{symbol} no_coverage marker",
        )
    time.sleep(DELAY)

    # Prices — 5 years
    rows = call_with_retry(
        lambda: fetchers.fetch_prices(yahoo_sym, period="5y"), f"{symbol} prices"
    )
    if rows:
        for r in rows:
            r["symbol"] = symbol
        for i in range(0, len(rows), 400):
            chunk = rows[i : i + 400]
            firestore_write_with_retry(
                lambda c=chunk: storage.write_prices_batch(symbol, c),
                f"{symbol} prices write",
            )
        logger.info("%s: %d price rows", symbol, len(rows))
        pulled = True
    time.sleep(DELAY)

    # Quarterly financials
    fins = call_with_retry(
        lambda: fetchers.fetch_financials(yahoo_sym), f"{symbol} quarterly"
    )
    if fins:
        for doc in fins:
            doc["symbol"] = symbol
            firestore_write_with_retry(
                lambda d=doc: storage.write_financials(symbol, d["period"], d),
                f"{symbol} quarterly write",
            )
        logger.info("%s: %d quarterly periods", symbol, len(fins))
        pulled = True
    time.sleep(DELAY)

    # Annual financials
    annual = call_with_retry(
        lambda: fetchers.fetch_annual_financials(yahoo_sym), f"{symbol} annual"
    )
    if annual:
        for doc in annual:
            doc["symbol"] = symbol
            firestore_write_with_retry(
                lambda d=doc: storage.write_financials(symbol, d["period"], d),
                f"{symbol} annual write",
            )
        logger.info("%s: %d annual periods", symbol, len(annual))
        pulled = True
    time.sleep(DELAY)

    return pulled


def main():
    pending = get_pending_tickers()
    if not pending:
        logger.info("Nothing to pull — all qualifying tickers already have data.")
        return

    db = storage.get_db()
    success, fail, no_data = 0, 0, 0
    start_time = time.time()

    for i, sym in enumerate(pending, 1):
        elapsed = time.time() - start_time
        rate = i / elapsed * 3600 if elapsed > 60 else 0
        eta = (len(pending) - i) / (rate / 3600) / 3600 if rate > 0 else 0

        logger.info(
            "[%d/%d] %s  (%.0f/hr, ETA %.1fh)",
            i, len(pending), sym, rate, eta,
        )

        try:
            pulled = pull_ticker(sym)
            status = "done" if pulled else "no_data"

            firestore_write_with_retry(
                lambda: db.collection("tickers").document(sym).update({
                    "onboard_status": status,
                    "onboarded_at": datetime.now(timezone.utc).isoformat(),
                }),
                f"{sym} status",
            )

            if pulled:
                success += 1
            else:
                no_data += 1

        except Exception as exc:
            logger.error("%s: FAILED: %s", sym, exc)
            fail += 1

        if i % 100 == 0:
            logger.info(
                "CHECKPOINT [%d/%d]: %d success, %d no_data, %d failed",
                i, len(pending), success, no_data, fail,
            )

    elapsed_h = (time.time() - start_time) / 3600
    logger.info(
        "COMPLETE: %d success, %d no_data, %d failed / %d total (%.1f hours)",
        success, no_data, fail, len(pending), elapsed_h,
    )


if __name__ == "__main__":
    main()
