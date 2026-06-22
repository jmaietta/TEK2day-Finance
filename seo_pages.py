"""
Server-rendered, crawlable pages for TEK2day Finance.

Routes:
    GET /stock/{symbol}  - indexable stock page rendered from Firestore only
    GET /stocks          - A-Z directory of all active tickers (crawl hub)
    GET /about           - crawlable product/entity page for TEK2day Finance
    GET /sitemap.xml     - all active tickers
    GET /robots.txt

Design rules:
  - Firestore only: no live Yahoo calls, so crawler traffic never hits Yahoo.
  - Rendered HTML is cached in-process (PAGE_TTL) to keep Firestore reads flat
    under crawl load.
  - Unknown/inactive tickers return 404 so search engines do not index junk.
"""
import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape

import storage
import terminal

router = APIRouter()

BASE_URL = os.getenv("BASE_URL", "https://finance.tek2dayholdings.com").rstrip("/")
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,12}$")

_env = Environment(
    loader=FileSystemLoader(Path(__file__).parent / "templates"),
    autoescape=select_autoescape(["html"]),
)

PAGE_TTL = 3600        # seconds a rendered stock page stays cached
SITEMAP_TTL = 86400    # sitemap regenerates daily
DIRECTORY_TTL = 86400  # /stocks directory regenerates daily
_page_cache: dict[str, tuple[float, str]] = {}
_sitemap_cache: tuple[float, str] | None = None
_directory_cache: tuple[float, str] | None = None

INCOME_ROWS = [
    ("Total Revenue", "Revenue"),
    ("Gross Profit", "Gross Profit"),
    ("Operating Income", "Operating Income"),
    ("Net Income", "Net Income"),
    ("Diluted EPS", "Diluted EPS"),
]

CHART_DAYS = 252  # ~1 trading year of EOD closes


