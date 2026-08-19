"""
Firestore read/write for the Yahoo Finance data pipeline.

Schema (Option C — ticker-centric with collection group queries):

    tickers/{AAPL}/
        _meta               → symbol, name, sector, exchange, active flag
        estimates/{date}    → daily consensus estimate snapshot
        prices/{date}       → daily OHLCV
        financials/{period} → quarterly financial statements
"""
import copy
import math
from datetime import datetime, timezone

from google.cloud import firestore

from config import FIRESTORE_PROJECT, COLLECTION_ROOT

_db = None


def get_db() -> firestore.Client:
    global _db
    if _db is None:
        if not FIRESTORE_PROJECT:
            raise RuntimeError(
                "FIRESTORE_PROJECT not set. Add it to .env or export it."
            )
        _db = firestore.Client(project=FIRESTORE_PROJECT)
    return _db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Ticker metadata ──────────────────────────────────────────────────────────

def write_ticker_meta(symbol: str, meta: dict) -> None:
    db = get_db()
    meta["updated_at"] = _now_iso()
    db.collection(COLLECTION_ROOT).document(symbol).set(meta, merge=True)


def get_ticker_meta(symbol: str) -> dict | None:
    db = get_db()
    doc = db.collection(COLLECTION_ROOT).document(symbol).get()
    return doc.to_dict() if doc.exists else None


def list_active_tickers() -> list[str]:
    db = get_db()
    docs = (
        db.collection(COLLECTION_ROOT)
        .where("active", "==", True)
        .stream()
    )
    return sorted([doc.id for doc in docs])


def deactivate_ticker(symbol: str) -> None:
    db = get_db()
    db.collection(COLLECTION_ROOT).document(symbol).update({
        "active": False,
        "deactivated_at": _now_iso(),
    })


# ── Estimates ─────────────────────────────────────────────────────────────────

def write_estimates(symbol: str, date_str: str, data: dict) -> None:
    db = get_db()
    data["fetched_at"] = _now_iso()
    (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("estimates")
        .document(date_str)
        .set(data)
    )


def get_estimates(symbol: str, date_str: str) -> dict | None:
    db = get_db()
    doc = (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("estimates")
        .document(date_str)
        .get()
    )
    return doc.to_dict() if doc.exists else None


