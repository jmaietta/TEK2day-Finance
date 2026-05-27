#!/usr/bin/env python3
"""
Yahoo Finance Data Pipeline CLI.

Pulls financial data from Yahoo Finance and stores it in Firestore
with full history accumulation. Designed to run on a schedule or manually.

Usage:
    python cli.py pull estimates          Pull consensus estimates for all active tickers
    python cli.py pull prices             Pull daily OHLCV for all active tickers
    python cli.py pull financials         Pull quarterly financials for all active tickers
    python cli.py pull all                Pull everything

    python cli.py ticker add AAPL         Add a ticker to the universe
    python cli.py ticker add-list FILE    Add tickers from a file (one per line)
    python cli.py ticker remove AAPL      Deactivate a ticker
    python cli.py ticker list             List all active tickers
    python cli.py ticker load-sp500       Bulk load S&P 500 as starting universe

    python cli.py query estimates AAPL              Latest estimates for a ticker
    python cli.py query estimate-history AAPL        Estimate history for a ticker
    python cli.py query estimates-date 2026-05-25    All tickers' estimates for a date
"""
import logging
import time

import click

import fetchers
import storage
import universe
from config import FETCH_DELAY

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ydp.cli")


@click.group()
def cli():
    """Yahoo Finance Data Pipeline."""
    pass


# ── Pull commands ─────────────────────────────────────────────────────────────

@cli.group()
def pull():
    """Pull data from Yahoo Finance into Firestore."""
    pass


@pull.command("estimates")
@click.option("--symbol", default=None, help="Pull for a single ticker only.")
def pull_estimates(symbol):
    """Pull consensus estimates for active tickers."""
    symbols = [symbol.upper()] if symbol else storage.list_active_tickers()
    if not symbols:
        click.echo("No active tickers. Add some with: ticker add AAPL")
        return

    success, fail = 0, 0
    for sym in symbols:
        data = fetchers.fetch_estimates(sym)
        if data:
            storage.write_estimates(sym, data["date"], data)
            success += 1
            logger.info("%s: estimates stored", sym)
        else:
            fail += 1
        time.sleep(FETCH_DELAY)

    click.echo(f"Estimates: {success} stored, {fail} failed/skipped ({len(symbols)} total)")


@pull.command("prices")
@click.option("--symbol", default=None, help="Pull for a single ticker only.")
@click.option("--period", default="5d", help="History period: 1d, 5d, 1mo, 3mo, 1y, max")
def pull_prices(symbol, period):
    """Pull OHLCV price data for active tickers."""
    symbols = [symbol.upper()] if symbol else storage.list_active_tickers()
    if not symbols:
        click.echo("No active tickers. Add some with: ticker add AAPL")
        return

    success, fail = 0, 0
    for sym in symbols:
        rows = fetchers.fetch_prices(sym, period=period)
        if rows:
            storage.write_prices_batch(sym, rows)
            success += 1
            logger.info("%s: %d price rows stored", sym, len(rows))
        else:
            fail += 1
        time.sleep(FETCH_DELAY)

    click.echo(f"Prices: {success} tickers stored, {fail} failed/skipped ({len(symbols)} total)")


@pull.command("financials")
@click.option("--symbol", default=None, help="Pull for a single ticker only.")
def pull_financials(symbol):
    """Pull quarterly financial statements for active tickers."""
    symbols = [symbol.upper()] if symbol else storage.list_active_tickers()
    if not symbols:
        click.echo("No active tickers. Add some with: ticker add AAPL")
        return

    success, fail = 0, 0
    for sym in symbols:
        docs = fetchers.fetch_financials(sym)
        if docs:
            for doc in docs:
                storage.write_financials(sym, doc["period"], doc)
            success += 1
            logger.info("%s: %d periods stored", sym, len(docs))
        else:
            fail += 1
        time.sleep(FETCH_DELAY)

    click.echo(f"Financials: {success} tickers stored, {fail} failed/skipped ({len(symbols)} total)")


@pull.command("all")
@click.option("--period", default="5d", help="Price history period")
def pull_all(period):
    """Pull estimates, prices, and financials for all active tickers."""
    symbols = storage.list_active_tickers()
    if not symbols:
        click.echo("No active tickers. Add some with: ticker add AAPL")
        return

    click.echo(f"Pulling all data for {len(symbols)} tickers...")

    for sym in symbols:
        logger.info("── %s ──", sym)

        est = fetchers.fetch_estimates(sym)
        if est:
            storage.write_estimates(sym, est["date"], est)
            logger.info("%s: estimates stored", sym)

        rows = fetchers.fetch_prices(sym, period=period)
        if rows:
            storage.write_prices_batch(sym, rows)
            logger.info("%s: %d price rows stored", sym, len(rows))

        fins = fetchers.fetch_financials(sym)
        if fins:
            for doc in fins:
                storage.write_financials(sym, doc["period"], doc)
            logger.info("%s: %d financial periods stored", sym, len(fins))

        time.sleep(FETCH_DELAY)

    click.echo(f"Done. Processed {len(symbols)} tickers.")


# ── Ticker management ─────────────────────────────────────────────────────────

@cli.group()
def ticker():
    """Manage the ticker universe."""
    pass


