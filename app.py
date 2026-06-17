"""
Yahoo Finance Data Pipeline — Web GUI.

Serves a charting interface backed by Firestore data.

Usage:
    python app.py
    # or: uvicorn app:app --reload --port 8050
"""
import io
import math
import re
import threading
import time
from datetime import datetime
from html import escape
from typing import Callable

from fastapi import FastAPI, Query, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
    Response,
)
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from pydantic import BaseModel, Field
import requests
from rich.console import Console

import auth
import storage
import terminal
import watchlist
from config import CEORATER_API_KEY
from seo_pages import BASE_URL, router as seo_router

def _json_safe(value):
    """Recursively replace NaN/Inf floats with None so Starlette's JSONResponse
    (which serializes with allow_nan=False) can encode Yahoo-sourced numbers.
    Yahoo returns NaN for inapplicable line items (e.g. preferred dividends),
    which otherwise raises and returns HTTP 500."""
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


class SafeJSONResponse(JSONResponse):
    """App-wide default JSON response: coerces NaN/Inf -> null before encoding."""

    def render(self, content):
        return super().render(_json_safe(content))


app = FastAPI(title="TEK2day Finance", default_response_class=SafeJSONResponse)
app.include_router(seo_router)
app.include_router(auth.router)
app.include_router(watchlist.router)


@app.middleware("http")
async def canonical_host_redirect(request, call_next):
    """301 the default Cloud Run hostname to the canonical domain so search
    engines index exactly one copy of the site."""
    host = request.headers.get("host", "").split(":")[0]
    if host.endswith(".run.app"):
        url = f"{BASE_URL}{request.url.path}"
        if request.url.query:
            url += f"?{request.url.query}"
        return RedirectResponse(url, status_code=301)
    return await call_next(request)


# ───── hardening: per-IP rate limit on /api/* + security headers ─────

RATE_CAPACITY = 30   # burst size (typeahead fires one request per keystroke)
RATE_REFILL = 2.0    # tokens/second; sustained 120 requests/min per IP
RATE_MAX_IPS = 10_000

_rate_buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_ts)
_rate_lock = threading.Lock()

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://www.googletagmanager.com https://www.gstatic.com https://apis.google.com; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://*.google-analytics.com https://*.googletagmanager.com https://*.googleusercontent.com; "
        "connect-src 'self' https://*.google-analytics.com https://*.analytics.google.com https://*.googletagmanager.com https://identitytoolkit.googleapis.com https://securetoken.googleapis.com https://www.googleapis.com; "
        "frame-src 'self' https://yfinance-cli.firebaseapp.com https://accounts.google.com; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    ),
}


def _client_ip(request) -> str:
    # Cloud Run appends the real client IP as the last entry.
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _rate_lock:
        if len(_rate_buckets) > RATE_MAX_IPS:
            _rate_buckets.clear()  # cheap reset; refills cost one burst per IP
        tokens, last = _rate_buckets.get(ip, (float(RATE_CAPACITY), now))
        tokens = min(float(RATE_CAPACITY), tokens + (now - last) * RATE_REFILL)
        if tokens < 1.0:
            _rate_buckets[ip] = (tokens, now)
            return True
        _rate_buckets[ip] = (tokens - 1.0, now)
        return False


@app.middleware("http")
async def security_middleware(request, call_next):
    if request.url.path.startswith("/api/") and _rate_limited(_client_ip(request)):
        return JSONResponse(
            {"detail": "Too many requests"},
            status_code=429,
            headers={"Retry-After": "1", **SECURITY_HEADERS},
        )
    response = await call_next(request)
    # The Firebase OAuth-handler proxy (/__/auth/*, /__/firebase/*) must carry its own
    # headers — our strict CSP + X-Frame-Options: DENY would break the handler's iframe.
    p = request.url.path
    if not (p.startswith("/__/auth/") or p.startswith("/__/firebase/")):
        for header, value in SECURITY_HEADERS.items():
            response.headers.setdefault(header, value)
    if request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response

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
    width: int = Field(default=104, ge=44, le=140)


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
    table_width = max(44, min(width, 120))
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


