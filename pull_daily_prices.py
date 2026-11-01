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
import math
import os
import random
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from google.api_core.exceptions import ResourceExhausted

import fetchers
import fx_rates
import share_counts
import storage
import universe_sync

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

# THURSDAY'S 5-YEAR RE-ADJUST, AND WHICH ROWS IT WRITES BACK (owner, 1 Nov 2026:
# "only 1 version of the truth ... a full stock price rewrite only once per month").
# Closes are adjusted for dividends and splits, so only an event moves a
# stock's older rows; rewriting all ~1,260 rows of every stock cost 10.2M
# writes a run (29 Oct). With PRICE_REFRESH_MODE=events (set only in the
# Thursday scheduler's override; "full", today's behaviour, by default) a stock
# gets its full history rewritten only when refresh_rows says so, and otherwise
# its newest NEW_ROWS rows, as on a weeknight. The download is the same either way.
# Companies worth LARGE_CAP or more keep the weekly full rewrite (owner, 1 Nov
# 2026: "do the $1 billion plan").
REFRESH_MODE = os.environ.get("PRICE_REFRESH_MODE", "full").strip().lower()
NEW_ROWS = 5
LARGE_CAP = 1e9
_COMPARED = ("open", "high", "low", "close", "volume")

# THE PRICE CHECK (owner, 1 Nov 2026: "Yes"), with the same switch. Before any
# row is written, the stored closes are compared with Yahoo's fresh download:
# CHECKED days spread across the five years each Thursday, every day on a
# month's first Thursday. It catches TEK2day's own mistakes (a bad write, a
# missed split or dividend, a stock left on old prices) and Yahoo revising its
# history, not Yahoo being wrong. A close differs when it moves by more than one
# rounding step and 0.01%: "expected" when a split or dividend since the last
# full rewrite shifts the older closes, "unexplained" otherwise. An unexplained
# difference over ALERT_PCT is logged as ERROR "PRICE CHECK ALERT", which the
# owner's email alert watches. Nothing is stored.
CHECKED = 5
ALERT_PCT = 1.0

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


