#!/usr/bin/env python3
"""
TEK2day Finance — Interactive Terminal

Usage:
    tek2day
"""
import io
import os
import sys
from datetime import datetime

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import readline
except ImportError:
    pass

__version__ = "0.1.1"

import yfinance as yf
import requests
from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from config import CEORATER_API_KEY

console = Console()
TABLE_WIDTH = 80

# ── Firestore (optional — live commands work without it) ───────────────────

_firestore = None

try:
    import storage
except ImportError:
    storage = None

def _has_firestore():
    global _firestore
    if _firestore is None:
        try:
            storage.get_db()
            _firestore = True
        except Exception:
            _firestore = False
    return _firestore

# ── CEORater ───────────────────────────────────────────────────────────────

CEORATER_ALIASES = {"GOOG": "GOOGL", "BRK.A": "BRK.B"}
SEC_HEADERS = {
    "User-Agent": "TEK2day Finance support@tek2day.com",
    "Accept": "application/json",
}
_cik_cache = {}

# ── Formatting helpers ─────────────────────────────────────────────────────


def _dollar(val):
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if abs(v) >= 1e12:
            return f"${v / 1e12:,.2f}T"
        if abs(v) >= 1e9:
            return f"${v / 1e9:,.2f}B"
        if abs(v) >= 1e6:
            return f"${v / 1e6:,.1f}M"
        return f"${v:,.2f}"
    except (ValueError, TypeError):
        return "N/A"


def _count(val):
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if abs(v) >= 1e9:
            return f"{v / 1e9:,.2f}B"
        if abs(v) >= 1e6:
            return f"{v / 1e6:,.1f}M"
        if abs(v) >= 1e3:
            return f"{v / 1e3:,.0f}K"
        return f"{v:,.0f}"
    except (ValueError, TypeError):
        return "N/A"


def _pct(val):
    if val is None:
        return "N/A"
    try:
        return f"{float(val) * 100:.2f}%"
    except (ValueError, TypeError):
        return "N/A"


def _ratio(val):
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if abs(v) >= 100:
            return f"{v:,.0f}x"
        return f"{v:.1f}x"
    except (ValueError, TypeError):
        return "N/A"


def _safe_ratio(num, denom):
    if num is None or denom is None:
        return "N/A"
    try:
        n, d = float(num), float(denom)
        if d == 0:
            return "N/A"
        return f"{n / d:.2f}x"
    except (ValueError, TypeError):
        return "N/A"


def _num(val, decimals=2):
    if val is None:
        return "N/A"
    try:
        return f"{float(val):,.{decimals}f}"
    except (ValueError, TypeError):
        return str(val) if val else "N/A"


def _price(val):
    if val is None:
        return "N/A"
    try:
        return f"${float(val):,.2f}"
    except (ValueError, TypeError):
        return "N/A"


def _fin(val):
    if val is None:
        return ""
    try:
        v = float(val)
        if abs(v) >= 1e9:
            return f"{v / 1e9:,.1f}B"
        if abs(v) >= 1e6:
            return f"{v / 1e6:,.1f}M"
        if abs(v) >= 1e3:
            return f"{v / 1e3:,.0f}K"
        return f"{v:,.2f}"
    except (ValueError, TypeError):
        return str(val)


def _color(val):
    try:
        v = float(val)
        if v > 0:
            return "green"
        if v < 0:
            return "red"
    except (ValueError, TypeError):
        pass
    return "white"


# ── Version check ──────────────────────────────────────────────────────────

GITHUB_REPO = "jmaietta/TEK2day-Finance"


def _check_for_update():
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=3,
        )
        if resp.status_code == 200:
            latest = resp.json().get("tag_name", "").lstrip("v")
            if latest and latest != __version__:
                console.print(
                    f"[yellow]  Update available: v{latest} "
                    f"(you have v{__version__})[/yellow]"
                )
                console.print(
                    '[yellow]  Run: pip install --upgrade '
                    'git+https://github.com/jmaietta/TEK2day-Finance.git[/yellow]'
                )
                console.print()
    except Exception:
        pass


