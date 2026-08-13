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
import os
import random
import time
from datetime import datetime, timezone

from google.api_core.exceptions import ResourceExhausted

import fetchers
import proposals
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


# ── Data Review ──────────────────────────────────────────────────────────────
# Populate stub records from data this pull already fetched. No per-run cap: the
# tranche already bounds the work (a sixth of the universe per run), so a count
# limit would only be a number somebody has to remember to raise. Measured
# 13 Aug 2026: 5,911 stubs across 3,393 companies, so roughly 985 land in each
# run and the backlog clears in one week.
#
# What replaces the cap is a proportional safety trip. Because it is a ratio it
# never needs adjusting, whether the universe is 10,000 tickers or 100,000.
REVIEW_ENABLED = os.getenv("REVIEW_ENABLED", "1").strip() != "0"

# About a third of companies legitimately hold a stub. A run finding stubs in
# more than 80% of tickers is broken, not busy — stub detection has gone wrong,
# and populating on that basis could write across records it should not touch.
try:
    REVIEW_TRIP_RATIO = float(os.getenv("REVIEW_TRIP_RATIO", "0.80"))
except ValueError:
    REVIEW_TRIP_RATIO = 0.80

# Below this many tickers the ratio is noise, so the trip stays out of the way.
REVIEW_TRIP_MIN_TICKERS = 50

_reviewed = 0
_tickers_seen = 0
_tripped = False
_records: list[dict] = []   # what this run did, for the Data Review page


def _safety_trip(total_tickers: int) -> bool:
    """Stop populating if the proportion of stubs is implausible.

    Emits DATA REVIEW SAFETY TRIP, which the "Data review safety trip" alert
    policy watches for and emails on. Nothing further is populated in this run;
    the pull itself carries on ingesting, because that is its actual job.
    """
    global _tripped
    if _tripped or _tickers_seen < REVIEW_TRIP_MIN_TICKERS:
        return _tripped
    ratio = _reviewed / max(_tickers_seen, 1)
    if ratio > REVIEW_TRIP_RATIO:
        _tripped = True
        logger.error(
            "DATA REVIEW SAFETY TRIP: populated %d records across %d tickers (%.0f%%), "
            "above the %.0f%% threshold. Stub detection is likely broken rather than the "
            "backlog being large. Populating is disabled for the rest of this run; "
            "ingestion continues.",
            _reviewed, _tickers_seen, ratio * 100, REVIEW_TRIP_RATIO * 100,
        )
    return _tripped


def _review(symbol: str, doc: dict) -> int:
    """Populate one stub. Returns 1 if it populated, else 0.

    Never raises: proposals.safe_review_and_populate swallows everything and
    logs, so a bad record cannot end the pull.
    """
    global _reviewed
    if not REVIEW_ENABLED or _tripped:
        return 0
    rec = proposals.safe_review_and_populate(symbol, doc["period"], doc, logger=logger)
    if not rec:
        return 0
    if not rec.get("populated"):
        # A stub Yahoo could not fill. Recorded so the page shows the gap
        # rather than silently omitting it.
        _records.append(rec)
        return 0
    _reviewed += 1
    _records.append(rec)
    warnings = [c for c in (rec.get("checks") or []) if not c["pass"]]
    logger.info(
        "data review: populated %s %s with %d fields%s",
        symbol, doc["period"], rec.get("fields", 0),
        f" (WARNING: {warnings[0]['name']})" if warnings else "",
    )
    return 1


def _note_ticker(i: int, total: int) -> None:
    """Record progress and evaluate the safety trip, once per ticker."""
    global _tickers_seen
    _tickers_seen = i
    _safety_trip(total)


def main():
    start = datetime.now(timezone.utc)
    logger.info("Quarterly financial pull starting")
    logger.info(
        "Data review: %s, safety trip at %.0f%% of tickers",
        "enabled" if REVIEW_ENABLED else "DISABLED", REVIEW_TRIP_RATIO * 100,
    )

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

    q_written = 0
    a_written = 0
    populated = 0
    skipped = 0
    failed = 0

    for i, symbol in enumerate(tickers, 1):
        _note_ticker(i, total)
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
                # write_financials refuses to touch a period we already hold, which
                # is right for a complete record and wrong for a stub: Yahoo posts a
                # placeholder within hours of a release and fills it in days later,
                # so the numbers we need are fetched every week and discarded.
                # Runs AFTER the normal write, uses the data already in hand (no
                # extra Yahoo traffic), and cannot raise — the pull's job is
                # ingestion and must not stop for this.
                populated += _review(symbol, doc)
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
    logger.info(
        "Data review: populated %d stub record(s) across %d tickers%s",
        populated, _tickers_seen, " [SAFETY TRIP FIRED]" if _tripped else "",
    )

    # Save what this run did so the Data Review page can show it. Wrapped: a
    # reporting failure must not fail an otherwise successful pull.
    if _records:
        try:
            pid = "DR-" + start.strftime("%Y%m%d-%H%M")
            proposals.save(
                pid, _records,
                target="production",
                source_read_at=start.strftime("%d %b %Y %H:%M UTC"),
            )
            logger.info("Data review: saved report %s with %d record(s)", pid, len(_records))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Data review: could not save the run report: %s", exc)


if __name__ == "__main__":
    main()