def get_estimate_history(symbol: str, limit: int = 90) -> list[dict]:
    db = get_db()
    docs = (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("estimates")
        .order_by("date", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return [doc.to_dict() for doc in docs]


# ── Prices ────────────────────────────────────────────────────────────────────

def write_price(symbol: str, date_str: str, data: dict) -> None:
    db = get_db()
    data["fetched_at"] = _now_iso()
    (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("prices")
        .document(date_str)
        .set(data)
    )


def write_prices_batch(symbol: str, rows: list[dict]) -> None:
    db = get_db()
    batch = db.batch()
    now = _now_iso()
    for row in rows:
        row["fetched_at"] = now
        ref = (
            db.collection(COLLECTION_ROOT)
            .document(symbol)
            .collection("prices")
            .document(row["date"])
        )
        batch.set(ref, row)
    batch.commit()


# ── Financials ────────────────────────────────────────────────────────────────

def write_financials(symbol: str, period: str, data: dict) -> None:
    db = get_db()
    ref = (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("financials")
        .document(period)
    )
    if ref.get().exists:
        return
    data["fetched_at"] = _now_iso()
    ref.set(data)


# ── Financials (read) ────────────────────────────────────────────────────────

STATEMENT_SECTIONS = ("income", "balance_sheet", "cash_flow")


def _is_empty_value(value) -> bool:
    """True when a stored field carries no usable number.

    Yahoo publishes a skeleton record within hours of an earnings release and
    fills it in over the following days. Because write_financials() is
    write-once, whatever landed first is frozen — so those skeletons appear as
    NaN-valued keys (Yahoo returned the key with no number) or as an entirely
    empty statement section.
    """
    if value is None:
        return True
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return True
    return False


# Share counts are the ONE exception to write-once, because they legitimately
# CHANGE rather than merely arrive late. A split, a reverse merger or a
# restatement makes the stored figure wrong, not just older — and Yahoo rewrites
# every past period onto the new basis when one happens.
#
# Measured 18 Aug 2026 across 150 companies: 59 of 430 stored counts (13.7%,
# spanning 53 companies) no longer match Yahoo. GIPR is the clear case — stored
# at exactly 10x Yahoo across four consecutive quarters, a 1-for-10 reverse
# split we ingested either side of, leaving the whole series on the pre-split
# basis. Everything per-share for that company was out by a factor of ten.
# CP, ALK, RJF and CSL show the milder version: 2-5% off after buybacks and
# revisions.
#
# Frozen share counts are worse than a stale figure: they silently contradict
# the prices, which ARE refreshed daily on a split-adjusted basis.
SHARE_COUNT_FIELDS = frozenset({
    "Diluted Average Shares",
    "Basic Average Shares",
    "Share Issued",
    "Ordinary Shares Number",
    "Treasury Shares Number",
})


def merge_financial_doc(existing: dict, incoming: dict) -> tuple[dict, list[str]]:
    """Fill the gaps in `existing` from `incoming`. Pure function — no I/O.

    THE RULE: a populated value is never overwritten. Only fields that are
    absent, None, NaN or Inf are filled.

    ⚠️ ONE EXCEPTION — SHARE COUNTS, see SHARE_COUNT_FIELDS. Those track Yahoo's
    current basis, because a split makes the old number WRONG rather than
    merely old. Guarded so the exception cannot become a hole: the incoming
    value must be finite AND non-zero, so a blank or a zero can never wipe a
    real count. His ruling, 18 Aug.

    This is not caution for its own sake. Yahoo's own history regresses: for
    BRK.B 2024-12-31 Yahoo now returns 2 of 48 cash-flow fields, while our
    stored copy holds a real operating cash flow of 4,621m captured at
    ingestion. A refresh that overwrote would destroy good data and replace it
    with nothing. Filling gaps only makes the operation safe to repeat.

    Period identity (`period`, `period_end`, `symbol`, `freq`) is never touched
    — this repairs the contents of a period, it does not redefine which period
    a document represents.

    Returns the merged document and the list of "section.field" paths filled.
    An empty list means nothing needed repair.
    """
    merged = copy.deepcopy(existing)
    filled: list[str] = []

    for section in STATEMENT_SECTIONS:
        new_block = incoming.get(section) or {}
        if not new_block:
            continue
        old_block = merged.get(section)
        if not isinstance(old_block, dict):
            old_block = {}
            merged[section] = old_block

        for field, new_value in new_block.items():
            if _is_empty_value(new_value):
                continue  # incoming has nothing better to offer
            if field in old_block and not _is_empty_value(old_block[field]):
                # The single exception: a share count follows Yahoo's current
                # basis. Only ever replaced by a real, non-zero number.
                if field in SHARE_COUNT_FIELDS and new_value and new_value != old_block[field]:
                    old_block[field] = new_value
                    filled.append(f"{section}.{field}")
                continue  # otherwise the existing value is real — leave it alone
            old_block[field] = new_value
            filled.append(f"{section}.{field}")

    return merged, filled


def backfill_financials(symbol: str, period: str, incoming: dict, db=None) -> list[str]:
    """Fill gaps in one stored financial document. Returns the fields filled.

    Deliberately separate from write_financials(), which keeps its write-once
    guard. Routine ingestion therefore remains incapable of rewriting history —
    only this function, invoked on purpose, can touch an existing document.

    Writes nothing when there is nothing to fill.
    """
    db = db or get_db()
    ref = (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("financials")
        .document(period)
    )
    snap = ref.get()
    if not snap.exists:
        return []  # nothing to repair; new periods are write_financials()'s job

    merged, filled = merge_financial_doc(snap.to_dict() or {}, incoming)
    if not filled:
        return []

    # Audit trail: what was repaired, and when. fetched_at is left as the
    # original ingestion timestamp so provenance of the first write survives.
    merged["backfilled_at"] = _now_iso()
    merged["backfilled_fields"] = sorted(filled)
    ref.set(merged)
    return filled


def get_all_financials(symbol: str) -> list[dict]:
    db = get_db()
    docs = (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("financials")
        .order_by("period_end", direction=firestore.Query.DESCENDING)
        .stream()
    )
    return [doc.to_dict() for doc in docs]


# ── Prices (read) ────────────────────────────────────────────────────────────

def get_prices_history(symbol: str, limit: int = 1260) -> list[dict]:
    db = get_db()
    docs = (
        db.collection(COLLECTION_ROOT)
        .document(symbol)
        .collection("prices")
        .order_by("date", direction=firestore.Query.DESCENDING)
        .limit(limit)
        .stream()
    )
    return sorted([doc.to_dict() for doc in docs], key=lambda x: x.get("date", ""))


# ── Cross-ticker queries (collection group) ──────────────────────────────────

def query_estimates_by_date(date_str: str) -> list[dict]:
    """Query all tickers' estimates for a given date via collection group."""
    db = get_db()
    docs = (
        db.collection_group("estimates")
        .where("date", "==", date_str)
        .stream()
    )
    results = []
    for doc in docs:
        data = doc.to_dict()
        data["_symbol"] = doc.reference.parent.parent.id
        results.append(data)
    return results
