"""
Yahoo Finance Data Pipeline — Web GUI.

Serves a charting interface backed by Firestore data.

Usage:
    python app.py
    # or: uvicorn app:app --reload --port 8050
"""
import io
import re
import threading
from html import escape
from typing import Callable

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from pydantic import BaseModel, Field
import requests
from rich.console import Console

import storage
import terminal
from config import CEORATER_API_KEY

app = FastAPI(title="TEK2day Finance")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

COMMAND_LOCK = threading.Lock()
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,12}$")
MENU_SUBCMDS = {
    "inc": terminal.cmd_income,
    "bal": terminal.cmd_balance,
    "cf": terminal.cmd_cashflow,
    "mgmt": terminal.cmd_mgmt,
    "filings": terminal.cmd_filings,
    "news": terminal.cmd_news,
}


class CommandRequest(BaseModel):
    command: str = Field(..., min_length=1, max_length=160)
    width: int = Field(default=104, ge=72, le=140)


def _validate_symbol(symbol: str) -> str:
    symbol = symbol.upper()
    if not SYMBOL_RE.fullmatch(symbol):
        raise ValueError(f"Invalid ticker: {symbol}")
    return symbol


def _plain_output(text: str) -> dict:
    return {"output": text, "output_html": escape(text)}


def _capture_terminal(fn: Callable[[], None], width: int) -> dict:
    """Run a terminal command with Rich output captured for the browser."""
    buffer = io.StringIO()
    captured = Console(
        file=buffer,
        record=True,
        force_terminal=True,
        color_system="truecolor",
        width=width,
        legacy_windows=False,
    )
    table_width = max(72, min(width, 120))
    with COMMAND_LOCK:
        old_console = terminal.console
        old_table_width = terminal.TABLE_WIDTH
        terminal.console = captured
        terminal.TABLE_WIDTH = table_width
        try:
            fn()
        finally:
            terminal.console = old_console
            terminal.TABLE_WIDTH = old_table_width
    text = captured.export_text(styles=False, clear=False).strip()
    html = captured.export_html(inline_styles=True, code_format="{code}").strip()
    return {"output": text, "output_html": html}


def _run_terminal_command(line: str, width: int) -> dict:
    line = line.strip()
    if not line.startswith("/"):
        line = "/" + line

    parts = line[1:].split()
    if not parts:
        raise ValueError("Enter a command such as /AAPL or /comp AAPL MSFT")

    first = parts[0].lower()

    if first in ("help", "?"):
        output = _capture_terminal(terminal._print_banner, width)
        return {"command": "/help", "kind": "help", **output}

    if first in ("exit", "quit", "q"):
        return {
            "command": "/exit",
            "kind": "system",
            **_plain_output("This is the web wrapper. Close the browser tab to exit."),
        }

    if first == "comp":
        if len(parts) < 3:
            raise ValueError("Usage: /comp AAPL MSFT (up to 6 tickers)")
        if len(parts) > 7:
            raise ValueError("Maximum 6 tickers at a time.")
        symbols = [_validate_symbol(part) for part in parts[1:]]
        output = _capture_terminal(lambda: terminal.cmd_compare(symbols), width)
        return {
            "command": "/comp " + " ".join(symbols),
            "kind": "compare",
            "symbols": symbols,
            **output,
        }

    symbol = _validate_symbol(parts[0])
    subcmd = parts[1].lower() if len(parts) > 1 else None

    if len(parts) > 2:
        raise ValueError("Ticker commands accept one optional subcommand.")

    if subcmd is None:
        output = _capture_terminal(lambda: terminal.cmd_full(symbol), width)
        kind = "summary"
    elif subcmd in MENU_SUBCMDS:
        output = _capture_terminal(lambda: MENU_SUBCMDS[subcmd](symbol), width)
        kind = subcmd
    else:
        options = ", ".join(MENU_SUBCMDS)
        raise ValueError(f"Unknown subcommand: {subcmd}. Options: {options}")

    return {
        "command": f"/{symbol}" + (f" {subcmd}" if subcmd else ""),
        "kind": kind,
        "symbol": symbol,
        "subcommand": subcmd,
        **output,
    }


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


@app.post("/api/command")
def run_command(req: CommandRequest):
    """Run a whitelisted TEK2day terminal command and return captured text output."""
    try:
        return _run_terminal_command(req.command, req.width)
    except ValueError as exc:
        return {"command": req.command, "kind": "error", "error": str(exc), **_plain_output(str(exc))}
    except Exception as exc:
        return {
            "command": req.command,
            "kind": "error",
            "error": str(exc),
            **_plain_output(f"Command failed: {exc}"),
        }


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