def _summary_payload(symbol: str) -> dict | None:
    snap = terminal._market_snapshot(symbol)
    if not snap:
        return None

    desc = terminal._company_summary(symbol, snap)
    estimates = _estimates_payload(symbol)
    short_interest = _short_interest_payload(symbol)

    return {
        "overview": {
            "symbol": symbol,
            "name": snap.get("name") or symbol,
            "price": terminal._price(snap.get("price")),
            "change": snap.get("change"),
            "change_pct": snap.get("change_pct"),
            "volume": terminal._count(snap.get("volume")),
            "description": desc or "",
            "source": "Yahoo Finance, TEK2day",
            "metrics": [
                {"label": "Market Cap", "value": terminal._dollar(snap.get("market_cap"))},
                {"label": "Diluted Shares", "value": terminal._count(snap.get("shares"))},
                {"label": "52wk High", "value": terminal._price(snap.get("fifty_two_week_high"))},
                {"label": "52wk Low", "value": terminal._price(snap.get("fifty_two_week_low"))},
                {"label": "Sector", "value": str(snap.get("sector") or "N/A")},
                {"label": "Industry", "value": str(snap.get("industry") or "N/A")},
                {"label": "Beta", "value": terminal._num(snap.get("beta"))},
                {"label": "P/E TTM (GAAP)", "value": terminal._ratio(snap.get("pe_ttm"))},
                {"label": "Fwd P/E (Est)", "value": terminal._ratio(snap.get("forward_pe"))},
                {"label": "P/S (TTM)", "value": terminal._ratio(snap.get("ps_ttm"))},
                {"label": "EV/EBITDA (TTM)", "value": terminal._ratio(snap.get("ev_ebitda"))},
                {"label": "EV/Rev (TTM)", "value": terminal._ratio(snap.get("ev_revenue"))},
            ],
        },
        "estimates": estimates,
        "shortInterest": short_interest,
    }


def _estimates_payload(symbol: str) -> dict | None:
    history = terminal._estimate_history(symbol)
    if not history:
        return None

    data = history[0]
    sections = []
    for prefix, title in [("eps", "EPS Estimates"), ("rev", "Revenue Estimates")]:
        metric_map = {
            k[len(prefix) + 1:]: v
            for k, v in data.items()
            if k.startswith(f"{prefix}_")
        }
        if not metric_map:
            continue

        sample = next(iter(metric_map.values()))
        period_codes = list(sample.keys()) if isinstance(sample, dict) else []
        periods = [p for p in terminal.PERIOD_ORDER if p in period_codes]
        periods += [p for p in period_codes if p not in periods]
        metric_order = terminal.METRIC_ORDER_REV if prefix == "rev" else terminal.METRIC_ORDER_EPS
        rows = []

        for metric_key in metric_order:
            if metric_key not in metric_map:
                continue
            values = []
            for period in periods:
                val = metric_map[metric_key].get(period)
                if metric_key == "growth":
                    values.append(terminal._pct(val))
                elif metric_key == "numberofanalysts":
                    values.append(str(int(val)) if val is not None else "N/A")
                elif prefix == "rev" and metric_key in ("avg", "high", "low", "yearagorevenue"):
                    values.append(terminal._dollar(val))
                elif prefix == "eps" and metric_key in ("avg", "high", "low", "yearagoeps"):
                    values.append(terminal._price(val))
                else:
                    values.append(terminal._num(val))
            rows.append({
                "label": terminal.METRIC_LABELS.get(metric_key, metric_key),
                "values": values,
            })

        sections.append({
            "title": title,
            "periods": [terminal.PERIOD_LABELS.get(p, p) for p in periods],
            "rows": rows,
        })

    return {
        "asOf": data.get("date", "unknown"),
        "source": "Yahoo Finance, TEK2day",
        "sections": sections,
    }