def _spread(ordered):
    """CHECKED days spread across the download, its oldest first."""
    n = len(ordered)
    return [ordered[i] for i in sorted({i * n // CHECKED for i in range(CHECKED)})] if n else []


def _differs(old, new):
    """More than one 4-decimal rounding step AND more than 0.01%."""
    return abs(new - old) > 0.0001 and abs(new - old) > abs(old) * 0.0001


def check_prices(compared, held, expected):
    """How the stored closes of `compared` days differ from Yahoo's download.
    price_repair rows are not Yahoo's and are left out."""
    found = {"compared": 0, "missing": 0, "differing": 0, "largest": None, "expected": expected}
    for row in compared:
        old = held.get(row["date"])
        if old is None:
            found["missing"] += 1
            continue
        if old.get("price_repair"):
            continue
        found["compared"] += 1
        a, b = old.get("close"), row.get("close")
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (a, b)):
            continue
        if _differs(a, b):
            found["differing"] += 1
            pct = (b - a) / a * 100 if a else math.inf
            if found["largest"] is None or abs(pct) > abs(found["largest"][3]):
                found["largest"] = (row["date"], a, b, pct)
    return found


def _log_check(symbol, found):
    if not (found["differing"] or found["missing"]):
        return
    kind = "expected" if found["expected"] else "unexplained"
    day, stored, new, pct = found["largest"] or ("-", None, None, 0.0)
    alert = kind == "unexplained" and abs(pct) > ALERT_PCT
    logger.log(logging.ERROR if alert else logging.WARNING,
               "%s%s: %d of %d stored days differ from Yahoo, %d missing (%s); largest %s: stored %s, "
               "Yahoo %s (%+.2f%%)", "PRICE CHECK ALERT " if alert else "price check ", symbol,
               found["differing"], found["compared"], found["missing"], kind, day, stored, new, pct)


def refresh_rows(symbol, rows, splits, adjustments, today, checks=None):
    """(rows to write, why) for one stock's 5-year download, after the price
    check (appended to `checks`). Every row the full history is not rewritten
    for is already stored on the same basis. Full when:
      - the switch is off ("full"): nothing read, nothing checked;
      - the check's reads fail ("unreadable");
      - a reviewed identity covers the ticker ("guarded"): its fenced writes
        and price_repair rows keep exactly today's Thursday;
      - it is a month's first Thursday ("monthly"): the safety net for a
        revision nothing below sees;
      - the company is worth LARGE_CAP or more ("large"), or its record cannot
        be read ("unreadable"); no market cap on file counts as smaller;
      - this stock has no last full-rewrite date ("no_refresh_date");
      - a split, dividend or capital-gains payout is dated on or after it ("event");
      - a checked day is missing or differs at all from Yahoo's ("tripwire"):
        a late-added dividend or a revised history moves it.
    Otherwise the newest NEW_ROWS rows ("new_rows"), and the last full-rewrite
    date stays where it is (main). Reads: CHECKED days + 2, or every day + 1
    on a month's first Thursday."""
    if REFRESH_MODE != "events":
        return rows, "full"
    ordered = sorted(rows, key=lambda r: r["date"])
    monthly = today.day <= 7
    # The newest NEW_ROWS days are left out: weeknights rewrite them anyway, and
    # this run's own newest day is not stored yet.
    history = ordered[:-NEW_ROWS]
    compared = history if monthly else _spread(history)
    try:
        last = share_counts.last_refresh(symbol)
        held = storage.get_prices_on(symbol, [r["date"] for r in compared])
    except Exception as exc:  # noqa: BLE001 - unknown means rewrite it all
        logger.warning("%s: stored prices unreadable, full rewrite, not checked: %s", symbol, exc)
        return rows, "unreadable"
    moved = not last or any(day >= last for day in [*splits, *adjustments])
    found = check_prices(compared, held, expected=moved)
    _log_check(symbol, found)
    if checks is not None:
        checks.append(found)
    if storage.reviewed_identity(symbol):
        return rows, "guarded"
    if monthly:
        return rows, "monthly"
    try:
        cap = (storage.get_ticker_meta(symbol) or {}).get("market_cap")
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: company record unreadable, full rewrite: %s", symbol, exc)
        return rows, "unreadable"
    if isinstance(cap, (int, float)) and cap >= LARGE_CAP:
        return rows, "large"
    if not last:
        return rows, "no_refresh_date"
    if moved:
        return rows, "event"
    if any(r["date"] not in held or any(held[r["date"]].get(k) != r.get(k) for k in _COMPARED)
           for r in compared):
        return rows, "tripwire"
    return ordered[-NEW_ROWS:], "new_rows"


def main():
    # A manual run of just the universe sync and the sector fill (owner, 2026-10-02:
    # "The first fill will run this weekend as we will manually push a job"):
    #   gcloud run jobs execute daily-price-pull --tasks=1 --update-env-vars=UNIVERSE_SYNC_ONLY=1
    # Makes no price requests.
    if os.environ.get("UNIVERSE_SYNC_ONLY", "").strip() in {"1", "true", "yes"}:
        logger.info("Universe sync only: no prices this run")
        if int(os.getenv("CLOUD_RUN_TASK_INDEX", "0")) != 0:
            logger.info("Universe sync only: task 0 runs it; this task has nothing to do")
            sys.exit(0)
        result = universe_sync.run()
        sys.exit(0 if result is not None else 1)
    start = datetime.now(timezone.utc)
    logger.info("Daily price pull starting (period=%s)", PERIOD)

    universe = sorted(storage.list_active_tickers() or [])
    tickers, smoke = _select(universe)
    if not SYMBOLS:
        # Tickers Yahoo has had no price for in over 30 days, from last night's
        # sync. Named tickers (PRICE_PULL_SYMBOLS) are always asked.
        skip = universe_sync.skip_list()
        if skip:
            before = len(tickers)
            tickers = [t for t in tickers if t not in skip]
            logger.info("%d ticker(s) left out: no Yahoo price in over %d days "
                        "(asked again on the first weekday of the month)",
                        before - len(tickers), universe_sync.STALE_DAYS)
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
    continuity_failed = []
    paths, rows_written, checks = Counter(), 0, []
    today_et = datetime.now(ZoneInfo("America/New_York")).date()
    # Names the universe sync added get their price history on their first night,
    # in the one request the job makes for them anyway (no extra Yahoo calls).
    backfill = universe_sync.backfill_pending() if tickers else set()
    if backfill & set(tickers):
        logger.info("%d newly added ticker(s) get %s of history tonight", len(backfill & set(tickers)),
                    universe_sync.BACKFILL_PERIOD)

    for i, symbol in enumerate(tickers, 1):
        yahoo_sym = symbol.replace(".", "-")

        period = universe_sync.BACKFILL_PERIOD if symbol in backfill else PERIOD
        splits, adjustments = {}, set()
        rows = call_with_retry(
            lambda s=yahoo_sym, p=period: fetchers.fetch_prices(s, period=p, splits=splits,
                                                                adjustments=adjustments),
            f"{symbol} prices",
        )

        if rows:
            for r in rows:
                r["symbol"] = symbol
            kept = period
            if period in share_counts.LONG_PERIODS and symbol not in backfill:
                rows, path = refresh_rows(symbol, rows, splits, adjustments, today_et, checks)
                paths[path] += 1
                if path == "new_rows":
                    # checked_at is when the whole history was last re-adjusted,
                    # so an event Yahoo lists late, dated after it, still counts
                    # next week. Recorded as a short download: events only.
                    kept = "5d"
            written = firestore_write_with_retry(
                lambda s=symbol, r=rows: storage.write_prices_batch(s, r),
                f"{symbol} prices write",
            )
            if written:
                logger.info("[%d/%d] %s: %d price rows written", i, total, symbol, len(rows))
                success += 1
                rows_written += len(rows)
                # The same answer's splits, for share counts (no extra Yahoo call).
                share_counts.save_splits(None, symbol, splits, kept)
                if symbol in backfill:
                    try:
                        universe_sync.backfill_done(None, symbol)
                    except Exception as exc:
                        logger.warning("%s: history written, backfill flag not cleared: %s", symbol, exc)
            else:
                failed += 1
                if storage.event_for(symbol):
                    continuity_failed.append(symbol)
        else:
            logger.warning("[%d/%d] %s: no prices returned", i, total, symbol)
            failed += 1
            if storage.event_for(symbol):
                continuity_failed.append(symbol)

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
    if paths:
        # The measured share of each path, which the owner's yes to switch on waits for.
        logger.info("5-year refresh (mode %s): %s; %d rows written", REFRESH_MODE,
                    ", ".join(f"{k} {v}" for k, v in sorted(paths.items())), rows_written)
    if checks:
        unexplained = [c for c in checks if c["differing"] and not c["expected"]]
        logger.info("Price check: %d stocks, %d stored days compared; differences: %d expected, "
                    "%d unexplained (%d over %.0f%%); %d stocks with stored days missing",
                    len(checks), sum(c["compared"] for c in checks),
                    sum(1 for c in checks if c["differing"] and c["expected"]), len(unexplained),
                    sum(1 for c in unexplained if abs(c["largest"][3]) > ALERT_PCT), ALERT_PCT,
                    sum(1 for c in checks if c["missing"]))
    if smoke:
        logger.warning(
            "*** THIS WAS A SMOKE TEST OVER %d TICKER(S), NOT THE NIGHTLY PULL. "
            "The universe has NOT been refreshed. ***", total)

    # The universe sync runs once, after task 0's prices, so it never changes the
    # list while the other tasks are slicing it. Its changes apply from the next
    # night. It never raises and never changes this job's result.
    if task_index == 0 and not smoke:
        universe_sync.run()
        # One Yahoo lookup per filing currency (28 today), once a night, task 0
        # only. Never raises and never changes this job's result: a failed
        # currency keeps yesterday's rate (fx_rates.run).
        try:
            fx_rates.run()
        except Exception as exc:  # noqa: BLE001 - prices are already written
            logger.error("Exchange rates failed: %s", exc)
        # Weekly, on the long (Thursday) run only: one checked share count per
        # company, SEC as the source of truth (issue #16). About 4 SEC
        # requests. Never raises and never changes this job's result.
        if PERIOD in share_counts.LONG_PERIODS:
            share_counts.run()

    rate = (success / total) if total else 0.0
    if continuity_failed:
        logger.error("Reviewed security price maintenance failed: %s", ",".join(continuity_failed))
        sys.exit(1)
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