# ── Banner ─────────────────────────────────────────────────────────────────

HELP_TEXT = """\
[white]  /AAPL                     Summary
  /AAPL inc                 Income statement
  /AAPL bal                 Balance sheet
  /AAPL cf                  Cash flow
  /AAPL mgmt                Management / CEO
  /AAPL filings             SEC filings
  /AAPL news                Recent news
  /compare AAPL MSFT GOOGL  Comp table (up to 20)
  /help                     Show this menu
  /exit                     Quit[/white]"""


def _print_banner():
    console.print()
    console.print(Panel(
        Text("TEK2day Finance", justify="center", style="bold white"),
        subtitle=f"[grey70]v{__version__}[/grey70]",
        border_style="bold red",
        box=box.HEAVY,
        padding=(1, 4),
        width=42,
    ))
    console.print(HELP_TEXT)
    console.print()


# ── Live Yahoo ─────────────────────────────────────────────────────────────


def _yahoo(symbol):
    try:
        return yf.Ticker(symbol).info or {}
    except Exception as e:
        console.print(f"[red]Error fetching {symbol}: {e}[/red]")
        return {}


def _get_diluted_shares(symbol, info):
    if _has_firestore():
        try:
            fins = storage.get_all_financials(symbol)
            for f in fins:
                if f.get("freq") == "Q":
                    val = f.get("income", {}).get("Diluted Average Shares")
                    if val is not None:
                        return _count(val)
                    break
        except Exception:
            pass
    return _count(info.get("sharesOutstanding"))


# ── /AAPL — Overview + Valuation ───────────────────────────────────────────


def cmd_overview(symbol, info=None):
    if not info:
        console.print(f"[grey70]Fetching live data for {symbol}...[/grey70]")
        info = _yahoo(symbol)
    if not info or not info.get("shortName"):
        console.print(f"[red]{symbol}: no data found[/red]")
        return

    name = info.get("shortName", symbol)
    price = info.get("regularMarketPrice") or info.get("currentPrice")
    change = info.get("regularMarketChange")
    change_pct = info.get("regularMarketChangePercent")
    volume = info.get("regularMarketVolume")

    price_text = Text()
    price_text.append(f"  {_price(price)}  ", style="bold white")
    if change is not None and change_pct is not None:
        sign = "+" if change > 0 else ""
        c = _color(change)
        price_text.append(
            f"{sign}{change:,.2f} ({sign}{change_pct:,.2f}%)", style=f"bold {c}"
        )
    if volume:
        price_text.append(f"   Vol: {_count(volume)}", style="grey70")

    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column("", style="white", width=18)
    t.add_column("", style="white", width=16)
    t.add_column("", style="white", width=18)
    t.add_column("", style="white", width=14)

    rows = [
        ("Market Cap", _dollar(info.get("marketCap")),
         "P/E TTM (GAAP)", _ratio(info.get("trailingPE"))),
        ("Diluted Shares", _get_diluted_shares(symbol, info),
         "Fwd P/E (Est)", _ratio(info.get("forwardPE"))),
        ("52wk High", _price(info.get("fiftyTwoWeekHigh")),
         "P/S (TTM)", _ratio(info.get("priceToSalesTrailing12Months"))),
        ("52wk Low", _price(info.get("fiftyTwoWeekLow")),
         "EV/EBITDA (TTM)", _ratio(info.get("enterpriseToEbitda"))),
        ("Sector", str(info.get("sector", "N/A")),
         "EV/Rev (TTM)", _ratio(info.get("enterpriseToRevenue"))),
        ("Industry", str(info.get("industry", "N/A"))[:26],
         "", ""),
        ("Beta", _num(info.get("beta")),
         "", ""),
    ]
    for r in rows:
        t.add_row(*r)

    desc = info.get("longBusinessSummary", "")
    if desc and len(desc) > 220:
        desc = desc[:217] + "..."

    elements = [price_text, "", t]
    if desc:
        elements += ["", Text(desc, style="grey70")]

    console.print(Panel(
        Group(*elements),
        title=f"[bold white]{name}[/bold white] · [grey70]{symbol}[/grey70]",
        border_style="green",
        padding=(1, 2),
        width=TABLE_WIDTH,
    ))


