#!/usr/bin/env python3
"""
Pull annual financials for S&P 500 tickers.
Only fetches annual income statement, balance sheet, and cash flow.
Does NOT touch quarterly data, prices, or estimates.
"""
import csv
import logging
import os
import random
import time

import fetchers
import storage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.annual")

CSV_PATH = os.getenv("SP500_CSV", "sp500_tickers.csv")
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


def main():
    sp500 = load_sp500_symbols()
    # Add the dot-ticker names Yahoo needs dashes for
    ticker_map = {}
    for sym in sp500:
        yahoo_sym = sym.replace(".", "-")
        ticker_map[sym] = yahoo_sym

    logger.info("Pulling annual financials for %d S&P 500 tickers", len(sp500))

    success, fail = 0, 0

    for i, sym in enumerate(sorted(ticker_map.keys()), 1):
        yahoo_sym = ticker_map[sym]
        logger.info("[%d/%d] %s", i, len(ticker_map), sym)

        fins = call_with_retry(
            lambda s=yahoo_sym: fetchers.fetch_annual_financials(s),
            f"{sym} annual financials",
        )

        if fins:
            for doc in fins:
                storage.write_financials(sym, doc["period"], doc)
            logger.info("%s: %d annual periods stored", sym, len(fins))
            success += 1
        else:
            logger.warning("%s: no annual data", sym)
            fail += 1

        time.sleep(DELAY)

    logger.info("Done. %d pulled, %d failed/no data (%d total)", success, fail, len(ticker_map))


if __name__ == "__main__":
    main()