@ticker.command("add")
@click.argument("symbol")
def ticker_add(symbol):
    """Add a ticker to the universe."""
    symbol = symbol.upper()
    existing = storage.get_ticker_meta(symbol)
    if existing and existing.get("active"):
        click.echo(f"{symbol} is already active.")
        return

    click.echo(f"Validating {symbol}...")
    info = fetchers.fetch_ticker_info(symbol)
    if not info:
        click.echo(f"{symbol}: not found or invalid.")
        return

    storage.write_ticker_meta(symbol, info)
    click.echo(f"Added: {symbol} — {info.get('name', '')} ({info.get('sector', 'N/A')})")


@ticker.command("add-list")
@click.argument("filepath", type=click.Path(exists=True))
def ticker_add_list(filepath):
    """Add tickers from a file (one symbol per line)."""
    with open(filepath) as f:
        symbols = [line.strip().upper() for line in f if line.strip()]

    added, skipped = 0, 0
    for sym in symbols:
        existing = storage.get_ticker_meta(sym)
        if existing and existing.get("active"):
            skipped += 1
            continue

        info = fetchers.fetch_ticker_info(sym)
        if info:
            storage.write_ticker_meta(sym, info)
            logger.info("added %s — %s", sym, info.get("name", ""))
            added += 1
        else:
            logger.warning("skipped %s — validation failed", sym)
            skipped += 1
        time.sleep(FETCH_DELAY)

    click.echo(f"Added {added}, skipped {skipped} ({len(symbols)} total)")


@ticker.command("remove")
@click.argument("symbol")
def ticker_remove(symbol):
    """Deactivate a ticker (data is preserved)."""
    symbol = symbol.upper()
    storage.deactivate_ticker(symbol)
    click.echo(f"Deactivated: {symbol}")


@ticker.command("list")
def ticker_list():
    """List all active tickers."""
    tickers = storage.list_active_tickers()
    if not tickers:
        click.echo("No active tickers.")
        return
    click.echo(f"{len(tickers)} active tickers:")
    for sym in tickers:
        meta = storage.get_ticker_meta(sym)
        name = meta.get("name", "") if meta else ""
        click.echo(f"  {sym:8s} {name}")


@ticker.command("load-sp500")
def ticker_load_sp500():
    """Bulk load S&P 500 constituents as a starting universe."""
    symbols = universe.get_sp500_tickers()
    if not symbols:
        click.echo("Failed to load S&P 500 list.")
        return

    click.echo(f"Loading {len(symbols)} S&P 500 tickers...")
    added, skipped = 0, 0

    for sym in symbols:
        existing = storage.get_ticker_meta(sym)
        if existing and existing.get("active"):
            skipped += 1
            continue

        info = fetchers.fetch_ticker_info(sym)
        if info:
            storage.write_ticker_meta(sym, info)
            added += 1
        else:
            skipped += 1
        time.sleep(FETCH_DELAY)

    click.echo(f"S&P 500: added {added}, skipped {skipped}")


# ── Query commands ────────────────────────────────────────────────────────────

@cli.group()
def query():
    """Query stored data."""
    pass


@query.command("estimates")
@click.argument("symbol")
@click.option("--date", default=None, help="Specific date (YYYY-MM-DD). Default: latest.")
def query_estimates(symbol, date):
    """Show stored estimates for a ticker."""
    symbol = symbol.upper()
    if date:
        data = storage.get_estimates(symbol, date)
        if data:
            _print_dict(data)
        else:
            click.echo(f"No estimates for {symbol} on {date}")
    else:
        history = storage.get_estimate_history(symbol, limit=1)
        if history:
            _print_dict(history[0])
        else:
            click.echo(f"No estimates stored for {symbol}")


@query.command("estimate-history")
@click.argument("symbol")
@click.option("--limit", default=30, help="Number of snapshots to show.")
def query_estimate_history(symbol, limit):
    """Show estimate history for a ticker."""
    symbol = symbol.upper()
    history = storage.get_estimate_history(symbol, limit=limit)
    if not history:
        click.echo(f"No estimate history for {symbol}")
        return

    click.echo(f"{symbol} — {len(history)} estimate snapshots:")
    for snap in history:
        date = snap.get("date", "?")
        click.echo(f"\n  {date}:")
        for key, val in sorted(snap.items()):
            if key in ("date", "symbol", "fetched_at"):
                continue
            if isinstance(val, dict):
                click.echo(f"    {key}:")
                for k2, v2 in val.items():
                    click.echo(f"      {k2}: {v2}")
            else:
                click.echo(f"    {key}: {val}")


@query.command("estimates-date")
@click.argument("date_str")
def query_estimates_date(date_str):
    """Show all tickers' estimates for a given date (collection group query)."""
    results = storage.query_estimates_by_date(date_str)
    if not results:
        click.echo(f"No estimates found for {date_str}")
        return

    click.echo(f"{len(results)} tickers with estimates on {date_str}:")
    for r in sorted(results, key=lambda x: x.get("_symbol", "")):
        sym = r.get("_symbol", "?")
        click.echo(f"  {sym}")


def _print_dict(d: dict, indent: int = 2) -> None:
    prefix = " " * indent
    for key, val in sorted(d.items()):
        if isinstance(val, dict):
            click.echo(f"{prefix}{key}:")
            _print_dict(val, indent + 2)
        else:
            click.echo(f"{prefix}{key}: {val}")


if __name__ == "__main__":
    cli()
