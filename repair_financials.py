#!/usr/bin/env python3
"""Repair stub financial records.

WHY THIS EXISTS
    Yahoo publishes a skeleton financial record within hours of an earnings
    release and fills it in over the following days. storage.write_financials()
    is write-once, so whatever landed first is frozen and the real numbers that
    arrive later are discarded.

    Verified 12 Aug 2026 across the ten audited symbols: four of ten had an
    unusable most-recent quarter (AMZN, JPM, JNJ, WMT — 4 populated income
    fields out of 38-49, both other statements empty), plus BRK.B 2025-Q3.
    Every one of those companies HAD reported; JPM filed on 14 July.

SAFETY
    - Dry run by default. Writing requires --apply.
    - Staging by default. Production requires --production, which is refused
      unless --apply is also given, so a production write is always deliberate.
    - Gaps are filled, never overwritten. See storage.merge_financial_doc.
    - Runs as a service account, so it cannot die mid-run on a user reauth.

USAGE
    python repair_financials.py                          # dry run, staging, golden ten
    python repair_financials.py --apply                  # write to staging
    python repair_financials.py --symbols NVDA,JPM       # limit symbols
    python repair_financials.py --all                    # whole universe
    python repair_financials.py --production --apply     # write to production
"""
import argparse
import math
import os
import random
import sys
import time
from datetime import datetime, timezone

from google.cloud import firestore
from google.oauth2.credentials import Credentials

import fetchers
import storage

GOLDEN_TEN = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "XOM", "JNJ", "WMT", "BRK.B"]
PROJECT = "yfinance-cli"
PRODUCTION_DB = "(default)"
STAGING_DB = "staging"

# Yahoo is rate-sensitive; the existing pullers use the same courtesy delay.
DELAY = 3
MAX_YAHOO_RETRIES = 3


# ── stub detection ───────────────────────────────────────────────────────────

def describe_document(doc: dict) -> dict:
    """Populated-field counts per statement section."""
    out = {}
    for section in storage.STATEMENT_SECTIONS:
        block = doc.get(section) or {}
        populated = sum(
            1 for v in block.values()
            if isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))
        )
        out[section] = (populated, len(block))
    return out


def is_stub(doc: dict) -> bool:
    """A stub is a period whose contents never arrived.

    Detection is structural rather than a field-count threshold: a real filing
    populates all three statements. An empty statement section is the reliable
    signal, and it does not need tuning per company (JPM's income statement has
    38 fields, JNJ's has 49).
    """
    counts = describe_document(doc)
    empty_sections = sum(1 for populated, _total in counts.values() if populated == 0)
    return empty_sections > 0


# ── clients ──────────────────────────────────────────────────────────────────

def get_client(database: str) -> firestore.Client:
    """Firestore client authenticated as the service account.

    SA_TOKEN is minted by the caller via:
      gcloud auth print-access-token --impersonate-service-account=...
    Falls back to ambient credentials when running inside Cloud Run, where the
    service account is attached and no token needs passing.
    """
    token = os.environ.get("SA_TOKEN", "").strip()
    creds = Credentials(token=token) if token else None
    kwargs = {"project": PROJECT}
    if creds:
        kwargs["credentials"] = creds
    if database != PRODUCTION_DB:
        kwargs["database"] = database
    return firestore.Client(**kwargs)