# ── /AAPL est — Estimates ─────────────────────────────────────────────────

PERIOD_LABELS = {
    "0q": "Curr Q", "0y": "Curr Yr",
    "+1q": "Next Q", "+1y": "Next Yr",
    "plus1q": "Next Q", "plus1y": "Next Yr",
}

METRIC_ORDER_EPS = ["avg", "high", "low", "numberofanalysts", "yearagoeps", "growth"]
METRIC_ORDER_REV = ["avg", "high", "low", "numberofanalysts", "yearagorevenue", "growth"]

METRIC_LABELS = {
    "avg": "Consensus",
    "high": "High",
    "low": "Low",
    "numberofanalysts": "# Analysts",
    "yearagoeps": "Year Ago",
    "yearagorevenue": "Year Ago",
    "growth": "YoY Growth",
}

PERIOD_ORDER = ["0q", "+1q", "0y", "+1y"]


def cmd_estimates(symbol):
    if not _has_firestore():
        console.print("[yellow]Estimates require Firestore. Set FIRESTORE_PROJECT.[/yellow]")
        return

    history = storage.get_estimate_history(symbol, limit=1)
    if not history:
        console.print(f"[yellow]No stored estimates for {symbol}[/yellow]")
        return

    data = history[0]
    pull_date = data.get("date", "unknown")

    for prefix, title in [("eps", "EPS Estimates"), ("rev", "Revenue Estimates")]:
        metric_map = {}
        for k in data:
            if k.startswith(f"{prefix}_"):
                metric_code = k[len(prefix) + 1:]
                metric_map[metric_code] = data[k]

        if not metric_map:
            continue

        sample = next(iter(metric_map.values()))
        period_codes = list(sample.keys())

        ordered_periods = [p for p in PERIOD_ORDER if p in period_codes]
        for p in period_codes:
            if p not in ordered_periods:
                ordered_periods.append(p)

        t = Table(
            title=title, box=box.SIMPLE_HEAVY, border_style="green",
            title_style="bold", width=TABLE_WIDTH,
        )
        t.add_column("", style="bold", width=16)
        for p in ordered_periods:
            t.add_column(PERIOD_LABELS.get(p, p), justify="right", width=14)

        metric_order = METRIC_ORDER_REV if prefix == "rev" else METRIC_ORDER_EPS
        for mk in metric_order:
            if mk not in metric_map:
                continue
            label = METRIC_LABELS.get(mk, mk)
            row = [label]
            for p in ordered_periods:
                val = metric_map[mk].get(p)
                if mk == "growth":
                    row.append(_pct(val))
                elif mk == "numberofanalysts":
                    row.append(str(int(val)) if val is not None else "N/A")
                elif prefix == "rev" and mk in ("avg", "high", "low", "yearagorevenue"):
                    row.append(_dollar(val))
                elif prefix == "eps" and mk in ("avg", "high", "low", "yearagoeps"):
                    row.append(_price(val))
                else:
                    row.append(_num(val))
            t.add_row(*row)

        console.print(t)

    console.print(f"[grey70]  As of {pull_date} · Source: Yahoo Finance[/grey70]")


# ── Financial statement helpers ────────────────────────────────────────────

INCOME_FIELDS = [
    "Total Revenue", "Cost Of Revenue", "Gross Profit",
    "Operating Expense", "Operating Income", "EBITDA",
    "Interest Expense", "Pretax Income", "Tax Provision",
    "Net Income", "Net Income Common Stockholders",
    "Basic EPS", "Diluted EPS",
]

