"""
Yahoo Finance Data Pipeline — Web GUI.

Serves a charting interface backed by Firestore data.

Usage:
    python app.py
    # or: uvicorn app:app --reload --port 8050
"""
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

import requests
import storage
from config import CEORATER_API_KEY

app = FastAPI(title="YFinance Data Pipeline")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/search")
def search_tickers(q: str = Query(..., min_length=1)):
    """Search tickers by symbol prefix."""
    db = storage.get_db()
    q = q.upper()
    docs = (
        db.collection(storage.COLLECTION_ROOT)
        .where("active", "==", True)
        .where("symbol", ">=", q)
        .where("symbol", "<=", q + "￿")
        .limit(15)
        .stream()
    )
    results = []
    for doc in docs:
        d = doc.to_dict()
        results.append({
            "symbol": d.get("symbol", doc.id),
            "name": d.get("name", ""),
            "sector": d.get("sector", ""),
        })
    return results


@app.get("/api/prices/{symbol}")
def get_prices(symbol: str, limit: int = Query(default=1260, le=2000)):
    """Get historical prices for a ticker. Default 1260 = ~5 years of trading days."""
    symbol = symbol.upper()
    db = storage.get_db()
    docs = (
        db.collection(storage.COLLECTION_ROOT)
        .document(symbol)
        .collection("prices")
        .order_by("date")
        .limit(limit)
        .stream()
    )
    prices = []
    for doc in docs:
        d = doc.to_dict()
        prices.append({
            "time": d.get("date"),
            "open": d.get("open"),
            "high": d.get("high"),
            "low": d.get("low"),
            "close": d.get("close"),
            "volume": d.get("volume"),
        })
    return prices


@app.get("/api/estimates/{symbol}")
def get_estimates(symbol: str, limit: int = Query(default=90, le=365)):
    """Get estimate history for a ticker."""
    symbol = symbol.upper()
    history = storage.get_estimate_history(symbol, limit=limit)
    return history


@app.get("/api/financials/{symbol}")
def get_financials(symbol: str):
    """Get quarterly financials for a ticker."""
    symbol = symbol.upper()
    db = storage.get_db()
    docs = (
        db.collection(storage.COLLECTION_ROOT)
        .document(symbol)
        .collection("financials")
        .order_by("period_end")
        .stream()
    )
    return [doc.to_dict() for doc in docs]


@app.get("/api/ticker/{symbol}")
def get_ticker_info(symbol: str):
    """Get ticker metadata."""
    symbol = symbol.upper()
    meta = storage.get_ticker_meta(symbol)
    if not meta:
        return {"error": "Ticker not found"}
    return meta


CEORATER_ALIASES = {"GOOG": "GOOGL", "BRK.A": "BRK.B"}


@app.get("/api/ceo/{symbol}")
def get_ceo(symbol: str):
    symbol = symbol.upper()
    if not CEORATER_API_KEY:
        return {"error": "CEORater not configured"}
    lookup = CEORATER_ALIASES.get(symbol, symbol)
    try:
        resp = requests.get(
            f"https://api.ceorater.com/v1/ceo/{lookup}",
            headers={"Authorization": f"Bearer {CEORATER_API_KEY}"},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            return data if isinstance(data, list) else [data]
        return {"error": f"CEORater returned {resp.status_code}"}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8050)