def fetch_with_retry(fn, label):
    for attempt in range(1, MAX_YAHOO_RETRIES + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == MAX_YAHOO_RETRIES:
                print(f"    ! {label}: failed after {MAX_YAHOO_RETRIES} attempts: {exc}", flush=True)
                return None
            time.sleep(DELAY * attempt + random.uniform(1, 3))
    return None


# ── the run ──────────────────────────────────────────────────────────────────

def run(symbols, database, apply_changes):
    db = get_client(database)
    started = datetime.now(timezone.utc)

    mode = "APPLY (writes enabled)" if apply_changes else "DRY RUN (nothing written)"
    print("=" * 78)
    print("  TEK2day Finance — financial stub repair")
    print(f"  database : {database}")
    print(f"  mode     : {mode}")
    print(f"  symbols  : {len(symbols)}")
    print(f"  started  : {started.isoformat(timespec='seconds')}")
    print("=" * 78)
    print()

    stubs_found = 0
    docs_changed = 0
    fields_filled = 0
    unrepairable = []
    retry_later = []

    for symbol in symbols:
        col = db.collection(storage.COLLECTION_ROOT).document(symbol).collection("financials")
        stored = {d.id: (d.to_dict() or {}) for d in col.stream()}
        stub_ids = sorted(pid for pid, doc in stored.items() if is_stub(doc))
        if not stub_ids:
            continue

        stubs_found += len(stub_ids)
        print(f"{symbol} — {len(stub_ids)} stub period(s): {', '.join(stub_ids)}")

        # One Yahoo fetch per symbol serves every stub period it has.
        yahoo_sym = symbol.replace(".", "-")
        quarterly = fetch_with_retry(lambda s=yahoo_sym: fetchers.fetch_financials(s), f"{symbol} quarterly") or []
        time.sleep(DELAY)
        annual = fetch_with_retry(lambda s=yahoo_sym: fetchers.fetch_annual_financials(s), f"{symbol} annual") or []
        fresh = {doc["period"]: doc for doc in list(quarterly) + list(annual)}

        for pid in stub_ids:
            before = describe_document(stored[pid])
            incoming = fresh.get(pid)
            if incoming is None:
                print(f"    {pid}: Yahoo no longer returns this period — cannot repair")
                unrepairable.append(f"{symbol} {pid}")
                continue

            merged, filled = storage.merge_financial_doc(stored[pid], incoming)
            if not filled:
                after = describe_document(incoming)
                # Distinguish "Yahoo hasn't caught up yet" from "Yahoo never will".
                # AMZN 2026-Q2 was still a stub at Yahoo two weeks after AMZN
                # reported — that one is worth retrying; a 2021 annual is not.
                if _is_recent(stored[pid]):
                    print(f"    {pid}: Yahoo has not published this period yet — RETRY LATER")
                    retry_later.append(f"{symbol} {pid}")
                else:
                    print(f"    {pid}: Yahoo has nothing to add — incomplete at source, unlikely to change")
                    unrepairable.append(f"{symbol} {pid}")
                print(f"         stored {_fmt(before)}   yahoo {_fmt(after)}")
                continue

            after = describe_document(merged)
            verb = "filled" if apply_changes else "would fill"
            print(f"    {pid}: {verb} {len(filled)} field(s)")
            print(f"         before {_fmt(before)}")
            print(f"         after  {_fmt(after)}")
            for path in sorted(filled)[:4]:
                section, field = path.split(".", 1)
                print(f"         + {field} = {_num(merged[section][field])}")
            if len(filled) > 4:
                print(f"         + ... and {len(filled) - 4} more")

            if apply_changes:
                storage.backfill_financials(symbol, pid, incoming, db=db)

            docs_changed += 1
            fields_filled += len(filled)
        print()

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print("=" * 78)
    print(f"  stub periods found     : {stubs_found}")
    print(f"  documents {'repaired' if apply_changes else 'repairable'}     : {docs_changed}")
    print(f"  fields {'filled' if apply_changes else 'fillable'}          : {fields_filled}")
    print(f"  retry later            : {len(retry_later)}"
          + (f"  ({', '.join(retry_later)})" if retry_later else "")
          + "   [Yahoo not caught up yet]")
    print(f"  unrepairable           : {len(unrepairable)}"
          + (f"  ({', '.join(unrepairable)})" if unrepairable else "")
          + "   [nothing at source]")
    print(f"  elapsed                : {elapsed:.0f}s")
    if not apply_changes:
        print()
        print("  DRY RUN — nothing was written. Re-run with --apply to make these changes.")
    print("=" * 78)
    return 0


def _is_recent(doc: dict, days: int = 180) -> bool:
    """Is this period recent enough that Yahoo may still fill it in?

    Yahoo publishes the full statements over days-to-weeks after a release, so a
    just-reported quarter that is still empty is worth retrying. An old annual
    that Yahoo has never populated will not suddenly appear.
    """
    try:
        end = datetime.strptime(str(doc.get("period_end")), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - end).days <= days


def _fmt(counts: dict) -> str:
    return " ".join(f"{s.split('_')[0]}={p}/{t}" for s, (p, t) in counts.items())


def _num(v) -> str:
    if isinstance(v, (int, float)) and abs(v) >= 1_000_000:
        return f"{v/1e6:,.0f}m"
    return str(v)


def main():
    ap = argparse.ArgumentParser(description="Repair stub financial records.")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--production", action="store_true", help="target production instead of staging")
    ap.add_argument("--symbols", help="comma-separated symbols (default: the audited ten)")
    ap.add_argument("--all", action="store_true", help="every active ticker")
    args = ap.parse_args()

    if args.production and not args.apply:
        # A production dry run reads production, which is harmless, but pairing
        # the flags keeps "production" from ever being a casual default.
        print("Refusing --production without --apply. Dry-run against staging first.")
        return 2

    database = PRODUCTION_DB if args.production else STAGING_DB

    if args.all:
        symbols = sorted(storage.list_active_tickers())
    elif args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = GOLDEN_TEN

    if args.production and args.apply:
        print()
        print("  *** TARGETING PRODUCTION ***")
        print(f"  {len(symbols)} symbols. Gaps will be filled; populated values are never overwritten.")
        print()

    return run(symbols, database, args.apply)


if __name__ == "__main__":
    sys.exit(main())