BALANCE_FIELDS = [
    "Cash And Cash Equivalents", "Short Term Investments",
    "Total Current Assets", "Net PPE", "Goodwill And Other Intangible Assets",
    "Total Assets",
    "Total Current Liabilities", "Long Term Debt", "Total Debt",
    "Total Liabilities Net Minority Interest",
    "Common Stock Equity", "Total Equity Gross Minority Interest",
    "Total Capitalization",
]

CASHFLOW_FIELDS = [
    "Operating Cash Flow", "Capital Expenditure", "Free Cash Flow",
    "Change In Working Capital",
    "Investing Cash Flow", "Financing Cash Flow",
    "Repurchase Of Capital Stock", "Cash Dividends Paid",
]


def _show_financials(symbol, section, fields, title):
    if not _has_firestore():
        console.print("[yellow]Financials require Firestore. Set FIRESTORE_PROJECT.[/yellow]")
        return

    all_fins = storage.get_all_financials(symbol)
    if not all_fins:
        console.print(f"[yellow]No financials stored for {symbol}[/yellow]")
        return

    quarterly = [f for f in all_fins if f.get("freq") == "Q"][:4]
    annual = [f for f in all_fins if f.get("freq") == "FY"][:4]

    for label, periods in [("Quarterly", quarterly), ("Annual", annual)]:
        if not periods:
            continue

        t = Table(
            title=f"{symbol} — {label} {title}",
            box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        )
        t.add_column("", style="bold", width=32)
        for p in periods:
            t.add_column(p["period"], justify="right", width=14)

        has_data = False
        for field in fields:
            vals = [p.get(section, {}).get(field) for p in periods]
            if not any(v is not None for v in vals):
                continue
            has_data = True
            row = [field] + [_fin(v) for v in vals]
            t.add_row(*row)

        if not has_data:
            all_keys = set()
            for p in periods:
                all_keys.update(p.get(section, {}).keys())
            for field in sorted(all_keys):
                vals = [p.get(section, {}).get(field) for p in periods]
                row = [field] + [_fin(v) for v in vals]
                t.add_row(*row)

        console.print(t)


def cmd_income(symbol):
    _show_financials(symbol, "income", INCOME_FIELDS, "Income Statement")


def cmd_balance(symbol):
    _show_financials(symbol, "balance_sheet", BALANCE_FIELDS, "Balance Sheet")


def cmd_cashflow(symbol):
    _show_financials(symbol, "cash_flow", CASHFLOW_FIELDS, "Cash Flow")


# ── /AAPL div — Dividends ─────────────────────────────────────────────────


def cmd_dividends(symbol):
    console.print(f"[grey70]Fetching live data for {symbol}...[/grey70]")
    info = _yahoo(symbol)
    if not info or not info.get("shortName"):
        console.print(f"[red]{symbol}: no data found[/red]")
        return

    t = Table(
        title=f"{symbol} — Dividends",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        width=TABLE_WIDTH,
    )
    t.add_column("Metric", style="bold", width=24)
    t.add_column("Value", justify="right", width=16)

    ex_date = info.get("exDividendDate")
    if isinstance(ex_date, (int, float)):
        ex_date = datetime.fromtimestamp(ex_date).strftime("%Y-%m-%d")

    t.add_row("Dividend Yield", _pct(info.get("dividendYield")))
    t.add_row("Dividend Rate", _price(info.get("dividendRate")))
    t.add_row("Payout Ratio", _pct(info.get("payoutRatio")))
    t.add_row("Ex-Dividend Date", str(ex_date or "N/A"))
    t.add_row("5yr Avg Yield", _pct(
        info.get("fiveYearAvgDividendYield", 0) / 100
        if info.get("fiveYearAvgDividendYield") else None
    ))

    console.print(t)


# ── /AAPL short — Short Interest ──────────────────────────────────────────


