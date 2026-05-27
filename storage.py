"""
Firestore read/write for the Yahoo Finance data pipeline.

Schema (Option C — ticker-centric with collection group queries):

    tickers/{AAPL}/
        _meta               → symbol, name, sector, exchange, active flag
        estimates/{date}    → daily consensus estimate snapshot
        prices/{date}       → daily OHLCV
        financials/{period} → quarterly financial statements
"""
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
