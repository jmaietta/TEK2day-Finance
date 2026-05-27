#!/usr/bin/env python3
"""
Pull 5 years of data for S&P 500 tickers only.
Reads S&P 500 membership from CSV, checks each ticker individually in Firestore.
Waits automatically if Firestore quota is temporarily unavailable.
"""
import csv
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
logger = logging.getLogger("ydp.sp500")

CSV_PATH = r"C:\Users\jmaie\OneDrive\Desktop\SP_500_tickers_05252025.csv"
DELAY = 10
MAX_RETRIES = 3


def load_sp500_symbols() -> list[str]:
    tickers = set()
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = row["Ticker"].strip().upper()
            if t:
                tickers.add(t)
    return sorted(tickers)


def wait_for_firestore(db):
    """Keep trying until Firestore responds. Handles quota propagation delays."""
    attempt = 0
    while True:
        try:
            db.collection(storage.COLLECTION_ROOT).document("AAPL").get()
            return
        except (ResourceExhausted, Exception) as exc:
            attempt += 1
            wait = min(60 * attempt, 300)
            logger.info("Firestore not ready (attempt %d), waiting %ds: %s", attempt, wait, exc)
            time.sleep(wait)


def call_with_retry(fn, label: str):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == MAX_RETRIES:
                logger.warning("%s: failed after %d attempts: %s", label, MAX_RETRIES, exc)
                return None
            backoff = DELAY * attempt + random.uniform(1, 5)
            logger.info("%s: attempt %d failed, retrying in %.0fs: %s", label, attempt, backoff, exc)
            time.sleep(backoff)
    return None


def firestore_write_with_retry(fn, label: str):
    """Retry Firestore writes, backing off on quota errors."""
    for attempt in range(1, 6):
        try:
            return fn()
        except ResourceExhausted:
            wait = 60 * attempt
            logger.info("%s: Firestore quota hit, waiting %ds (attempt %d)", label, wait, attempt)
            time.sleep(wait)
        except Exception as exc:
            logger.warning("%s: write error: %s", label, exc)
            return None
    logger.warning("%s: gave up after 5 Firestore retries", label)
    return None


def main():
    sp500 = load_sp500_symbols()
    logger.info("Loaded %d S&P 500 symbols from CSV", len(sp500))

    db = storage.get_db()

    logger.info("Waiting for Firestore to be ready...")
    wait_for_firestore(db)
    logger.info("Firestore is ready.")

    pending = []
    for sym in sp500:
        try:
            doc = db.collection(storage.COLLECTION_ROOT).document(sym).get()
            if doc.exists:
                d = doc.to_dict()
                if d.get("onboard_status") == "done":
                    continue
        except ResourceExhausted:
            logger.info("Quota hit while checking %s, waiting 60s...", sym)
            time.sleep(60)
            try:
                doc = db.collection(storage.COLLECTION_ROOT).document(sym).get()
                if doc.exists and doc.to_dict().get("onboard_status") == "done":
                    continue
            except Exception:
                pass
        pending.append(sym)

    logger.info("%d already onboarded, %d pending", len(sp500) - len(pending), len(pending))

    if not pending:
        logger.info("Nothing to do — all S&P 500 tickers are onboarded.")
        return

    success, fail = 0, 0

    for i, sym in enumerate(pending, 1):
        logger.info("[%d/%d] %s", i, len(pending), sym)

        pulled = False

        # Estimates
        est = call_with_retry(lambda s=sym: fetchers.fetch_estimates(s), f"{sym} estimates")
        if est:
            firestore_write_with_retry(
                lambda s=sym, e=est: storage.write_estimates(s, e["date"], e),
                f"{sym} estimates write",
            )
            logger.info("%s: estimates stored", sym)
            pulled = True
        time.sleep(DELAY)

        # Prices — 5 years
        rows = call_with_retry(lambda s=sym: fetchers.fetch_prices(s, period="5y"), f"{sym} prices")
        if rows:
            for chunk_start in range(0, len(rows), 400):
                chunk = rows[chunk_start:chunk_start + 400]
                firestore_write_with_retry(
                    lambda s=sym, c=chunk: storage.write_prices_batch(s, c),
                    f"{sym} prices write",
                )
            logger.info("%s: %d price rows stored", sym, len(rows))
            pulled = True
        time.sleep(DELAY)

        # Financials — quarterly (~5 years)
        fins = call_with_retry(lambda s=sym: fetchers.fetch_financials(s), f"{sym} financials")
        if fins:
            for fdoc in fins:
                firestore_write_with_retry(
                    lambda s=sym, d=fdoc: storage.write_financials(s, d["period"], d),
                    f"{sym} financials write",
                )
            logger.info("%s: %d financial periods stored", sym, len(fins))
            pulled = True
        time.sleep(DELAY)

        # Update onboard status
        try:
            status = "done" if pulled else "no_data"
            firestore_write_with_retry(
                lambda s=sym, st=status: db.collection(storage.COLLECTION_ROOT).document(s).update({
                    "onboard_status": st,
                    "onboarded_at": datetime.now(timezone.utc).isoformat(),
                }),
                f"{sym} status write",
            )
        except Exception:
            pass

        if pulled:
            success += 1
        else:
            fail += 1

    logger.info("Done. %d pulled, %d failed/no data (%d total)", success, fail, len(pending))


if __name__ == "__main__":
    main()