def cmd_short(symbol, info=None):
    if not info:
        console.print(f"[grey70]Fetching live data for {symbol}...[/grey70]")
        info = _yahoo(symbol)
    if not info or not info.get("shortName"):
        console.print(f"[red]{symbol}: no data found[/red]")
        return

    t = Table(
        title=f"{symbol} — Short Interest",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        width=TABLE_WIDTH,
    )
    t.add_column("Metric", style="bold", width=24)
    t.add_column("Value", justify="right", width=16)

    short_date = info.get("dateShortInterest")
    if isinstance(short_date, (int, float)):
        short_date = datetime.fromtimestamp(short_date).strftime("%Y-%m-%d")

    t.add_row("Shares Short", _count(info.get("sharesShort")))
    t.add_row("Short Ratio", _num(info.get("shortRatio")))
    t.add_row("Short % of Float", _pct(
        info.get("shortPercentOfFloat")
    ))
    t.add_row("Short % of Shares Out", _pct(
        info.get("sharesPercentSharesOut")
    ))
    t.add_row("As of", str(short_date or "N/A"))

    console.print(t)


# ── /AAPL target — Analyst Targets ────────────────────────────────────────


def cmd_target(symbol, info=None):
    if not info:
        console.print(f"[grey70]Fetching live data for {symbol}...[/grey70]")
        info = _yahoo(symbol)
    if not info or not info.get("shortName"):
        console.print(f"[red]{symbol}: no data found[/red]")
        return

    t = Table(
        title=f"{symbol} — Analyst Targets",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        width=TABLE_WIDTH,
    )
    t.add_column("Metric", style="bold", width=24)
    t.add_column("Value", justify="right", width=16)

    rec = info.get("recommendationKey", "N/A")
    rec_map = {
        "strongBuy": "[bold green]Strong Buy[/bold green]",
        "buy": "[green]Buy[/green]",
        "hold": "[yellow]Hold[/yellow]",
        "sell": "[red]Sell[/red]",
        "strongSell": "[bold red]Strong Sell[/bold red]",
    }

    t.add_row("Recommendation", rec_map.get(rec, rec))
    t.add_row("Mean Score", _num(info.get("recommendationMean")))
    t.add_row("# Analysts", str(info.get("numberOfAnalystOpinions", "N/A")))
    t.add_row("Target Mean", _price(info.get("targetMeanPrice")))
    t.add_row("Target Median", _price(info.get("targetMedianPrice")))
    t.add_row("Target High", _price(info.get("targetHighPrice")))
    t.add_row("Target Low", _price(info.get("targetLowPrice")))

    price = info.get("regularMarketPrice") or info.get("currentPrice")
    mean_target = info.get("targetMeanPrice")
    if price and mean_target:
        try:
            upside = (float(mean_target) - float(price)) / float(price)
            c = _color(upside)
            sign = "+" if upside > 0 else ""
            t.add_row("Upside/Downside", f"[{c}]{sign}{upside * 100:.1f}%[/{c}]")
        except (ValueError, TypeError):
            pass

    console.print(t)


# ── /AAPL chart — Price Chart ─────────────────────────────────────────────


