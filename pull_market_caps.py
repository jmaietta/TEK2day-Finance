#!/usr/bin/env python3
"""
Lightweight pass: fetch market cap for all pending tickers from Yahoo.
Marks tickers under $100M as 'excluded'. Skips already-onboarded tickers.
"""
import logging
import random
import time
from datetime import datetime, timezone

from google.cloud.firestore_v1.base_query import FieldFilter
from google.api_core.exceptions import ResourceExhausted

import fetchers
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.mcap")

DELAY = 5
MAX_RETRIES = 3
MIN_MARKET_CAP = 100_000_000


def call_with_retry(fn, label: str):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == MAX_RETRIES:
                logger.warning("%s: failed after %d attempts: %s", label, MAX_RETRIES, exc)
                return None
            backoff = DELAY * attempt + random.uniform(1, 3)
            logger.info("%s: attempt %d failed, retrying in %.0fs: %s", label, attempt, backoff, exc)
            time.sleep(backoff)
    return None


def firestore_write_with_retry(fn, label: str):
    for attempt in range(1, 4):
        try:
            return fn()
        except ResourceExhausted:
            wait = 60 * attempt
            logger.info("%s: Firestore quota hit, waiting %ds", label, wait)
            time.sleep(wait)
        except Exception as exc:
            logger.warning("%s: write error: %s", label, exc)
            return None
    return None


def main():
    db = storage.get_db()

    pending = []
    done = 0
    has_mcap = 0
    docs = db.collection(storage.COLLECTION_ROOT).where(
        filter=FieldFilter("active", "==", True)
    ).stream()

    for doc in docs:
        d = doc.to_dict()
        if d.get("onboard_status") == "done":
            done += 1
            continue
        if d.get("onboard_status") == "excluded":
            continue
        if d.get("market_cap") is not None:
            has_mcap += 1
            continue
        pending.append(doc.id)

    pending.sort()
    logger.info("%d done, %d already have market cap, %d need market cap fetch", done, has_mcap, len(pending))

    if not pending:
        logger.info("Nothing to do.")
        return

    fetched, excluded, failed = 0, 0, 0

    for i, sym in enumerate(pending, 1):
        logger.info("[%d/%d] %s", i, len(pending), sym)

        yahoo_sym = sym.replace(".", "-")
        info = call_with_retry(lambda s=yahoo_sym: fetchers.fetch_ticker_info(s), f"{sym} info")

        if info and info.get("market_cap"):
            mcap = info["market_cap"]
            if mcap < MIN_MARKET_CAP:
                firestore_write_with_retry(
                    lambda s=sym: db.collection(storage.COLLECTION_ROOT).document(s).update({
                        "market_cap": mcap,
                        "onboard_status": "excluded",
                        "exclude_reason": "market_cap_under_100M",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    f"{sym} exclude",
                )
                logger.info("%s: $%.0fM — excluded", sym, mcap / 1e6)
                excluded += 1
            else:
                firestore_write_with_retry(
                    lambda s=sym, i=info: db.collection(storage.COLLECTION_ROOT).document(s).update({
                        "market_cap": i["market_cap"],
                        "name": i.get("name") or "",
                        "sector": i.get("sector") or "",
                        "industry": i.get("industry") or "",
                        "exchange": i.get("exchange") or "",
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }),
                    f"{sym} update",
                )
                logger.info("%s: $%.0fM — kept", sym, mcap / 1e6)
                fetched += 1
        else:
            firestore_write_with_retry(
                lambda s=sym: db.collection(storage.COLLECTION_ROOT).document(s).update({
                    "onboard_status": "excluded",
                    "exclude_reason": "no_yahoo_data",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }),
                f"{sym} no data",
            )
            logger.info("%s: no data — excluded", sym)
            failed += 1

        time.sleep(DELAY)

    logger.info("Done. %d kept (>=$100M), %d excluded (<$100M), %d no data (%d total)",
                fetched, excluded, failed, len(pending))


if __name__ == "__main__":
    main()
