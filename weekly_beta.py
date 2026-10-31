#!/usr/bin/env python3
"""The weekly-beta job: every active company's beta for the newest finished week.

Cloud Run job `weekly-beta`, every weekday 8 PM ET (after the price run), on
the daily-prices image with this file as its command. Most evenings it reads
SPY's prices, sees the stored table already covers the newest finished week,
and stops. After a Friday close is stored it works out every company (beta.py)
and stores the result in beta_weekly/{symbol}, keeping each week in its
history. Company records are never written. No Yahoo request.

    python weekly_beta.py            # what the job runs
    python weekly_beta.py --force    # work it out again for the same week
"""
import argparse
import logging
import sys

import beta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.weekly_beta")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="work it out even if this week is stored")
    args = parser.parse_args()
    try:
        summary = beta.run(force=args.force)
    except Exception:
        logger.exception("weekly beta failed")
        sys.exit(1)
    logger.info("weekly beta done: %s", summary)
    return 0


if __name__ == "__main__":
    main()