def cmd_chart(symbol):
    if not _has_firestore():
        console.print("[yellow]Chart requires Firestore. Set FIRESTORE_PROJECT.[/yellow]")
        return

    prices = storage.get_prices_history(symbol, limit=252)
    if not prices:
        console.print(f"[yellow]No stored prices for {symbol}[/yellow]")
        return

    try:
        import plotext as plt

        dates = [p["date"] for p in prices]
        closes = [p["close"] for p in prices]
        highs = [p["high"] for p in prices]
        lows = [p["low"] for p in prices]
        volumes = [p.get("volume", 0) for p in prices]

        n = len(dates)
        tick_count = 6
        step = max(1, n // tick_count)
        tick_idx = list(range(0, n, step))
        if tick_idx[-1] != n - 1:
            tick_idx.append(n - 1)
        tick_labels = [dates[i] for i in tick_idx]

        chart_width = min(console.width - 4, TABLE_WIDTH)

        price_min = min(closes)
        price_max = max(closes)
        price_step = (price_max - price_min) / 5
        price_ticks = [price_min + i * price_step for i in range(6)]
        price_labels = [f"${v:,.0f}" for v in price_ticks]

        plt.clear_figure()
        plt.theme("dark")
        plt.plot_size(chart_width, 18)
        plt.plot(list(range(n)), closes, label="Close")
        plt.title(f"{symbol} — 1 Year")
        plt.xticks(tick_idx, tick_labels)
        plt.yticks(price_ticks, price_labels)
        plt.show()

        vol_max = max(volumes) if volumes else 0
        vol_ticks = [0, vol_max / 2, vol_max]
        vol_labels = [_count(v) for v in vol_ticks]

        plt.clear_figure()
        plt.theme("dark")
        plt.plot_size(chart_width, 6)
        plt.bar(list(range(n)), volumes, width=1)
        plt.title("Volume")
        plt.xticks(tick_idx, tick_labels)
        plt.yticks(vol_ticks, vol_labels)
        plt.show()

    except ImportError:
        t = Table(
            title=f"{symbol} — Recent Prices",
            box=box.SIMPLE_HEAVY, border_style="green",
        )
        t.add_column("Date", width=12)
        t.add_column("Open", justify="right", width=10)
        t.add_column("High", justify="right", width=10)
        t.add_column("Low", justify="right", width=10)
        t.add_column("Close", justify="right", width=10)
        t.add_column("Volume", justify="right", width=12)

        for p in prices[-20:]:
            t.add_row(
                p["date"], _price(p["open"]), _price(p["high"]),
                _price(p["low"]), _price(p["close"]), _count(p.get("volume")),
            )
        console.print(t)
        console.print("[grey70]Install plotext for interactive charts: pip install plotext[/grey70]")


# ── /AAPL mgmt — Management / CEO ─────────────────────────────────────────


def _get_ceorater(symbol):
    if not CEORATER_API_KEY:
        return None
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
    except Exception:
        pass
    return None


def cmd_mgmt(symbol):
    console.print(f"[grey70]Fetching management data for {symbol}...[/grey70]")

    ceo_data = _get_ceorater(symbol)
    if ceo_data:
        t = Table(
            title=f"{symbol} — CEO (via CEORater)",
            box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        )
        t.add_column("", style="bold", width=20)
        t.add_column("", width=20)

        for ceo in ceo_data:
            t.add_row("CEO", str(ceo.get("ceo", "N/A")))
            t.add_row("Founder CEO", "Yes" if ceo.get("founderCEO") else "No")
            t.add_row("CEORater Score", _num(ceo.get("ceoraterScore"), 1))
            t.add_row("Alpha Score", _num(ceo.get("alphaScore"), 1))
            t.add_row("Comp Score", _num(ceo.get("compScore"), 1))
            comp = ceo.get("compensationMM")
            if comp:
                t.add_row("Compensation", f"${float(comp):,.1f}M")
            tsr = ceo.get("tsrMultiple")
            if tsr:
                t.add_row("TSR (Tenure)", f"{float(tsr):,.1f}x")

        console.print(t)
    elif CEORATER_API_KEY:
        console.print(f"[grey70]No CEORater data for {symbol}[/grey70]")
    else:
        console.print("[grey70]CEORater data requires CEORATER_API_KEY[/grey70]")

    info = _yahoo(symbol)
    officers = info.get("companyOfficers", [])
    if officers:
        t = Table(
            title=f"{symbol} — Officers",
            box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        )
        t.add_column("Name", style="bold", width=24)
        t.add_column("Title", width=32)
        t.add_column("Age", justify="right", width=6)
        t.add_column("Total Pay", justify="right", width=14)

        for o in officers[:10]:
            pay = o.get("totalPay")
            t.add_row(
                o.get("name", ""),
                o.get("title", ""),
                str(o.get("age", "")),
                _dollar(pay) if pay else "N/A",
            )
        console.print(t)
    elif not ceo_data:
        console.print(f"[yellow]No management data found for {symbol}[/yellow]")


# ── /AAPL filings — SEC Filings ───────────────────────────────────────────


def _load_cik_cache():
    if _cik_cache:
        return
    try:
        resp = requests.get(
            "https://www.sec.gov/files/company_tickers.json",
            headers=SEC_HEADERS,
            timeout=15,
        )
        if resp.status_code == 200:
            for entry in resp.json().values():
                _cik_cache[entry["ticker"].upper()] = str(entry["cik_str"])
    except Exception:
        pass


def cmd_filings(symbol):
    console.print(f"[grey70]Fetching SEC filings for {symbol}...[/grey70]")
    _load_cik_cache()

    cik = _cik_cache.get(symbol)
    if not cik:
        console.print(f"[yellow]No SEC CIK found for {symbol}[/yellow]")
        return

    cik_padded = cik.zfill(10)
    try:
        resp = requests.get(
            f"https://data.sec.gov/submissions/CIK{cik_padded}.json",
            headers=SEC_HEADERS,
            timeout=15,
        )
        if resp.status_code != 200:
            console.print(f"[red]SEC API returned {resp.status_code}[/red]")
            return

        data = resp.json()
        recent = data.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        descs = recent.get("primaryDocDescription", [])
        accessions = recent.get("accessionNumber", [])

    except Exception as e:
        console.print(f"[red]Error fetching SEC data: {e}[/red]")
        return

    if not forms:
        console.print(f"[yellow]No filings found for {symbol}[/yellow]")
        return

    t = Table(
        title=f"{symbol} — Recent SEC Filings",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
    )
    t.add_column("Date", width=12)
    t.add_column("Form", style="bold", width=10)
    t.add_column("Description", width=40)
    t.add_column("Accession", style="grey70", width=24)

    count = min(15, len(forms))
    for i in range(count):
        t.add_row(
            dates[i] if i < len(dates) else "",
            forms[i],
            descs[i] if i < len(descs) else "",
            accessions[i] if i < len(accessions) else "",
        )

    console.print(t)


# ── /AAPL news — Recent News ──────────────────────────────────────────────


def cmd_news(symbol):
    console.print(f"[grey70]Fetching news for {symbol}...[/grey70]")
    try:
        t = yf.Ticker(symbol)
        news = t.news
        if not news:
            console.print(f"[yellow]No recent news for {symbol}[/yellow]")
            return

        items = news[:10]
        for item in items:
            title = item.get("title", "")
            publisher = item.get("publisher", "")
            link = item.get("link", "")
            ts = item.get("providerPublishTime")
            date_str = ""
            if ts:
                try:
                    date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
                except (ValueError, OSError):
                    pass

            console.print(f"  [bold white]{title}[/bold white]")
            meta = f"  [grey70]{publisher}"
            if date_str:
                meta += f" · {date_str}"
            meta += "[/grey70]"
            console.print(meta)
            if link:
                console.print(f"  [blue underline]{link}[/blue underline]")
            console.print()

    except Exception as e:
        console.print(f"[red]Error fetching news: {e}[/red]")


# ── /compare — Comp Table ─────────────────────────────────────────────────


def cmd_compare(symbols):
    console.print(f"[grey70]Fetching live data for {', '.join(symbols)}...[/grey70]")

    infos = {}
    for sym in symbols:
        info = _yahoo(sym)
        if info and info.get("shortName"):
            infos[sym] = info
        else:
            console.print(f"[yellow]{sym}: no data[/yellow]")

    if not infos:
        return

    t = Table(
        title="Comparison",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        expand=True,
    )
    t.add_column("Metric", style="bold", width=16)
    for sym in infos:
        name = infos[sym].get("shortName", sym)
        if len(name) > 16:
            name = name[:13] + "..."
        t.add_column(f"{sym}\n[grey70]{name}[/grey70]", justify="right", width=14)

    metrics = [
        ("Price", lambda i: _price(
            i.get("regularMarketPrice") or i.get("currentPrice"))),
        ("Market Cap", lambda i: _dollar(i.get("marketCap"))),
        ("EV", lambda i: _dollar(i.get("enterpriseValue"))),
        ("Revenue", lambda i: _dollar(i.get("totalRevenue"))),
        ("EBITDA", lambda i: _dollar(i.get("ebitda"))),
        ("Net Income", lambda i: _dollar(i.get("netIncomeToCommon"))),
        ("EPS (TTM)", lambda i: _num(i.get("trailingEps"))),
        ("EPS (Fwd)", lambda i: _num(i.get("forwardEps"))),
        ("P/E (TTM)", lambda i: _ratio(i.get("trailingPE"))),
        ("P/E (Fwd)", lambda i: _ratio(i.get("forwardPE"))),
        ("PEG", lambda i: _ratio(i.get("pegRatio"))),
        ("P/B", lambda i: _ratio(i.get("priceToBook"))),
        ("EV/Revenue", lambda i: _ratio(i.get("enterpriseToRevenue"))),
        ("EV/EBITDA", lambda i: _ratio(i.get("enterpriseToEbitda"))),
        ("EV/OpCF", lambda i: _safe_ratio(
            i.get("enterpriseValue"), i.get("operatingCashflow"))),
        ("EV/FCF", lambda i: _safe_ratio(
            i.get("enterpriseValue"), i.get("freeCashflow"))),
        ("Div Yield", lambda i: _pct(i.get("dividendYield"))),
        ("Beta", lambda i: _num(i.get("beta"))),
    ]

    for label, fn in metrics:
        row = [label] + [fn(infos[sym]) for sym in infos]
        t.add_row(*row)

    console.print(t)


# ── /AAPL — Full Report ───────────────────────────────────────────────────


def cmd_full(symbol):
    console.print(f"[grey70]Fetching data for {symbol}...[/grey70]")
    info = _yahoo(symbol)
    if not info or not info.get("shortName"):
        console.print(f"[red]{symbol}: no data found[/red]")
        return

    cmd_overview(symbol, info=info)
    console.print("[grey70]  Source: Yahoo Finance[/grey70]")
    console.print()
    cmd_estimates(symbol)
    console.print()
    cmd_short(symbol, info=info)
    console.print("[grey70]  Source: Yahoo Finance[/grey70]")


# ── Command router ─────────────────────────────────────────────────────────

SUBCMDS = {
    "est": cmd_estimates,
    "inc": cmd_income,
    "bal": cmd_balance,
    "cf": cmd_cashflow,
    "div": cmd_dividends,
    "short": cmd_short,
    "target": cmd_target,
    "mgmt": cmd_mgmt,
    "filings": cmd_filings,
    "news": cmd_news,
}


def main():
    _print_banner()

    while True:
        try:
            line = console.input("[bold green]tek2day>[/bold green] ")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[grey70]Goodbye.[/grey70]")
            break

        line = line.strip()
        if not line:
            continue

        if not line.startswith("/"):
            console.print("[yellow]Commands start with /. Type /help for options.[/yellow]")
            continue

        parts = line[1:].split()
        if not parts:
            continue

        first = parts[0].lower()

        if first in ("exit", "quit", "q"):
            console.print("[grey70]Goodbye.[/grey70]")
            break

        if first == "help":
            _print_banner()
            continue

        if first == "compare":
            if len(parts) < 3:
                console.print("[yellow]Usage: /compare AAPL MSFT (up to 20 tickers)[/yellow]")
                continue
            if len(parts) > 21:
                console.print("[yellow]Maximum 20 tickers at a time.[/yellow]")
                continue
            cmd_compare([p.upper() for p in parts[1:]])
            continue

        symbol = parts[0].upper()
        subcmd = parts[1].lower() if len(parts) > 1 else None

        if subcmd is None:
            cmd_full(symbol)
        elif subcmd in SUBCMDS:
            SUBCMDS[subcmd](symbol)
        else:
            console.print(
                f"[yellow]Unknown subcommand: {subcmd}. "
                f"Options: {', '.join(SUBCMDS.keys())}[/yellow]"
            )


if __name__ == "__main__":
    main()
