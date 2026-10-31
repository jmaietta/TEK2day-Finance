#!/usr/bin/env python3
"""The daily-volatility job: every active company's one-year volatility, kept up to date.

Cloud Run job `daily-volatility`, every weekday 8:30 PM ET (after the price
run), on the daily-prices image with this file as its command. Each evening it
reads SPY's dates and, for each company, only the closes that can have changed
since the last run; Thursday evenings, after the 5-year re-adjust, every
window is read again in full (volatility.py). A price run still writing is
waited for, up to an hour; past that it computes nothing and exits 1. Stores the figure and its
returns in volatility_daily/{symbol}, every distinct figure kept in its
history. Company records and prices are never written. No Yahoo request.

    python daily_volatility.py           # what the job runs
    python daily_volatility.py --full    # read every window again in full
"""
import argparse
import logging
import sys

import volatility

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.daily_volatility")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full", action="store_true", help="read every window again in full")
    args = parser.parse_args()
    try:
        summary = volatility.run(full=args.full)
    except Exception:
        logger.exception("daily volatility failed")
        sys.exit(1)
    logger.info("daily volatility done: %s", summary)
    return 0


if __name__ == "__main__":
    main()