def _chart_svg(prices: list[dict], width: int = 920, height: int = 300) -> str | None:
    """One-year line chart of EOD closes as crawl-friendly inline SVG."""
    points = [(p["date"], p["close"]) for p in prices if p.get("close") is not None]
    if len(points) < 10:
        return None

    pad_l, pad_r, pad_t, pad_b = 56, 16, 18, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    closes = [c for _, c in points]
    lo, hi = min(closes), max(closes)
    span = (hi - lo) or 1.0
    lo -= span * 0.05
    hi += span * 0.05
    span = hi - lo

    n = len(points)

    def x(i):
        return pad_l + plot_w * i / (n - 1)

    def y(c):
        return pad_t + plot_h * (1 - (c - lo) / span)

    line = " ".join(f"{x(i):.1f},{y(c):.1f}" for i, (_, c) in enumerate(points))
    area = f"{pad_l:.1f},{pad_t + plot_h:.1f} {line} {x(n - 1):.1f},{pad_t + plot_h:.1f}"

    grid = []
    for frac in (0.0, 0.25, 0.5, 0.75, 1.0):
        gy = pad_t + plot_h * frac
        val = hi - span * frac
        grid.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" '
            f'stroke="#1c1b14" stroke-width="1"/>'
            f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" '
            f'fill="#8a8878" font-size="11">${val:,.0f}</text>'
        )

    xlabels = []
    for i in sorted(set([0, n // 4, n // 2, 3 * n // 4, n - 1])):
        d = points[i][0]
        # Anchor the edge labels inward so they are not clipped.
        anchor = "middle"
        if i == 0:
            anchor = "start"
        elif i == n - 1:
            anchor = "end"
        xlabels.append(
            f'<text x="{x(i):.1f}" y="{height - 8}" text-anchor="{anchor}" '
            f'fill="#8a8878" font-size="11">{d[:7]}</text>'
        )

    first, last = closes[0], closes[-1]
    chg = (last - first) / first * 100 if first else 0
    chg_color = "#2fd47f" if chg >= 0 else "#ff4d42"

    return f"""
<figure style="margin: 18px 0 6px;">
  <figcaption style="display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 6px;">
    <span style="font-family: var(--sans); font-weight: 700; color: var(--amber); font-size: 14px;">1-Year Price History</span>
    <span style="color: {chg_color}; font-size: 13px; font-weight: 600;">{chg:+.1f}% over period</span>
  </figcaption>
  <svg viewBox="0 0 {width} {height}" role="img" aria-label="One year price chart"
       style="width: 100%; height: auto; background: var(--panel); border: 1px solid var(--line); border-radius: 4px;">
    <defs>
      <linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="#ff9f1c" stop-opacity="0.22"/>
        <stop offset="100%" stop-color="#ff9f1c" stop-opacity="0"/>
      </linearGradient>
    </defs>
    {''.join(grid)}
    <polygon points="{area}" fill="url(#fade)"/>
    <polyline points="{line}" fill="none" stroke="#ff9f1c" stroke-width="1.8"/>
    <circle cx="{x(n - 1):.1f}" cy="{y(last):.1f}" r="3.2" fill="#ff9f1c"/>
    {''.join(xlabels)}
  </svg>
</figure>
"""


def _income_table(periods: list[dict]) -> dict | None:
    if not periods:
        return None
    rows = []
    for key, label in INCOME_ROWS:
        values = [p.get("income", {}).get(key) for p in periods]
        if not any(v is not None for v in values):
            continue
        if key == "Diluted EPS":
            rows.append({"label": label, "values": [terminal._price(v) for v in values]})
        else:
            rows.append({"label": label, "values": [terminal._fin(v) for v in values]})
    if not rows:
        return None
    return {
        "periods": [str(p.get("period_end", p.get("period", ""))) for p in periods],
        "rows": rows,
    }


def _build_page(symbol: str) -> str | None:
    meta = storage.get_ticker_meta(symbol)
    if not meta or meta.get("active") is False:
        return None

    name = meta.get("long_name") or meta.get("name") or symbol

    # EOD price history from Firestore (no live quote). One query feeds the
    # latest price, the daily change, and the 1-year chart.
    price = None
    price_date = None
    change_pct = None
    close = None
    history = storage.get_prices_history(symbol, limit=CHART_DAYS)
    if history:
        latest = history[-1]
        close = latest.get("close")
        price = terminal._price(close)
        price_date = latest.get("date")
        if len(history) > 1 and history[-2].get("close"):
            prev = history[-2]["close"]
            if prev:
                change_pct = (close - prev) / prev * 100 if close is not None else None
    chart = _chart_svg(history) if history else None

    # Financials from Firestore.
    all_fins = storage.get_all_financials(symbol)
    quarterly_periods = sorted(
        [f for f in all_fins if f.get("freq") != "FY"],
        key=lambda f: f.get("period_end", ""),
    )[-4:]
    annual_periods = sorted(
        [f for f in all_fins if f.get("freq") == "FY"],
        key=lambda f: f.get("period_end", ""),
    )[-4:]

    # TTM aggregates for valuation ratios (need a full 4 quarters).
    ttm_revenue = None
    ttm_eps = None
    if len(quarterly_periods) == 4:
        revs = [p.get("income", {}).get("Total Revenue") for p in quarterly_periods]
        epss = [p.get("income", {}).get("Diluted EPS") for p in quarterly_periods]
        if all(v is not None for v in revs):
            ttm_revenue = sum(revs)
        if all(v is not None for v in epss):
            ttm_eps = sum(epss)

    shares = meta.get("shares_outstanding")
    market_cap = close * shares if (close is not None and shares) else meta.get("market_cap")

    metrics = []

    def add(label, value):
        if value not in (None, "N/A", ""):
            metrics.append({"label": label, "value": value})

    add("Market Cap", terminal._dollar(market_cap))
    add("Shares Outstanding", terminal._count(shares))
    if market_cap and ttm_revenue:
        add("P/S (TTM)", terminal._ratio(market_cap / ttm_revenue))
    if close is not None and ttm_eps and ttm_eps > 0:
        add("P/E (TTM)", terminal._ratio(close / ttm_eps))
    add("Beta", terminal._num(meta.get("beta")))
    add("Dividend Yield", terminal._pct(meta.get("dividend_yield")))
    add("Exchange", meta.get("exchange") or None)
    add("Currency", meta.get("currency") or None)

    short_interest = []
    si_pairs = [
        ("Shares Short", terminal._count(meta.get("shares_short"))),
        ("Short Ratio", terminal._num(meta.get("short_ratio"))),
        ("Short % of Float", terminal._pct(meta.get("short_percent_of_float"))),
    ]
    si_date = meta.get("date_short_interest")
    if isinstance(si_date, (int, float)):
        si_date = datetime.fromtimestamp(si_date).strftime("%Y-%m-%d")
    if si_date:
        si_pairs.append(("As of", str(si_date)))
    for label, value in si_pairs:
        if value not in (None, "N/A", ""):
            short_interest.append({"label": label, "value": value})
    if not any(m["label"] != "As of" for m in short_interest):
        short_interest = []

    template = _env.get_template("stock.html")
    return template.render(
        base_url=BASE_URL,
        symbol=symbol,
        name=name,
        exchange=meta.get("exchange") or "US Markets",
        sector=meta.get("sector") or "",
        industry=meta.get("industry") or "",
        price=price,
        price_date=price_date,
        change_pct=change_pct,
        chart=chart,
        metrics=metrics,
        summary=(meta.get("summary") or "").strip() or None,
        quarterly=_income_table(quarterly_periods),
        annual=_income_table(annual_periods),
        short_interest=short_interest,
        year=date.today().year,
    )


@router.get("/about", response_class=HTMLResponse)
def about_page():
    html = _env.get_template("about.html").render(
        base_url=BASE_URL,
        year=date.today().year,
    )
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/stock/{symbol}", response_class=HTMLResponse)
def stock_page(symbol: str):
    upper = symbol.upper()
    if not SYMBOL_RE.fullmatch(upper):
        raise HTTPException(status_code=404, detail="Unknown ticker")
    if symbol != upper:
        # One canonical URL per ticker: /stock/aapl -> /stock/AAPL.
        return RedirectResponse(f"/stock/{upper}", status_code=301)

    now = time.time()
    cached = _page_cache.get(upper)
    if cached and cached[0] > now:
        return HTMLResponse(cached[1], headers={"Cache-Control": "public, max-age=3600"})

    html = _build_page(upper)
    if html is None:
        raise HTTPException(status_code=404, detail="Unknown ticker")

    _page_cache[upper] = (now + PAGE_TTL, html)
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/stocks", response_class=HTMLResponse)
def stocks_directory():
    """A-Z directory of all active tickers: the crawl hub linking / to every
    /stock/{symbol} page so none of them are sitemap-only orphans."""
    global _directory_cache
    now = time.time()
    if _directory_cache and _directory_cache[0] > now:
        return HTMLResponse(_directory_cache[1], headers={"Cache-Control": "public, max-age=3600"})

    db = storage.get_db()
    docs = (
        db.collection(storage.COLLECTION_ROOT)
        .where("active", "==", True)
        .select(["long_name", "name"])
        .stream()
    )
    tickers = sorted(
        (
            {"symbol": doc.id, "name": (d.get("long_name") or d.get("name") or doc.id)}
            for doc in docs
            if (d := doc.to_dict()) is not None
        ),
        key=lambda t: t["symbol"],
    )

    groups: list[tuple[str, list[dict]]] = []
    for t in tickers:
        letter = t["symbol"][0] if t["symbol"][0].isalpha() else "#"
        if not groups or groups[-1][0] != letter:
            groups.append((letter, []))
        groups[-1][1].append(t)

    html = _env.get_template("stocks.html").render(
        base_url=BASE_URL,
        count=len(tickers),
        groups=groups,
        year=date.today().year,
    )
    _directory_cache = (now + DIRECTORY_TTL, html)
    return HTMLResponse(html, headers={"Cache-Control": "public, max-age=3600"})


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap():
    global _sitemap_cache
    now = time.time()
    if _sitemap_cache and _sitemap_cache[0] > now:
        return Response(_sitemap_cache[1], media_type="application/xml")

    db = storage.get_db()
    docs = (
        db.collection(storage.COLLECTION_ROOT)
        .where("active", "==", True)
        .select([])
        .stream()
    )
    today = datetime.now(timezone.utc).date().isoformat()
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"<url><loc>{BASE_URL}/</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>1.0</priority></url>",
        f"<url><loc>{BASE_URL}/about</loc><lastmod>{today}</lastmod><changefreq>weekly</changefreq><priority>0.9</priority></url>",
        f"<url><loc>{BASE_URL}/stocks</loc><lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.8</priority></url>",
    ]
    for doc in sorted(docs, key=lambda d: d.id):
        lines.append(
            f"<url><loc>{BASE_URL}/stock/{doc.id}</loc>"
            f"<lastmod>{today}</lastmod><changefreq>daily</changefreq><priority>0.5</priority></url>"
        )
    lines.append("</urlset>")
    xml = "\n".join(lines)
    _sitemap_cache = (now + SITEMAP_TTL, xml)
    return Response(xml, media_type="application/xml")


@router.get("/robots.txt", include_in_schema=False)
def robots():
    return PlainTextResponse(
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /api/\n"
        f"Sitemap: {BASE_URL}/sitemap.xml\n"
    )
