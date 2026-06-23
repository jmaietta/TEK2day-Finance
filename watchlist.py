"""Watchlist API for TEK2day Finance — LOGIN-GATED, stored in Firestore
(users/{uid}/watchlists on the yfinance-cli project). Supports multiple named
lists. Quotes + export reuse the existing market-data storage. No Kilby code."""
import csv
import io
import os
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import auth
import storage
import terminal

router = APIRouter()


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


WATCHLIST_LIVE_QUOTES = os.getenv("WATCHLIST_LIVE_QUOTES", "1").strip().lower() not in {
    "0",
    "false",
    "no",
    "off",
}
WATCHLIST_LIVE_QUOTE_LIMIT = _env_int("WATCHLIST_LIVE_QUOTE_LIMIT", 100, 0, 500)
WATCHLIST_LIVE_QUOTE_WORKERS = _env_int("WATCHLIST_LIVE_QUOTE_WORKERS", 6, 1, 12)
WATCHLIST_LIVE_QUOTE_TTL_SECONDS = 30
WATCHLIST_STORED_QUOTE_TTL_SECONDS = 300


def _col(uid: str):
    return storage.get_db().collection("users").document(uid).collection("watchlists")


def _to_dict(doc) -> dict:
    d = doc.to_dict() or {}
    return {"id": doc.id, "name": d.get("name", ""), "tickers": d.get("tickers", [])}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_symbols(tickers) -> list[str]:
    seen, out = set(), []
    for t in tickers or []:
        s = str(t).upper().strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out[:500]


def _float_or_none(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if result != result else result


@terminal._ttl_cache(WATCHLIST_STORED_QUOTE_TTL_SECONDS, should_cache=lambda quote: bool(quote))
def _stored_quote_cached(sym: str) -> dict:
    meta = storage.get_ticker_meta(sym) or {}
    rows = storage.get_prices_history(sym, 2) or []
    price = rows[-1]["close"] if rows else None
    chg = None
    if len(rows) >= 2 and rows[-2].get("close"):
        chg = (rows[-1]["close"] - rows[-2]["close"]) / rows[-2]["close"] * 100
    return {
        "symbol": sym,
        "name": meta.get("name") or meta.get("long_name") or sym,
        "sector": meta.get("sector", ""),
        "price": price,
        "chg": chg,
        "change": None,
        "previous_close": rows[-2].get("close") if len(rows) >= 2 else None,
        "volume": rows[-1].get("volume") if rows else None,
        "as_of": rows[-1].get("date") if rows else None,
        "source": "TEK2day EOD",
        "is_live": False,
    }


def _stored_quote(sym: str) -> dict:
    return dict(_stored_quote_cached(sym))


def _apply_live_quote(sym: str, quote: dict) -> dict:
    try:
        live_quote = terminal._live_quote(sym) or {}
    except Exception:
        return quote

    price = _float_or_none(live_quote.get("price"))
    if price is None:
        return quote

    previous_close = _float_or_none(live_quote.get("previous_close"))
    change = _float_or_none(live_quote.get("change"))
    change_pct = _float_or_none(live_quote.get("change_pct"))
    quote.update(
        {
            "price": price,
            "chg": change_pct if change_pct is not None else quote.get("chg"),
            "change": change,
            "previous_close": previous_close or quote.get("previous_close"),
            "volume": _float_or_none(live_quote.get("volume")) or quote.get("volume"),
            "as_of": live_quote.get("date") or quote.get("as_of"),
            "source": "Yahoo Finance",
            "is_live": True,
        }
    )
    return quote


def _quote(sym: str, *, live: bool = True) -> dict:
    quote = _stored_quote(sym)
    return _apply_live_quote(sym, quote) if live else quote


def _quotes(syms: list[str], *, live: bool = True) -> tuple[list[dict], int]:
    quotes = {sym: _stored_quote(sym) for sym in syms}
    live_syms = syms[:WATCHLIST_LIVE_QUOTE_LIMIT] if live and WATCHLIST_LIVE_QUOTES else []
    if live_syms:
        workers = min(WATCHLIST_LIVE_QUOTE_WORKERS, len(live_syms))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(_apply_live_quote, sym, quotes[sym]): sym
                for sym in live_syms
            }
            for future in as_completed(future_map):
                sym = future_map[future]
                try:
                    quotes[sym] = future.result()
                except Exception:
                    pass
    live_count = sum(1 for quote in quotes.values() if quote.get("is_live"))
    return [quotes[sym] for sym in syms], live_count


