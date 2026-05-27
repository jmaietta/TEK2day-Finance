#!/usr/bin/env python3
"""
Onboard tickers from sec_company_tickers.json into Firestore.

Loads tickers in daily batches to avoid Yahoo rate limits.
Tracks progress in Firestore so it can resume after interruption.

Usage:
    python onboard.py load-tickers                Load all tickers into Firestore (metadata only, fast)
    python onboard.py pull-batch --size 750        Pull data for the next batch of un-pulled tickers
    python onboard.py status                       Show onboard progress
"""
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import click
import os

import fetchers
import storage
from config import FIRESTORE_PROJECT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.onboard")

SEC_TICKERS_PATH = Path(
    os.getenv("SEC_TICKERS_JSON", "sec_company_tickers.json")
)


def _load_sec_tickers() -> list[dict]:
    with open(SEC_TICKERS_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    tickers = []
    for entry in raw.values():
        sym = entry.get("ticker", "").strip().upper()
        if not sym or "." in sym or "-" in sym or len(sym) > 5:
            continue
        tickers.append({
            "symbol": sym,
            "name": entry.get("title", ""),
            "cik": entry.get("cik_str"),
            "sector": entry.get("sector", ""),
            "industry": entry.get("industry", ""),
            "active": True,
        })
    return tickers


@click.group()
def cli():
    """Onboard tickers into the yfinance data pipeline."""
    pass


@cli.command("load-tickers")
def load_tickers():
    """Load all tickers from sec_company_tickers.json into Firestore as metadata.
    No Yahoo API calls — just registers them in the database."""
    tickers = _load_sec_tickers()
    click.echo(f"Loading {len(tickers)} tickers into Firestore...")

    db = storage.get_db()
    batch = db.batch()
    count = 0

    for t in tickers:
        sym = t["symbol"]
        ref = db.collection(storage.COLLECTION_ROOT).document(sym)
        doc = {
            "symbol": sym,
            "name": t["name"],
            "cik": t["cik"],
            "sector": t["sector"],
            "industry": t["industry"],
            "active": True,
            "onboard_status": "pending",
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        batch.set(ref, doc, merge=True)
        count += 1

        if count % 400 == 0:
            batch.commit()
            batch = db.batch()
            logger.info("committed %d tickers", count)

    if count % 400 != 0:
        batch.commit()

    click.echo(f"Loaded {count} tickers into Firestore.")


@cli.command("pull-batch")
@click.option("--size", default=750, help="Number of tickers to pull per batch.")
@click.option("--delay", default=1.5, help="Seconds between Yahoo requests per ticker.")
def pull_batch(size, delay):
    """Pull estimates, financials, and prices for the next batch of
    tickers that haven't been onboarded yet."""
    db = storage.get_db()

    docs = (
        db.collection(storage.COLLECTION_ROOT)
        .where("onboard_status", "==", "pending")
        .limit(size)
        .stream()
    )
    pending = [(doc.id, doc.to_dict()) for doc in docs]

    if not pending:
        click.echo("No pending tickers. Onboard is complete.")
        return

    click.echo(f"Pulling data for {len(pending)} tickers (delay={delay}s)...")
    success, fail = 0, 0

    for i, (sym, meta) in enumerate(pending, 1):
        logger.info("[%d/%d] %s — %s", i, len(pending), sym, meta.get("name", ""))

        pulled = False

        # Estimates (current consensus)
        try:
            est = fetchers.fetch_estimates(sym)
            if est:
                storage.write_estimates(sym, est["date"], est)
                pulled = True
        except Exception as exc:
            logger.warning("%s: estimates error: %s", sym, exc)
        time.sleep(delay)

        # Prices (5 years of history)
        try:
            rows = fetchers.fetch_prices(sym, period="5y")
            if rows:
                for chunk_start in range(0, len(rows), 400):
                    chunk = rows[chunk_start:chunk_start + 400]
                    storage.write_prices_batch(sym, chunk)
                pulled = True
                logger.info("%s: %d price rows stored", sym, len(rows))
        except Exception as exc:
            logger.warning("%s: prices error: %s", sym, exc)
        time.sleep(delay)

        # Financials (quarterly — yfinance gives ~5 years)
        try:
            fins = fetchers.fetch_financials(sym)
            if fins:
                for doc in fins:
                    storage.write_financials(sym, doc["period"], doc)
                pulled = True
                logger.info("%s: %d financial periods stored", sym, len(fins))
        except Exception as exc:
            logger.warning("%s: financials error: %s", sym, exc)
        time.sleep(delay)

        # Mark status
        status = "done" if pulled else "no_data"
        db.collection(storage.COLLECTION_ROOT).document(sym).update({
            "onboard_status": status,
            "onboarded_at": datetime.now(timezone.utc).isoformat(),
        })

        if pulled:
            success += 1
        else:
            fail += 1

    click.echo(f"\nBatch complete: {success} pulled, {fail} no data ({len(pending)} total)")


@cli.command("status")
def status():
    """Show onboard progress."""
    db = storage.get_db()

    total = len(list(db.collection(storage.COLLECTION_ROOT).stream()))

    done = len(list(
        db.collection(storage.COLLECTION_ROOT)
        .where("onboard_status", "==", "done")
        .stream()
    ))

    no_data = len(list(
        db.collection(storage.COLLECTION_ROOT)
        .where("onboard_status", "==", "no_data")
        .stream()
    ))

    pending = len(list(
        db.collection(storage.COLLECTION_ROOT)
        .where("onboard_status", "==", "pending")
        .stream()
    ))

    click.echo(f"Total tickers:  {total}")
    click.echo(f"  Pulled:       {done}")
    click.echo(f"  No data:      {no_data}")
    click.echo(f"  Pending:      {pending}")

    if total > 0:
        pct = ((done + no_data) / total) * 100
        click.echo(f"  Progress:     {pct:.1f}%")


if __name__ == "__main__":
    cli()