def _short_interest_payload(symbol: str) -> dict | None:
    meta = terminal._firestore_meta(symbol) or {}
    values = {
        "date": terminal._first_value(meta, ["date_short_interest", "dateShortInterest"]),
        "shares": terminal._first_value(meta, ["shares_short", "sharesShort"]),
        "ratio": terminal._first_value(meta, ["short_ratio", "shortRatio"]),
        "float_pct": terminal._first_value(meta, ["short_percent_of_float", "shortPercentOfFloat"]),
        "shares_out_pct": terminal._first_value(meta, ["shares_percent_shares_out", "sharesPercentSharesOut"]),
    }
    if not any(v is not None for v in values.values()):
        info = terminal._yahoo(symbol)
        values = {
            "date": info.get("dateShortInterest"),
            "shares": info.get("sharesShort"),
            "ratio": info.get("shortRatio"),
            "float_pct": info.get("shortPercentOfFloat"),
            "shares_out_pct": info.get("sharesPercentSharesOut"),
        }
    if not any(v is not None for v in values.values()):
        return None

    short_date = values["date"]
    if isinstance(short_date, (int, float)):
        short_date = datetime.fromtimestamp(short_date).strftime("%Y-%m-%d")

    return {
        "title": "Short Interest",
        "source": "Yahoo Finance, TEK2day",
        "metrics": [
            {"label": "Shares Short", "value": terminal._count(values["shares"])},
            {"label": "Short Ratio", "value": terminal._num(values["ratio"])},
            {"label": "Short % of Float", "value": terminal._pct(values["float_pct"])},
            {"label": "Short % of Shares Out", "value": terminal._pct(values["shares_out_pct"])},
            {"label": "As of", "value": str(short_date or "N/A")},
        ],
    }


def _field_parts(field) -> tuple[str, str]:
    if isinstance(field, tuple):
        return field
    return field, field


def _statement_rows(periods: list[dict], section: str, fields: list) -> list[dict]:
    rows = []
    for field in fields:
        key, label = _field_parts(field)
        values = [p.get(section, {}).get(key) for p in periods]
        if not any(v is not None for v in values):
            continue
        rows.append({
            "label": label,
            "values": [terminal._fin(v) for v in values],
        })

    if rows:
        return rows

    all_keys = set()
    for period in periods:
        all_keys.update(period.get(section, {}).keys())
    for key in sorted(all_keys):
        values = [p.get(section, {}).get(key) for p in periods]
        rows.append({
            "label": key,
            "values": [terminal._fin(v) for v in values],
        })
    return rows


def _financial_payload(symbol: str, section: str, fields: list, title: str) -> dict | None:
    all_fins = terminal._all_financials(symbol)
    if not all_fins:
        return None

    sections = []
    if section == "balance_sheet":
        seen = set()
        unique = []
        for financial in sorted(all_fins, key=lambda f: f.get("period_end", "")):
            period_end = financial.get("period_end", "")
            if period_end in seen:
                continue
            seen.add(period_end)
            unique.append(financial)
        periods = unique[-8:]
        if periods:
            sections.append({
                "title": "Recent Periods",
                "periods": [str(p.get("period_end", p.get("period", ""))) for p in periods],
                "rows": _statement_rows(periods, section, fields),
            })
    else:
        quarterly = sorted(
            [f for f in all_fins if f.get("freq") != "FY"],
            key=lambda f: f.get("period_end", ""),
        )[-4:]
        annual = sorted(
            [f for f in all_fins if f.get("freq") == "FY"],
            key=lambda f: f.get("period_end", ""),
        )[-4:]
        for label, periods in [("Quarterly", quarterly), ("Annual", annual)]:
            if not periods:
                continue
            sections.append({
                "title": label,
                "periods": [str(p.get("period_end", p.get("period", ""))) for p in periods],
                "rows": _statement_rows(periods, section, fields),
            })

    if not sections:
        return None
    return {
        "type": "financials",
        "symbol": symbol,
        "title": title,
        "source": "TEK2day Firestore",
        "sections": sections,
    }