@router.get("/api/watchlists")
def list_watchlists(request: Request):
    uid = auth.require_uid(request)
    docs = _col(uid).order_by("created_at").stream()
    return {"watchlists": [_to_dict(d) for d in docs]}


class CreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=40)


@router.post("/api/watchlists")
def create_watchlist(body: CreateBody, request: Request):
    uid = auth.require_uid(request)
    ref = _col(uid).document(secrets.token_urlsafe(8))
    now = _now()
    ref.set({"name": body.name.strip()[:40], "tickers": [], "created_at": now, "updated_at": now})
    return _to_dict(ref.get())


class UpdateBody(BaseModel):
    name: str | None = None
    tickers: list[str] | None = None


@router.patch("/api/watchlists/{list_id}")
def update_watchlist(list_id: str, body: UpdateBody, request: Request):
    uid = auth.require_uid(request)
    ref = _col(uid).document(list_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    patch = {"updated_at": _now()}
    if body.name is not None:
        patch["name"] = body.name.strip()[:40]
    if body.tickers is not None:
        patch["tickers"] = _clean_symbols(body.tickers)
    ref.update(patch)
    return _to_dict(ref.get())


@router.delete("/api/watchlists/{list_id}")
def delete_watchlist(list_id: str, request: Request):
    uid = auth.require_uid(request)
    ref = _col(uid).document(list_id)
    if not ref.get().exists:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    ref.delete()
    return {"ok": True}


@router.get("/api/watchlist/quotes")
def watchlist_quotes(request: Request, symbols: str = Query(""), live: bool = Query(True)):
    auth.require_uid(request)
    syms = [s.upper().strip() for s in symbols.split(",") if s.strip()][:500]
    quotes, live_count = _quotes(syms, live=live)
    return {
        "quotes": quotes,
        "source": "Yahoo Finance live quote with TEK2day EOD fallback",
        "cache_seconds": WATCHLIST_LIVE_QUOTE_TTL_SECONDS,
        "live_enabled": bool(live and WATCHLIST_LIVE_QUOTES),
        "live_limit": WATCHLIST_LIVE_QUOTE_LIMIT,
        "live_count": live_count,
        "fallback_count": max(0, len(quotes) - live_count),
    }


@router.get("/api/watchlists/{list_id}/export")
def export_watchlist(list_id: str, request: Request, fmt: str = Query("csv")):
    uid = auth.require_uid(request)
    snap = _col(uid).document(list_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    d = snap.to_dict() or {}
    rows = [["Ticker", "Company", "Sector", "Last", "Day Chg %"]]
    quotes, _live_count = _quotes(_clean_symbols(d.get("tickers", [])), live=True)
    for q in quotes:
        rows.append([
            q["symbol"], q["name"], q["sector"],
            "" if q["price"] is None else q["price"],
            "" if q["chg"] is None else round(q["chg"], 2),
        ])
    safe = "".join(c if c.isalnum() else "_" for c in d.get("name", "watchlist")) or "watchlist"
    if fmt == "xls":
        html = "<table>" + "".join(
            "<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows
        ) + "</table>"
        return Response(
            "﻿" + html, media_type="application/vnd.ms-excel",
            headers={"Content-Disposition": f'attachment; filename="{safe}.xls"'},
        )
    buf = io.StringIO()
    csv.writer(buf).writerows(rows)
    return Response(
        "﻿" + buf.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{safe}.csv"'},
    )
