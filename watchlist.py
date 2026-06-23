"""Watchlist API for TEK2day Finance — LOGIN-GATED, stored in Firestore
(users/{uid}/watchlists on the yfinance-cli project). Supports multiple named
lists. Quotes + export reuse the existing market-data storage. No Kilby code."""
import csv
import io
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

import auth
import storage

router = APIRouter()


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


def _quote(sym: str) -> dict:
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
    }


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
def watchlist_quotes(request: Request, symbols: str = Query("")):
    auth.require_uid(request)
    syms = [s.upper().strip() for s in symbols.split(",") if s.strip()][:500]
    return {"quotes": [_quote(s) for s in syms]}


@router.get("/api/watchlists/{list_id}/export")
def export_watchlist(list_id: str, request: Request, fmt: str = Query("csv")):
    uid = auth.require_uid(request)
    snap = _col(uid).document(list_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="Watchlist not found")
    d = snap.to_dict() or {}
    rows = [["Ticker", "Company", "Sector", "Last", "Day Chg %"]]
    for s in d.get("tickers", []):
        q = _quote(s)
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