def _management_payload(symbol: str) -> dict | None:
    ceo_data = terminal._get_ceorater(symbol)
    if ceo_data:
        leaders = []
        for ceo in ceo_data:
            founder = ceo.get("Founder (Y/N)") or ceo.get("founderCEO")
            metrics = [
                {"label": "CEO", "value": str(ceo.get("CEO Name") or ceo.get("ceo") or "N/A")},
                {"label": "Founder CEO", "value": "Yes" if founder in (True, "Y") else "No"},
                {"label": "Tenure", "value": str(ceo.get("Tenure (years)") or ceo.get("tenure") or "N/A")},
                {"label": "CEORater Score", "value": terminal._num(ceo.get("CEORaterScore") or ceo.get("ceoraterScore"), 0)},
                {"label": "Alpha Score", "value": terminal._num(ceo.get("AlphaScore") or ceo.get("alphaScore"), 0)},
                {"label": "Comp Score", "value": str(ceo.get("CompScore") or ceo.get("compScore") or "N/A")},
                {"label": "Revenue CAGR", "value": str(ceo.get("Revenue CAGR (Adj.)") or "N/A")},
                {"label": "TSR During Tenure", "value": str(ceo.get("TSR During Tenure") or "N/A")},
                {"label": "Avg Annual TSR", "value": str(ceo.get("Avg. Annual TSR") or "N/A")},
                {"label": "TSR vs SPY", "value": str(ceo.get("TSR vs. SPY") or "N/A")},
                {"label": "Compensation", "value": str(ceo.get("Compensation ($ millions)") or ceo.get("compensationMM") or "N/A")},
            ]
            leaders.append({"metrics": metrics})
        return {
            "type": "management",
            "symbol": symbol,
            "title": "Management / CEO",
            "source": "CEORater",
            "leaders": leaders,
        }

    info = terminal._yahoo(symbol) or {}
    officers = info.get("companyOfficers", [])
    if not officers:
        return None
    return {
        "type": "management",
        "symbol": symbol,
        "title": "Management / Officers",
        "source": "Yahoo Finance",
        "officers": [
            {
                "name": str(officer.get("name", "")),
                "title": str(officer.get("title", "")),
                "age": str(officer.get("age", "") or "N/A"),
                "totalPay": terminal._dollar(officer.get("totalPay")) if officer.get("totalPay") else "N/A",
            }
            for officer in officers[:10]
        ],
    }


# Per-ticker filings cache (per instance). Also cuts SEC request volume, which is
# what triggers EDGAR's rate limiting in the first place.
_FILINGS_CACHE: dict = {}            # symbol -> (expires_ts, payload)
_FILINGS_TTL = 3600                  # 1 hour


def _fetch_filings_from_sec(symbol: str, cik: str) -> dict | None:
    """One SEC EDGAR submissions fetch -> filings payload, or None. Retries
    transient failures (EDGAR rate-limits aggressively)."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    resp = None
    for attempt in range(1, 4):
        try:
            resp = requests.get(url, headers=terminal.SEC_HEADERS, timeout=15)
            if resp.status_code == 200:
                break
        except Exception:
            resp = None
        time.sleep(0.6 * attempt)
    if resp is None or resp.status_code != 200:
        return None

    try:
        recent = resp.json().get("filings", {}).get("recent", {})
    except Exception:
        return None
    forms = recent.get("form", [])
    if not forms:
        return None
    dates = recent.get("filingDate", [])
    descs = recent.get("primaryDocDescription", [])
    accessions = recent.get("accessionNumber", [])
    primary_docs = recent.get("primaryDocument", [])

    filings = []
    for idx in range(min(15, len(forms))):
        accession = accessions[idx] if idx < len(accessions) else ""
        primary_doc = primary_docs[idx] if idx < len(primary_docs) else ""
        archive_path = accession.replace("-", "")
        url_doc = ""
        if accession and primary_doc:
            url_doc = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{archive_path}/{primary_doc}"
        filings.append({
            "date": dates[idx] if idx < len(dates) else "",
            "form": forms[idx],
            "description": descs[idx] if idx < len(descs) else "",
            "accession": accession,
            "url": url_doc,
        })

    return {
        "type": "filings",
        "symbol": symbol,
        "title": "Recent SEC Filings",
        "source": "SEC EDGAR",
        "filings": filings,
    }


def _filings_payload(symbol: str) -> dict | None:
    terminal._load_cik_cache()
    cik = terminal._cik_cache.get(symbol)
    if not cik:
        return None  # genuinely not in SEC's ticker map

    now = time.time()
    cached = _FILINGS_CACHE.get(symbol)
    if cached and cached[0] > now:
        return cached[1]

    try:
        payload = _fetch_filings_from_sec(symbol, cik)
    except Exception:
        payload = None

    if payload:
        _FILINGS_CACHE[symbol] = (now + _FILINGS_TTL, payload)
        return payload
    # Transient SEC failure after retries: serve stale cache if we have one,
    # so a momentary throttle doesn't blank the panel.
    return cached[1] if cached else None


def _news_payload(symbol: str) -> dict | None:
    ticker = terminal._yf().Ticker(symbol)
    news = ticker.news
    if not news:
        return None

    items = []
    for item in news[:10]:
        content = item.get("content", item)
        provider = content.get("provider", {})
        publisher = provider.get("displayName", "") if isinstance(provider, dict) else str(provider)
        click_url = content.get("clickThroughUrl", {})
        canonical_url = content.get("canonicalUrl", {})
        link = ""
        if isinstance(click_url, dict):
            link = click_url.get("url", "")
        if not link and isinstance(canonical_url, dict):
            link = canonical_url.get("url", "")
        if not link:
            link = content.get("link", "")

        pub_date = content.get("pubDate", "")
        date_str = ""
        if isinstance(pub_date, str) and pub_date:
            date_str = pub_date[:16].replace("T", " ")
        elif isinstance(pub_date, (int, float)):
            date_str = datetime.fromtimestamp(pub_date).strftime("%Y-%m-%d %H:%M")

        items.append({
            "title": str(content.get("title", "")),
            "publisher": publisher,
            "date": date_str,
            "url": link,
        })

    return {
        "type": "news",
        "symbol": symbol,
        "title": "Recent News",
        "source": "Yahoo Finance",
        "items": items,
    }


def _compare_payload(symbols: list[str]) -> dict | None:
    snapshots = {}
    for symbol in symbols[:6]:
        snap = terminal._market_snapshot(symbol)
        if snap:
            snapshots[symbol] = snap
    if not snapshots:
        return None

    metrics = [
        ("Price", lambda item: terminal._price(item.get("price"))),
        ("Market Cap", lambda item: terminal._dollar(item.get("market_cap"))),
        ("EV", lambda item: terminal._dollar(item.get("enterprise_value"))),
        ("Revenue (TTM)", lambda item: terminal._dollar(item.get("revenue"))),
        ("EBITDA (TTM)", lambda item: terminal._dollar(item.get("ebitda"))),
        ("Net Income (TTM)", lambda item: terminal._dollar(item.get("net_income"))),
        ("EPS (TTM)", lambda item: terminal._num(item.get("eps_ttm"))),
        ("EPS (Fwd)", lambda item: terminal._num(item.get("forward_eps"))),
        ("P/E TTM (GAAP)", lambda item: terminal._ratio(item.get("pe_ttm"))),
        ("Fwd P/E (Est)", lambda item: terminal._ratio(item.get("forward_pe"))),
        ("P/S (TTM)", lambda item: terminal._ratio(item.get("ps_ttm"))),
        ("EV/Rev (TTM)", lambda item: terminal._ratio(item.get("ev_revenue"))),
        ("EV/EBITDA (TTM)", lambda item: terminal._ratio(item.get("ev_ebitda"))),
        ("EV/OpCF", lambda item: terminal._ratio(item.get("ev_opcf"))),
        ("EV/FCF", lambda item: terminal._ratio(item.get("ev_fcf"))),
    ]
    ordered_symbols = list(snapshots.keys())
    return {
        "type": "compare",
        "title": "Comparison",
        "source": "Yahoo Finance, TEK2day",
        "symbols": [
            {"symbol": symbol, "name": snapshots[symbol].get("name", symbol)}
            for symbol in ordered_symbols
        ],
        "rows": [
            {
                "label": label,
                "values": [formatter(snapshots[symbol]) for symbol in ordered_symbols],
            }
            for label, formatter in metrics
        ],
    }


def _web_payload(kind: str, symbol: str | None = None, symbols: list[str] | None = None) -> dict | None:
    try:
        if kind == "summary" and symbol:
            return _summary_payload(symbol)
        if kind == "inc" and symbol:
            return _financial_payload(symbol, "income", terminal.INCOME_FIELDS, "Income Statement")
        if kind == "bal" and symbol:
            return _financial_payload(symbol, "balance_sheet", terminal.BALANCE_FIELDS, "Balance Sheet")
        if kind == "cf" and symbol:
            return _financial_payload(symbol, "cash_flow", terminal.CASHFLOW_FIELDS, "Cash Flow")
        if kind == "mgmt" and symbol:
            return _management_payload(symbol)
        if kind == "filings" and symbol:
            return _filings_payload(symbol)
        if kind == "news" and symbol:
            return _news_payload(symbol)
        if kind == "compare" and symbols:
            return _compare_payload(symbols)
    except Exception:
        return None
    return None


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
        data = _web_payload("compare", symbols=symbols)
        # The browser renders the structured payload natively; only fall back
        # to the captured terminal text when no payload could be built. This
        # avoids fetching all data twice and skips the global COMMAND_LOCK.
        output = (
            _plain_output("")
            if data is not None
            else _capture_terminal(lambda: terminal.cmd_compare(symbols), width)
        )
        return {
            "command": "/comp " + " ".join(symbols),
            "kind": "compare",
            "symbols": symbols,
            "data": data,
            **output,
        }

    symbol = _validate_symbol(parts[0])
    subcmd = parts[1].lower() if len(parts) > 1 else None

    if len(parts) > 2:
        raise ValueError("Ticker commands accept one optional subcommand.")

    if subcmd is None:
        kind = "summary"
        runner = lambda: terminal.cmd_full(symbol)
    elif subcmd in MENU_SUBCMDS:
        kind = subcmd
        runner = lambda: MENU_SUBCMDS[subcmd](symbol)
    else:
        options = ", ".join(MENU_SUBCMDS)
        raise ValueError(f"Unknown subcommand: {subcmd}. Options: {options}")

    data = _web_payload(kind, symbol=symbol)
    # The browser renders the structured payload natively; only fall back to
    # the captured terminal text when no payload could be built. This avoids
    # fetching all data twice and skips the global COMMAND_LOCK.
    output = _plain_output("") if data is not None else _capture_terminal(runner, width)

    return {
        "command": f"/{symbol}" + (f" {subcmd}" if subcmd else ""),
        "kind": kind,
        "symbol": symbol,
        "subcommand": subcmd,
        "data": data,
        **output,
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/auth/action", response_class=HTMLResponse, include_in_schema=False)
def auth_action():
    # Branded Firebase email-action handler (password reset, email verify/recover).
    # Set as the "custom action URL" in Firebase Console so links + this page are
    # TEK2day-branded (finance.tek2dayholdings.com) instead of *.firebaseapp.com.
    return (STATIC_DIR / "auth-action.html").read_text(encoding="utf-8")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon")


@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(
        STATIC_DIR / "manifest.webmanifest",
        media_type="application/manifest+json",
    )


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(STATIC_DIR / "sw.js", media_type="text/javascript")


# --- Same-origin proxy for Firebase's OAuth handler (Google sign-in on mobile/PWA) ---
# Serves Firebase's reserved auth-helper paths from THIS origin so the browser treats the
# handler's storage as first-party instead of cross-site (*.firebaseapp.com). Additive and
# inert until FIREBASE_AUTH_DOMAIN is pointed at this host. See STATUS.md (reverse-proxy plan).
_FB_HANDLER_ORIGIN = "https://yfinance-cli.firebaseapp.com"
_FB_PASS_RESP_HEADERS = ("location", "cache-control", "etag", "last-modified", "expires", "vary")
_FB_FWD_REQ_HEADERS = ("cookie", "user-agent", "accept", "accept-language", "referer")


@app.api_route("/__/auth/{_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
@app.api_route("/__/firebase/{_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
def firebase_auth_proxy(_path: str, request: Request):
    upstream = _FB_HANDLER_ORIGIN + request.url.path
    if request.url.query:
        upstream += "?" + request.url.query
    fwd = {h: request.headers[h] for h in _FB_FWD_REQ_HEADERS if h in request.headers}
    try:
        up = requests.request(
            request.method, upstream, headers=fwd, allow_redirects=False, timeout=15
        )
    except requests.RequestException:
        return Response(status_code=502)
    resp = Response(
        content=up.content,
        status_code=up.status_code,
        media_type=up.headers.get("content-type"),
    )
    for h in _FB_PASS_RESP_HEADERS:
        if h in up.headers:
            resp.headers[h] = up.headers[h]
    # Re-scope any handler cookies to this host (strip the cross-site Domain attribute).
    try:
        raw_cookies = up.raw.headers.getlist("Set-Cookie")
    except Exception:
        sc = up.headers.get("Set-Cookie")
        raw_cookies = [sc] if sc else []
    for sc in raw_cookies:
        resp.headers.append("set-cookie", re.sub(r";\s*[Dd]omain=[^;]+", "", sc))
    return resp


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


def _live_bar(symbol: str) -> dict | None:
    """The chart's last point, built from the SAME cached quote the summary
    header uses (terminal._live_quote, 30s TTL) — one Yahoo read total."""
    quote = terminal._live_quote(symbol)
    if quote.get("price") is None or not quote.get("date"):
        return None
    volume = quote.get("volume")
    return {
        "date": quote["date"],
        "open": None,
        "high": quote.get("day_high"),
        "low": quote.get("day_low"),
        "close": quote["price"],
        "volume": int(volume) if volume is not None else None,
    }


@app.get("/api/prices/{symbol}")
def get_prices(symbol: str, limit: int = Query(default=1260, le=2000)):
    """Get historical prices for a ticker. Default 1260 = ~5 years of trading days."""
    symbol = symbol.upper()

    def clean(value):
        # Older rows may contain NaN, which is not JSON-serializable.
        if isinstance(value, float) and math.isnan(value):
            return None
        return value

    # Newest-first then re-sorted ascending, so the limit keeps the most
    # recent rows (ascending order_by + limit would drop the newest days
    # once history exceeds the limit).
    rows = [
        {
            "time": d.get("date"),
            "open": clean(d.get("open")),
            "high": clean(d.get("high")),
            "low": clean(d.get("low")),
            "close": clean(d.get("close")),
            "volume": clean(d.get("volume")),
        }
        for d in storage.get_prices_history(symbol, limit)
    ]

    # Merge the official live quote so the chart's last point always matches
    # the summary header, even before the EOD bar reaches Firestore.
    live = _live_bar(symbol)
    if live and rows:
        live_row = {
            "time": live["date"],
            "open": live["open"],
            "high": live["high"],
            "low": live["low"],
            "close": live["close"],
            "volume": live["volume"],
        }
        if live_row["time"] > rows[-1]["time"]:
            rows.append(live_row)
        elif live_row["time"] == rows[-1]["time"]:
            stored = rows[-1]
            # Live quote wins for price fields; keep the stored open (the
            # metadata has no official open).
            stored.update({k: v for k, v in live_row.items() if v is not None and k != "open"})
    return rows


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
