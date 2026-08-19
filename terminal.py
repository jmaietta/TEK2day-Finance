#!/usr/bin/env python3
"""
TEK2day Finance — Interactive Terminal

Usage:
    tek2day
"""
import functools
import io
import os
import sys
import threading
import time
from datetime import datetime, timezone

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

try:
    import readline
except ImportError:
    pass

__version__ = "1.0.2"

import requests
from rich.console import Console, Group
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

yf = None

def _yf():
    global yf
    if yf is None:
        import yfinance
        yf = yfinance
    return yf

from config import CEORATER_API_KEY, TEK2DAY_API_URL

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


# ⚠️ EVERY FORMATTER BELOW GOES THROUGH _to_float, AND THAT IS THE WHOLE POINT.
#
# These helpers used to ask "is this value MISSING?" (`val is None`) when they
# meant "is this value a USABLE NUMBER?". NaN is not None, so it walked straight
# past the guard and into the f-string, and Yahoo sends NaN whenever it has no
# figure — it is not rare. Measured 19 Aug 2026 across the full universe: 6,182
# cells on 809 of the 4,337 companies holding estimates printed a literal `$nan`,
# `nan%` or `($nan)` to users on the website and in the terminal. `_dollar` was
# the worst of them, rendering a missing number as `($nan)` — brackets, which in
# this codebase mean a LOSS.
#
# The partner API never had the bug because it filters on `envelope.finite`.
# `_eps` and `_analyst_count` were fixed on 19 Aug; these six were left behind,
# which is the seventh instance of this same confusion. Yahoo writes `--` for a
# figure it does not have; we now write `N/A`.
#
# _to_float also rejects Inf, so `$infT` and `infB` are gone with it.

def _dollar(val):
    """Money, in the accounting convention: a negative in brackets.

    His call, 18 Aug — consistent across every financial figure, so a loss never
    depends on a reader catching a minus sign at 12px.
    """
    v = _to_float(val)
    if v is None:
        return "N/A"
    a = abs(v)
    if a >= 1e12:
        out = f"${a / 1e12:,.2f}T"
    elif a >= 1e9:
        out = f"${a / 1e9:,.2f}B"
    elif a >= 1e6:
        out = f"${a / 1e6:,.1f}M"
    else:
        out = f"${a:,.2f}"
    return out if v >= 0 else f"({out})"


def _count(val):
    v = _to_float(val)
    if v is None:
        return "N/A"
    if abs(v) >= 1e9:
        return f"{v / 1e9:,.2f}B"
    if abs(v) >= 1e6:
        return f"{v / 1e6:,.1f}M"
    if abs(v) >= 1e3:
        return f"{v / 1e3:,.0f}K"
    return f"{v:,.0f}"


def _pct(val):
    v = _to_float(val)
    if v is None:
        return "N/A"
    return f"{v * 100:.2f}%"


def _ratio(val):
    """A multiple. Keeps the minus sign — brackets are for currency.

    His call, 18 Aug: dollar figures follow the accounting convention, ratios do
    not. A negative multiple is not an accounting entry, and "(2.2x)" reads more
    like a footnote marker than a number.
    """
    v = _to_float(val)
    if v is None:
        return "N/A"
    if abs(v) >= 100:
        return f"{v:,.0f}x"
    return f"{v:.1f}x"


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
    """A plain number, negative in brackets.

    Keeps the one behaviour the others do not have: a NON-NUMERIC value passes
    through as itself, because callers use this for labels as well as figures.
    A value that IS a number but not a finite one is still refused — `nan` must
    not reach the `str(val)` fallback and print itself.
    """
    v = _to_float(val)
    if v is not None:
        out = f"{abs(v):,.{decimals}f}"
        return out if v >= 0 else f"({out})"
    if val is None:
        return "N/A"
    try:
        float(val)
    except (ValueError, TypeError):
        return str(val) if val else "N/A"
    return "N/A"  # a number we refused: NaN or Inf


def _price(val):
    v = _to_float(val)
    if v is None:
        return "N/A"
    return f"${v:,.2f}"


# Which share count belongs with which reported EPS line.
EPS_SHARE_COUNT = {
    "Basic EPS": "Basic Average Shares",
    "Diluted EPS": "Diluted Average Shares",
}


def computed_eps(income, field="Diluted EPS"):
    """Earnings per share from the record's OWN components, or None.

    COMPUTED RATHER THAN STORED, for two reasons that compound.

    1. The stored figure can be missing its minus sign. SQNS 2025-Q4 holds
       +5.62 against a net loss of $87,127,000 over 15,504,809 shares: the
       company lost $5.62 a share and the record reads as a profit.
    2. Share counts now track Yahoo's current basis (see
       storage.SHARE_COUNT_FIELDS), so a stored EPS frozen on an older basis
       would openly contradict the share count sitting beside it. Deriving it
       keeps the two consistent by construction.

    SAFE TO COMPUTE — the units were proven first, across all 9,911 tickers:
    |EPS x shares| / |net income| lands at ~1 for 56,274 of 56,778 records
    (99.1%). A denomination fault would show a fat cluster at 1,000x; there are
    ten scattered records there, so net income and share counts are in the same
    units everywhere that matters.

    Net income to COMMON where stated, after preferred holders are paid. Using
    total net income instead fails every company with preferred stock, by exactly
    its preferred dividends, in every period.

    Returns None when a component is absent, so the caller keeps whatever was
    reported rather than inventing a figure.
    """
    if not isinstance(income, dict):
        return None
    shares = _to_float(income.get(EPS_SHARE_COUNT.get(field, "Diluted Average Shares")))
    if not shares:
        return None
    ni_common = _to_float(income.get("Net Income Common Stockholders"))
    net_income = ni_common if ni_common is not None else _to_float(income.get("Net Income"))
    if net_income is None:
        return None
    return net_income / shares


def _analyst_count(val):
    """How many analysts contributed. Blank when we do not know.

    ⚠️ THIS WAS A LIVE 500. The old line was:

        str(int(val)) if val is not None else "N/A"

    Firestore stores Yahoo's blanks as `nan`, and `nan is not None` is True, so
    `int(nan)` raised and took the WHOLE COMPANY PAGE down — not the row, the
    page. Measured 19 Aug 2026: five companies in a 200 sample, so roughly 250
    across the universe, where /CANF, /CRML and /LOT returned "cannot convert
    float NaN to integer" instead of a summary. It also broke the terminal's
    estimates table and cmd_full.

    Fifth time presence has been tested where finiteness was meant. Shared by
    the website and the terminal so there is only one of it now.
    """
    v = _to_float(val)
    return "N/A" if v is None else f"{int(v):,}"


def _eps(val):
    """A per-share figure in the accounting convention: a loss in parentheses.

    His call, 18 Aug: positive shows as $x.xx, negative as ($x.xx). A leading
    minus is easy to miss at 12px in a table of forty rows; brackets are not.
    """
    v = _to_float(val)
    if v is None:
        return ""
    return f"${v:,.2f}" if v >= 0 else f"(${abs(v):,.2f})"


def _fin(val):
    """A statement figure, negative in brackets.

    Income statements and cash flow statements are full of negatives — capex,
    interest expense, a loss-making quarter. A leading minus is easy to miss in
    a table of forty rows; brackets are not.
    """
    v = _to_float(val)
    if v is not None:
        a = abs(v)
        if a >= 1e9:
            out = f"{a / 1e9:,.1f}B"
        elif a >= 1e6:
            out = f"{a / 1e6:,.1f}M"
        elif a >= 1e3:
            out = f"{a / 1e3:,.0f}K"
        else:
            out = f"{a:,.2f}"
        return out if v >= 0 else f"({out})"
    if val is None:
        return ""
    try:
        float(val)
    except (ValueError, TypeError):
        return str(val)
    return ""  # this branch used to filter NaN but let Inf through as `infB`


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

PYPI_PACKAGE = "tek2day-finance"


def _version_tuple(version):
    parts = []
    for part in str(version).split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def _check_for_update():
    try:
        resp = requests.get(
            f"https://pypi.org/pypi/{PYPI_PACKAGE}/json",
            timeout=2,
        )
        if resp.status_code == 200:
            latest = resp.json().get("info", {}).get("version", "")
            if latest and _version_tuple(latest) > _version_tuple(__version__):
                console.print(
                    f"[yellow]  Update available: v{latest} "
                    f"(you have v{__version__})[/yellow]"
                )
                console.print(
                    "[yellow]  Run: python -m pip install --upgrade "
                    f"{PYPI_PACKAGE}[/yellow]"
                )
                console.print()
    except Exception:
        pass


# ── Banner ─────────────────────────────────────────────────────────────────

HELP_TEXT = """\
[white]  /TICKER                   Summary
  /TICKER inc               Income statement
  /TICKER bal               Balance sheet
  /TICKER cf                Cash flow
  /TICKER mgmt              Management / CEO
  /TICKER filings           SEC filings
  /TICKER news              Recent news
  /comp TICKER1 TICKER2 ... Comp table (up to 6)
  /macro                    Macro dashboard
  /help                     Show this menu
  /exit                     Quit[/white]"""


def _print_banner():
    from rich.panel import Panel
    from rich.align import Align
    tek2day_art = """████████╗███████╗██╗  ██╗██████╗ ██████╗  █████╗ ██╗   ██╗
╚══██╔══╝██╔════╝██║ ██╔╝╚════██╗██╔══██╗██╔══██╗╚██╗ ██╔╝
   ██║   █████╗  █████╔╝  █████╔╝██║  ██║███████║ ╚████╔╝
   ██║   ██╔══╝  ██╔═██╗ ██╔═══╝ ██║  ██║██╔══██║  ╚██╔╝
   ██║   ███████╗██║  ██╗███████╗██████╔╝██║  ██║   ██║
   ╚═╝   ╚══════╝╚═╝  ╚═╝╚══════╝╚═════╝ ╚═╝  ╚═╝   ╚═╝"""
    finance_art = """███████╗██╗███╗   ██╗ █████╗ ███╗   ██╗ ██████╗███████╗
██╔════╝██║████╗  ██║██╔══██╗████╗  ██║██╔════╝██╔════╝
█████╗  ██║██╔██╗ ██║███████║██╔██╗ ██║██║     █████╗
██╔══╝  ██║██║╚██╗██║██╔══██║██║╚██╗██║██║     ██╔══╝
██║     ██║██║ ╚████║██║  ██║██║ ╚████║╚██████╗███████╗
╚═╝     ╚═╝╚═╝  ╚═══╝╚═╝  ╚═╝╚═╝  ╚═══╝ ╚═════╝╚══════╝"""
    title_width = max(
        len(line)
        for art in (tek2day_art, finance_art)
        for line in art.splitlines()
    )
    finance_lines = finance_art.splitlines()
    finance_width = max(len(line) for line in finance_lines)
    finance_indent = " " * max((title_width - finance_width) // 2, 0)
    finance_art = "\n".join(f"{finance_indent}{line}" for line in finance_lines)
    title_text = Text(tek2day_art, style="bold white")
    title_text.append("\n\n")
    title_text.append(finance_art, style="bold green")
    ver = Text(f"v{__version__}".center(title_width), style="grey70")
    banner = Align.center(Text.assemble(
        "\n", title_text, "\n", ver, "\n",
    ))
    console.print()
    console.print(Panel(banner, border_style="red", padding=(0, 4)))
    console.print(HELP_TEXT)
    console.print()


# ── Short-lived caches ──────────────────────────────────────────────────────
# Live quotes stay fresh (short TTL); slow non-price lookups (Yahoo .info,
# Firestore reads) are reused across commands so a burst of commands for the
# same symbol does not refetch identical data.


def _ttl_cache(ttl_seconds, should_cache=bool):
    def wrap(fn):
        cache = {}
        lock = threading.Lock()

        @functools.wraps(fn)
        def inner(*args):
            now = time.monotonic()
            with lock:
                hit = cache.get(args)
                if hit is not None and now - hit[0] < ttl_seconds:
                    return hit[1]
            result = fn(*args)
            if should_cache(result):
                with lock:
                    cache[args] = (now, result)
            return result

        return inner
    return wrap


# ── Live Yahoo ─────────────────────────────────────────────────────────────


@_ttl_cache(600)
def _yahoo(symbol):
    try:
        return _yf().Ticker(symbol.replace(".", "-")).info or {}
    except Exception as e:
        console.print(f"[red]Error fetching {symbol}: {e}[/red]")
        return {}


def _to_float(val):
    """A usable number, or None.

    ⚠️ REJECTS INFINITY AS WELL AS NaN. It used to filter only NaN, which meant
    Inf flowed through as a "valid" figure — and `int(inf)` raises OverflowError
    the same way `int(nan)` raises ValueError, the bug that took ~250 company
    pages down. Caught by a test written for the NaN fix, which is the only
    reason it was found.

    This is now the same standard envelope.finite applies. No financial figure
    is ever legitimately infinite, so nothing is lost by refusing it.
    """
    if val is None:
        return None
    try:
        v = float(val)
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return v
    except (ValueError, TypeError):
        return None


def _first_value(data, keys):
    if not isinstance(data, dict):
        return None
    for key in keys:
        val = data.get(key)
        if val is not None:
            return val
    return None


def _fast_value(data, *keys):
    for key in keys:
        try:
            val = data.get(key)
        except Exception:
            try:
                val = data[key]
            except Exception:
                try:
                    val = getattr(data, key)
                except Exception:
                    val = None
        if val is not None:
            return val
    return None


@_ttl_cache(30, should_cache=lambda quote: quote.get("price") is not None)
def _live_quote(symbol):
    """Return only live quote fields from Yahoo.

    Primary source is the chart-metadata call, which carries the official
    quote (price, previous close, day high/low, volume) PLUS its date —
    one Yahoo read serves both the summary header and the chart's last
    point, so the two always agree.
    """
    quote = {
        "price": None,
        "previous_close": None,
        "change": None,
        "change_pct": None,
        "volume": None,
        "fifty_two_week_high": None,
        "fifty_two_week_low": None,
        "date": None,
        "day_high": None,
        "day_low": None,
    }
    yahoo_symbol = symbol.replace(".", "-")
    try:
        ticker = _yf().Ticker(yahoo_symbol)
        ticker.history(period="1d", auto_adjust=True)
        meta = ticker.history_metadata or {}
        quote["price"] = _to_float(meta.get("regularMarketPrice"))
        quote["previous_close"] = _to_float(meta.get("previousClose"))
        quote["volume"] = _to_float(meta.get("regularMarketVolume"))
        quote["fifty_two_week_high"] = _to_float(meta.get("fiftyTwoWeekHigh"))
        quote["fifty_two_week_low"] = _to_float(meta.get("fiftyTwoWeekLow"))
        quote["day_high"] = _to_float(meta.get("regularMarketDayHigh"))
        quote["day_low"] = _to_float(meta.get("regularMarketDayLow"))
        ts = meta.get("regularMarketTime")
        if ts:
            # ⚠️ SHARED WITH fetchers AND app. This line used to be its own copy
            # of `ts + offset`, and when yfinance began sending a pandas
            # Timestamp it raised into the bare `except` below — silently
            # costing this quote its date, 52-week range and day range, which is
            # why the company-page chart lost its last point. Do not inline it
            # again. Imported here rather than at module scope so the pip
            # package keeps its lazy yfinance import.
            from fetchers import yahoo_local_date  # noqa: PLC0415

            local_date = yahoo_local_date(ts, meta.get("gmtoffset") or 0)
            if local_date is not None:
                quote["date"] = local_date.strftime("%Y-%m-%d")
    except Exception:
        pass

    if quote["price"] is None or quote["previous_close"] is None:
        try:
            ticker = _yf().Ticker(yahoo_symbol)
            fast = getattr(ticker, "fast_info", {}) or {}
            quote["price"] = quote["price"] or _to_float(_fast_value(fast, "last_price", "lastPrice"))
            quote["previous_close"] = quote["previous_close"] or _to_float(_fast_value(
                fast, "previous_close", "previousClose", "regularMarketPreviousClose"
            ))
            quote["volume"] = quote["volume"] or _to_float(_fast_value(fast, "last_volume", "lastVolume"))
            quote["fifty_two_week_high"] = quote["fifty_two_week_high"] or _to_float(_fast_value(
                fast, "year_high", "yearHigh"
            ))
            quote["fifty_two_week_low"] = quote["fifty_two_week_low"] or _to_float(_fast_value(
                fast, "year_low", "yearLow"
            ))
        except Exception:
            pass

    if quote["price"] is None or quote["previous_close"] is None:
        try:
            info = _yahoo(symbol)
            quote["price"] = quote["price"] or _to_float(
                info.get("regularMarketPrice") or info.get("currentPrice")
            )
            quote["previous_close"] = quote["previous_close"] or _to_float(
                info.get("regularMarketPreviousClose") or info.get("previousClose")
            )
            quote["volume"] = quote["volume"] or _to_float(info.get("regularMarketVolume"))
            quote["fifty_two_week_high"] = quote["fifty_two_week_high"] or _to_float(
                info.get("fiftyTwoWeekHigh")
            )
            quote["fifty_two_week_low"] = quote["fifty_two_week_low"] or _to_float(
                info.get("fiftyTwoWeekLow")
            )
        except Exception as e:
            console.print(f"[red]Error fetching Yahoo Finance data for {symbol}: {e}[/red]")

    price = quote["price"]
    previous_close = quote["previous_close"]
    if price is not None and previous_close:
        quote["change"] = price - previous_close
        quote["change_pct"] = (price - previous_close) / previous_close * 100
    return quote


@_ttl_cache(300, should_cache=lambda meta: bool(meta))
def _firestore_meta(symbol):
    if not _has_firestore():
        return None
    try:
        return storage.get_ticker_meta(symbol) or {}
    except Exception:
        return None


@_ttl_cache(300)
def _all_financials(symbol):
    """Cached Firestore financials read shared by commands and web payloads."""
    if not _has_firestore():
        return []
    try:
        return storage.get_all_financials(symbol) or []
    except Exception:
        return []


@_ttl_cache(300)
def _estimate_history(symbol):
    """Cached Firestore estimates read shared by commands and web payloads."""
    if not _has_firestore():
        return []
    try:
        return storage.get_estimate_history(symbol, limit=1) or []
    except Exception:
        return []


def _period_end_date(period):
    try:
        return datetime.strptime(str((period or {}).get("period_end") or ""), "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _are_consecutive_quarters(periods) -> bool:
    """Whether these periods are adjacent quarters in calendar terms.

    Adjacency in the STORED LIST is not adjacency in time. If a quarter is
    missing from Firestore entirely — no document, not even a stub — the list
    closes up and four records that look neighbouring can span fifteen months.
    Summing those and calling the result trailing-twelve-months would be a
    worse error than the one this fix exists to remove, because the number
    would look completely ordinary.

    Fiscal quarters run 13 or 14 weeks, so a real gap is about 91 days; 60 to
    120 accommodates every issuer's calendar without admitting a skipped
    quarter, which would show up as roughly 180.
    """
    dates = [_period_end_date(p) for p in periods]
    if any(d is None for d in dates):
        return False
    for newer, older in zip(dates, dates[1:]):
        gap = (newer - older).days
        if not 60 <= gap <= 120:
            return False
    return True


def _ttm_window(periods, section, keys, size=4):
    """The most recent run of `size` consecutive quarters that hold this field.

    Returns (total, period_end of the newest quarter in the window), or
    (None, None).

    WHY A WINDOW AND NOT `periods[:4]`.

    Yahoo opens a quarter within hours of a release and fills it in over the
    following days — sometimes weeks. Until it does, TEK2day holds a stub in the
    newest slot. The old code took the four newest RECORDS, hit that stub on its
    first iteration, gave up, and `_statement_value` then published the latest
    ANNUAL figure under a heading that says (TTM).

    Amazon, 16 Aug 2026, measured: the card showed $716.92B revenue and $77.67B
    net income — its fiscal 2025, a period that ended nearly eight months
    earlier — because its June quarter was a stub at Yahoo and therefore here.
    The four quarters behind that stub were all present and sum to $742.78B.

    So a stub at the FRONT simply moves the window back one quarter. That is
    still a true trailing twelve months; it just ends a quarter earlier, and the
    date it ends on is returned so a consumer can say which.
    """
    values = [_to_float(_first_value((p or {}).get(section, {}), keys)) for p in periods]
    for start in range(0, max(0, len(periods) - size + 1)):
        window = values[start:start + size]
        if any(value is None for value in window):
            continue
        if not _are_consecutive_quarters(periods[start:start + size]):
            continue
        return sum(window), str(periods[start].get("period_end") or "") or None
    return None, None


def _sum_recent(periods, section, keys):
    total, _end = _ttm_window(periods, section, keys)
    return total


def _latest_annual_value(periods, section, keys):
    if not periods:
        return None
    return _to_float(_first_value(periods[0].get(section, {}), keys))


def _latest_annual_window(annual, section, keys):
    """The newest fiscal year holding this field, with the date it ends on."""
    if not annual:
        return None, None
    value = _to_float(_first_value((annual[0] or {}).get(section, {}), keys))
    if value is None:
        return None, None
    return value, str(annual[0].get("period_end") or "") or None


def _ttm_value(quarterly, annual, section, keys):
    """The trailing twelve months, and the date those twelve months END on.

    WHICHEVER GENUINE TWELVE-MONTH WINDOW ENDS LATER WINS. A fiscal year IS four
    consecutive quarters; the only question is which window is more recent.

    The old code asked a different question — "can I sum four quarters, and if
    not, what is the latest annual figure?" — and published the answer under a
    heading reading (TTM) with nothing marking it as annual. Measured across 55
    large caps on 16 Aug 2026, THIRTEEN were showing a fiscal year labelled TTM,
    ten of them stale, including all four big banks:

        JPM   showed 181.85B (FY to 2025-12-31)   real TTM 186.94B to 2026-03-31
        BAC   showed 113.10B                      real TTM 116.00B
        WFC   showed  83.70B                      real TTM  84.74B
        GS    showed  58.28B                      real TTM  60.45B
        AMZN  showed 716.92B                      real TTM 742.78B

    Both directions matter, which is why neither source can simply be preferred:

    - AMZN's fiscal year ended in December and a stub June quarter pushed the
      quarterly window back to March. March is still LATER than December, so
      the quarters win and the figure moves forward by $25.9bn.
    - ORCL's fiscal year ends 31 MAY, which is later than the February its
      quarterly window reaches once its own stub is stepped over. The annual
      record wins, and dropping it would have swapped a correct figure for a
      three-month-older one.
    - BRK.B has no complete quarterly window at all. Without the annual it has
      no revenue row.

    The returned date is what makes this honest: a consumer is told which twelve
    months the figure covers rather than assuming it is the twelve months ending
    today.
    """
    q_total, q_end = _ttm_window(quarterly, section, keys)
    a_total, a_end = _latest_annual_window(annual, section, keys)
    if a_total is not None and (q_end is None or (a_end or "") > q_end):
        return a_total, a_end
    return q_total, q_end


def _statement_value(quarterly, annual, section, keys):
    total, _end = _ttm_value(quarterly, annual, section, keys)
    return total


def _latest_balance_value(latest, keys):
    if not latest:
        return None
    return _to_float(_first_value(latest.get("balance_sheet", {}), keys))


def _has_balance_sheet(period) -> bool:
    """Whether a period carries a usable balance sheet.

    NaN is the trap. Firestore holds `nan` — not None — wherever Yahoo sent a
    blank, and ORCL's 2024-Q4 is 66 balance-sheet fields of it. A presence test
    written as `value is not None` counts that as a populated sheet, `_to_float`
    then turns every figure back into None, and enterprise value collapses to
    market cap through a record that looks complete. So the test is finiteness,
    not presence — the same standard `_to_float` applies to the values.
    """
    sheet = (period or {}).get("balance_sheet")
    if not isinstance(sheet, dict):
        return False
    return any(_to_float(value) is not None for value in sheet.values())


# How far back a stated annual total debt may be carried into a period that
# states none. HIS RULE, 18 Aug 2026: one year, no further. Measured carried
# figures ran 0.6 to 5.0 years old; a five-year-old debt position is not
# evidence about a company today.
_DEBT_CARRY_FORWARD_DAYS = 366


def _total_debt(balance, annual):
    """Total debt for enterprise value, and the period it came from.

    EV needs TOTAL debt — long-term plus short-term. The chain below is ordered
    by how close each candidate is to that figure, which the old one was not: it
    reached for `Long Term Debt` ahead of `Long Term Debt And Capital Lease
    Obligation`, taking the NARROWEST of the three. On GOOGL 2025-Q1 that is
    $10.9bn against a real total nearer $22.6bn.

    Reconstruction is second because it is near-exact: measured 18 Aug 2026 over
    938 records holding both parts, long-term + short-term rebuilds Total Debt
    to within 0.5% in 933 of them (99.5%).

    ⚠️ AND WHY THE ANNUAL FALLBACK EXISTS, which was HIS CALL and which I argued
    against before measuring. A quarterly record missing Total Debt usually has
    nothing else either: of 467 such records, 81.2% carry NEITHER a long-term nor
    a short-term line. So the real choice is not "same period versus mixed
    period" — it is "last known total debt versus pretend it is zero", and zero
    is the only answer that is definitely wrong.

    Enbridge is the case in point. Its selected balance sheet has no debt line at
    all, its 2025 annual reports $105.25bn, and enterprise value was being
    computed as market cap minus cash — as though the company carried no debt.

    Short-term debt alone is NEVER used as a stand-in for total debt: measured
    over 1,138 records, it is a median 24.3% of the total and under a tenth of it
    in a third of cases. A quarter of the answer wearing the whole answer's label
    is worse than no answer.

    Returns (value, period_end it came from). The date is returned because a
    figure carried forward from an earlier period must be placeable — the same
    reason balance_sheet_as_of exists.
    """
    sheet = (balance or {}).get("balance_sheet") or {}
    where = (balance or {}).get("period_end")

    total = _to_float(sheet.get("Total Debt"))
    if total is not None:
        return total, where

    long_term = _to_float(sheet.get("Long Term Debt And Capital Lease Obligation"))
    short_term = _to_float(sheet.get("Current Debt And Capital Lease Obligation"))
    if short_term is None:
        short_term = _to_float(sheet.get("Current Debt"))
    if long_term is not None and short_term is not None:
        return long_term + short_term, where
    if long_term is not None:
        return long_term, where

    # Nothing in this period. Fall back to the most recent ANNUAL record that
    # states a total, and say which one it was.
    #
    # ⚠️ ONE YEAR, NO FURTHER — HIS RULE, and the measurement is why. Carried
    # figures ran from 0.6 to 5.0 years old. A five-year-old debt position is not
    # evidence about a company today, and the fact that the oldest ones happened
    # to be zero is luck rather than a reason. Enbridge, the case this fallback
    # exists for, is 0.6 years — comfortably inside.
    #
    # Measured AGAINST THE SELECTED SHEET'S OWN DATE, not the wall clock, so the
    # answer is reproducible and a test does not rot.
    reference = _period_end_date(balance)
    for period in (annual or []):
        stated = _to_float(((period or {}).get("balance_sheet") or {}).get("Total Debt"))
        if stated is None:
            continue
        stated_at = _period_end_date(period)
        if reference is None or stated_at is None:
            continue
        if abs((reference - stated_at).days) > _DEBT_CARRY_FORWARD_DAYS:
            continue
        return stated, period.get("period_end")
    return None, None


def _balance_period(quarterly, annual):
    """The most recent period that actually HOLDS a balance sheet.

    ORDERED BY DATE ACROSS BOTH FREQUENCIES, which is right here and would be
    badly wrong anywhere else. A balance sheet is a point-in-time statement, so
    the annual and the quarterly record dated 2026-05-31 describe the SAME
    balance sheet and either will do. An income statement is a flow: MSFT's
    2026-FY and 2026-Q2 also share 2026-06-30 and differ by 3.7x, which is why
    `_statement_value` keeps the two apart and this deliberately does not.

    Oracle is exactly why it matters. Its fiscal year ends 31 May, so 2026-Q2
    and 2026-FY both end 2026-05-31 — and the quarterly record's balance sheet
    is empty while the annual one holds all 70 fields. Preferring quarterly
    would fall back to FEBRUARY while May sits in the next record along.

    A balance sheet is a point-in-time statement, so unlike revenue it is never
    summed across quarters — it is taken whole from a single period. But it must
    not be taken from the latest period regardless of whether that period has
    one. A quarter Yahoo has opened but not yet populated would otherwise erase
    the balance sheet, while the income statement survives because
    `_statement_value` already falls back through prior quarters and annuals.

    That asymmetry is not hypothetical. Oracle, 16 Aug 2026: Yahoo had not sent
    the May quarter's figures, so cash and debt came back empty while revenue
    was fine — and enterprise value silently became market cap, taking EV/Rev,
    EV/EBITDA, EV/OpCF and EV/FCF down with it on the website, the terminal and
    the partner API at once.

    A balance sheet is always "as of last reported" anyway, so falling back to
    the most recent one we hold is what the figure means, not an approximation
    of it.
    """
    candidates = [p for p in (list(quarterly or []) + list(annual or []))
                  if _has_balance_sheet(p)]
    if not candidates:
        return None
    # Most recent first. Ties — an annual and a quarterly closing the same day —
    # settle on the one holding more of the statement, so a full annual sheet
    # beats a thin quarterly one at the same date.
    return max(
        candidates,
        key=lambda p: (
            str(p.get("period_end") or ""),
            sum(1 for v in (p.get("balance_sheet") or {}).values()
                if _to_float(v) is not None),
        ),
    )


def _estimate_value(data, prefix, metric, periods):
    metric_map = data.get(f"{prefix}_{metric}")
    if isinstance(metric_map, dict):
        for period in periods:
            for key in (period, period.replace("+", "plus")):
                val = _to_float(metric_map.get(key))
                if val is not None:
                    return val

    for period in periods:
        for key in (period, period.replace("+", "plus")):
            period_map = data.get(f"{prefix}_{key}")
            if isinstance(period_map, dict):
                val = _to_float(period_map.get(metric))
                if val is not None:
                    return val
    return None


def _latest_forward_eps(symbol):
    history = _estimate_history(symbol)
    if not history:
        return None
    return _estimate_value(history[0], "eps", "avg", ["+1y", "plus1y", "0y"])


def _firestore_fundamentals(symbol, meta):
    result = {
        "shares": _to_float((meta or {}).get("shares_outstanding")),
        "revenue": None,
        "ebitda": None,
        "net_income": None,
        "operating_cashflow": None,
        "free_cashflow": None,
        "cash": None,
        "debt": None,
        # Which period the balance sheet came from, and None when we hold none
        # at all. Enterprise value depends on it, so a consumer must be able to
        # tell "as of March" from "we do not have one".
        "balance_sheet_as_of": None,
        # The quarter the trailing-twelve-month window ENDS on. Not always the
        # latest quarter: a stub in the newest slot moves the window back one.
        "ttm_as_of": None,
        # Which period the debt figure came from. Usually the same as
        # balance_sheet_as_of, but not when the selected sheet states no debt at
        # all and the last annual total is carried forward.
        "debt_as_of": None,
    }
    all_fins = _all_financials(symbol)
    if not all_fins:
        return result

    quarterly = sorted(
        [f for f in all_fins if f.get("freq") != "FY"],
        key=lambda f: f.get("period_end", ""),
        reverse=True,
    )
    annual = sorted(
        [f for f in all_fins if f.get("freq") == "FY"],
        key=lambda f: f.get("period_end", ""),
        reverse=True,
    )
    latest = quarterly[0] if quarterly else (annual[0] if annual else None)

    if latest:
        shares = _to_float(_first_value(latest.get("income", {}), [
            "Diluted Average Shares",
            "Basic Average Shares",
        ]))
        if shares is not None:
            result["shares"] = shares

    # Revenue is the anchor row, so its window dates the whole TTM block. A
    # consumer reading "$742.78B (TTM)" is entitled to know which twelve months
    # that is — and after a stub quarter it is not the twelve months ending
    # today.
    result["revenue"], result["ttm_as_of"] = _ttm_value(quarterly, annual, "income", [
        "Total Revenue",
    ])
    result["ebitda"] = _statement_value(quarterly, annual, "income", [
        "EBITDA",
        "Normalized EBITDA",
    ])
    result["net_income"] = _statement_value(quarterly, annual, "income", [
        "Net Income",
        "Net Income Common Stockholders",
        "Net Income Continuous Operations",
    ])
    result["operating_cashflow"] = _statement_value(quarterly, annual, "cash_flow", [
        "Operating Cash Flow",
    ])
    result["free_cashflow"] = _statement_value(quarterly, annual, "cash_flow", [
        "Free Cash Flow",
    ])
    if result["free_cashflow"] is None:
        capex = _statement_value(quarterly, annual, "cash_flow", [
            "Capital Expenditure",
        ])
        if result["operating_cashflow"] is not None and capex is not None:
            result["free_cashflow"] = result["operating_cashflow"] + capex

    # NOT `latest` — the most recent period that actually has a balance sheet.
    # See _balance_period: an opened-but-unpopulated quarter used to erase cash
    # and debt while revenue carried on, which turned enterprise value into
    # market cap without saying so.
    balance = _balance_period(quarterly, annual)
    result["balance_sheet_as_of"] = (balance or {}).get("period_end")
    result["cash"] = _latest_balance_value(balance, [
        "Cash And Cash Equivalents",
        "Cash Cash Equivalents And Short Term Investments",
        "Cash And Short Term Investments",
    ])
    result["debt"], result["debt_as_of"] = _total_debt(balance, annual)
    return result


def _calc_ratio(num, denom):
    num = _to_float(num)
    denom = _to_float(denom)
    if num is None or denom in (None, 0):
        return None
    return num / denom


def _market_snapshot(symbol):
    meta = _firestore_meta(symbol)
    if meta is None:
        return None

    quote = _live_quote(symbol)
    fundamentals = _firestore_fundamentals(symbol, meta)

    price = quote.get("price")
    shares = fundamentals.get("shares")
    market_cap = price * shares if price is not None and shares is not None else None
    # FS7: missing is never zero. `debt or 0` used to make "we hold no balance
    # sheet" and "this company has no debt" produce the same enterprise value —
    # market cap exactly — with nothing saying which. Four multiples are built
    # on EV, so one absent balance sheet published five wrong figures.
    #
    # Inside a balance sheet we DO read an absent line as zero: the statement is
    # there and does not report the item. Absent STATEMENT and absent LINE are
    # different claims and are now treated differently.
    has_balance_sheet = fundamentals.get("balance_sheet_as_of") is not None
    debt = fundamentals.get("debt") or 0
    cash = fundamentals.get("cash") or 0
    enterprise_value = (
        market_cap + debt - cash
        if market_cap is not None and has_balance_sheet
        else None
    )
    eps_ttm = _calc_ratio(fundamentals.get("net_income"), shares)
    forward_eps = _latest_forward_eps(symbol)

    return {
        "symbol": symbol,
        "name": meta.get("name") or meta.get("shortName") or symbol,
        "sector": meta.get("sector", ""),
        "industry": meta.get("industry", ""),
        "summary": meta.get("summary") or meta.get("longBusinessSummary", ""),
        "beta": meta.get("beta"),
        "price": price,
        "change": quote.get("change"),
        "change_pct": quote.get("change_pct"),
        "volume": quote.get("volume"),
        "fifty_two_week_high": quote.get("fifty_two_week_high"),
        "fifty_two_week_low": quote.get("fifty_two_week_low"),
        # Already fetched by _live_quote and previously dropped here, so the
        # chart had the day's range and nothing else could reach it. Passing it
        # through costs no extra Yahoo call.
        "day_high": quote.get("day_high"),
        "day_low": quote.get("day_low"),
        "shares": shares,
        "market_cap": market_cap,
        "enterprise_value": enterprise_value,
        # Which quarter the balance sheet behind EV came from, and None when we
        # hold none. A portfolio manager reading ORCL's enterprise value while
        # Yahoo is late with the May quarter deserves to know it stands on
        # February's balance sheet.
        "balance_sheet_as_of": fundamentals.get("balance_sheet_as_of"),
        # The twelve months every (TTM) figure below actually covers.
        "ttm_as_of": fundamentals.get("ttm_as_of"),
        # Differs from balance_sheet_as_of when the selected sheet carries no
        # debt line and an earlier annual total was used instead.
        "debt_as_of": fundamentals.get("debt_as_of"),
        "revenue": fundamentals.get("revenue"),
        "ebitda": fundamentals.get("ebitda"),
        "net_income": fundamentals.get("net_income"),
        "eps_ttm": eps_ttm,
        "forward_eps": forward_eps,
        "pe_ttm": _calc_ratio(price, eps_ttm),
        "forward_pe": _calc_ratio(price, forward_eps),
        "ps_ttm": _calc_ratio(market_cap, fundamentals.get("revenue")),
        "ev_revenue": _calc_ratio(enterprise_value, fundamentals.get("revenue")),
        "ev_ebitda": _calc_ratio(enterprise_value, fundamentals.get("ebitda")),
        "ev_opcf": _calc_ratio(enterprise_value, fundamentals.get("operating_cashflow")),
        "ev_fcf": _calc_ratio(enterprise_value, fundamentals.get("free_cashflow")),
        "dividend_yield": meta.get("dividend_yield"),
    }


def _company_summary(symbol, snap):
    # Firestore-first: the stored description avoids Yahoo's slow .info call.
    stored = (snap or {}).get("summary", "")
    if stored:
        return stored
    info = _yahoo(symbol)
    return info.get("longBusinessSummary", "")


def _get_diluted_shares(symbol, info):
    for f in _all_financials(symbol):
        if f.get("freq") == "Q":
            val = f.get("income", {}).get("Diluted Average Shares")
            if val is not None:
                return _count(val)
            break
    return _count(info.get("sharesOutstanding"))


# ── /AAPL — Overview + Valuation ───────────────────────────────────────────


def cmd_overview(symbol, info=None):
    console.print(f"[grey70]Reading Yahoo Finance and TEK2day data for {symbol}...[/grey70]")
    snap = _market_snapshot(symbol)
    if not snap:
        console.print(f"[yellow]{symbol}: no TEK2day fundamentals found[/yellow]")
        return

    name = snap.get("name") or symbol
    price = snap.get("price")
    change = snap.get("change")
    change_pct = snap.get("change_pct")
    volume = snap.get("volume")

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
        ("Market Cap", _dollar(snap.get("market_cap")),
         "P/E TTM (GAAP)", _ratio(snap.get("pe_ttm"))),
        ("Diluted Shares", _count(snap.get("shares")),
         "Fwd P/E (Est)", _ratio(snap.get("forward_pe"))),
        ("52wk High", _price(snap.get("fifty_two_week_high")),
         "P/S (TTM)", _ratio(snap.get("ps_ttm"))),
        ("52wk Low", _price(snap.get("fifty_two_week_low")),
         "EV/EBITDA (TTM)", _ratio(snap.get("ev_ebitda"))),
        ("Sector", str(snap.get("sector") or "N/A"),
         "EV/Rev (TTM)", _ratio(snap.get("ev_revenue"))),
        ("Industry", str(snap.get("industry") or "N/A")[:26],
         "", ""),
        ("Beta", _num(snap.get("beta")),
         "", ""),
    ]
    for r in rows:
        t.add_row(*r)

    desc = _company_summary(symbol, snap)
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
        console.print("[yellow]Estimates require TEK2day data access.[/yellow]")
        return

    history = _estimate_history(symbol)
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
                    row.append(_analyst_count(val))
                elif prefix == "rev" and mk in ("avg", "high", "low", "yearagorevenue"):
                    row.append(_dollar(val))
                elif prefix == "eps" and mk in ("avg", "high", "low", "yearagoeps"):
                    # ⚠️ _eps, NOT _price. An estimate is still a per-share
                    # figure, so it follows the accounting convention like every
                    # other one — ($0.58), not $-0.58. This table was the last
                    # surface calling _price for earnings, which is why AAPG read
                    # `$-0.58` here and `($0.58)` on the income statement and the
                    # comp card for the same company. `or "N/A"` because _eps
                    # returns blank for a missing value and these cells say N/A.
                    row.append(_eps(val) or "N/A")
                else:
                    row.append(_num(val))
            t.add_row(*row)

        console.print(t)

    console.print(f"[grey70]  As of {pull_date} · Source: Yahoo Finance, TEK2day[/grey70]")


# ── Financial statement helpers ────────────────────────────────────────────

INCOME_FIELDS = [
    "Total Revenue", "Cost Of Revenue", "Gross Profit",
    "Operating Expense", "Operating Income", "EBITDA",
    "Interest Expense", "Pretax Income", "Tax Provision",
    "Net Income", "Net Income Common Stockholders",
    ("Basic EPS", "Reported EPS (Basic)"),
    ("Diluted EPS", "Reported EPS (Diluted)"),
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


def _show_financials(symbol, section, fields, title, snapshot=False):
    if not _has_firestore():
        console.print("[yellow]Financials require TEK2day data access.[/yellow]")
        return

    all_fins = _all_financials(symbol)
    if not all_fins:
        console.print(f"[yellow]No financials stored for {symbol}[/yellow]")
        return

    quarterly = sorted([f for f in all_fins if f.get("freq") != "FY"], key=lambda f: f.get("period_end", ""))[-4:]
    annual = sorted([f for f in all_fins if f.get("freq") == "FY"], key=lambda f: f.get("period_end", ""))[-4:]

    for freq_label, periods in [("Quarterly", quarterly), ("Annual", annual)]:
        if not periods:
            continue

        if snapshot:
            latest_date = periods[0].get("period_end", "")
            heading = f"{symbol} — {title} as of {latest_date}"
        else:
            heading = f"{symbol} — {freq_label} {title}"
        t = Table(
            title=heading,
            box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
            expand=False, pad_edge=False,
        )
        t.add_column("", style="bold", no_wrap=True)
        for p in periods:
            col_header = p.get("period_end", p["period"])
            t.add_column(str(col_header), justify="right", no_wrap=True)

        has_data = False
        for field in fields:
            if isinstance(field, tuple):
                key, label = field
            else:
                key, label = field, field
            vals = [statement_cell(p, section, key) for p in periods]
            if not any(v is not None for v in vals):
                continue
            has_data = True
            row = [label] + [statement_display(section, key, v) for v in vals]
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


def balance_sheet_periods(all_fins, limit=8):
    """One column per DATE for a balance sheet, choosing the fuller record.

    A balance sheet is point-in-time, so an annual and a quarterly record
    closing the same day describe the same sheet — but they are routinely NOT
    equivalent, because Yahoo posts a skeleton within hours of a release and
    fills it in later.

        GOOGL 2024-12-31   2024-FY  63 fields, Total Assets $450.3B
                           2024-Q4   4 fields, Total Assets missing

    Keeping whichever came first showed an almost empty 2024 column while we
    held everything. Measured 18 Aug 2026: 281 of 700 companies (40%) gain data
    from this, 14,166 figures recovered, no period worse. JPM was discarding
    $4,002.8B of total assets.

    Ties on field count go to the ANNUAL — GOOGL 2025-FY carries Total Debt at
    $59.3B where 2025-Q4, with the same field count, carries none.

    ⚠️ SHARED BY THE TERMINAL AND THE WEBSITE ON PURPOSE. Both had their own copy
    of this loop and only one was fixed, so /GOOGL bal was right on the site and
    wrong in the terminal.
    """
    def weight(financial):
        """Rank two records closing the same day.

        ⚠️ THE ANNUAL WINS OUTRIGHT — his ruling, 18 Aug. A company's year end is
        also its fourth-quarter end, so Yahoo sends TWO balance sheets for that
        one day. A balance sheet is a photograph of a single date, so there can
        only be one right answer, and where they conflict Yahoo is in error:

            BP, 31 December 2025    annual cash $31.8B
                                 quarterly cash $36.6B

        $4.8bn apart for the same company on the same day. The annual figure is
        the audited, fuller presentation and is the one to trust.

        The first term is a GUARD, not a preference: an annual record that holds
        no usable balance sheet must not beat a quarterly one that does, or
        GOOGL's empty 2024-Q4 problem simply returns reversed and the column goes
        blank again. So: a real sheet first, then the annual, then the fuller.
        """
        block = financial.get("balance_sheet") or {}
        populated = sum(1 for v in block.values() if _to_float(v) is not None)
        is_annual = 1 if str(financial.get("period") or "").endswith("-FY") else 0
        return (1 if populated else 0, is_annual, populated)

    best = {}
    for financial in (all_fins or []):
        period_end = str(financial.get("period_end", ""))
        if period_end not in best or weight(financial) > weight(best[period_end]):
            best[period_end] = financial
    ordered = sorted(best.values(), key=lambda f: str(f.get("period_end", "")))
    return ordered[-limit:] if limit else ordered


def statement_cell(period, section, key):
    """The value a statement row should show — derived where we can do better.

    EPS is computed from net income and the share count rather than read, so a
    stored figure missing its minus sign cannot reach a reader, and so a
    per-share number cannot contradict the share count beside it now that share
    counts follow Yahoo's current basis. Everything else is read as stored.
    """
    block = (period or {}).get(section) or {}
    if section == "income" and key in EPS_SHARE_COUNT:
        derived = computed_eps(block, key)
        if derived is not None:
            return derived
    return block.get(key)


def statement_display(section, key, value):
    """Render one statement cell. Per-share figures use the accounting form."""
    if section == "income" and key in EPS_SHARE_COUNT:
        return _eps(value)
    return _fin(value)


def cmd_income(symbol):
    _show_financials(symbol, "income", INCOME_FIELDS, "Income Statement")


def cmd_balance(symbol):
    if not _has_firestore():
        console.print("[yellow]Financials require TEK2day data access.[/yellow]")
        return
    all_fins = _all_financials(symbol)
    if not all_fins:
        console.print(f"[yellow]No financials stored for {symbol}[/yellow]")
        return
    periods = balance_sheet_periods(all_fins)
    t = Table(
        title=f"{symbol} — Balance Sheet",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        expand=False, pad_edge=False,
    )
    t.add_column("", style="bold", no_wrap=True)
    for p in periods:
        t.add_column(str(p.get("period_end", p["period"])), justify="right", no_wrap=True)
    for field in BALANCE_FIELDS:
        if isinstance(field, tuple):
            key, label = field
        else:
            key, label = field, field
        vals = [p.get("balance_sheet", {}).get(key) for p in periods]
        if not any(v is not None for v in vals):
            continue
        t.add_row(label, *[_fin(v) for v in vals])
    console.print(t)


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


# ── Short Interest section used inside /AAPL summary ──────────────────────


def cmd_short(symbol, info=None):
    meta = _firestore_meta(symbol)
    if meta is None:
        meta = {}
    source = "Yahoo Finance, TEK2day"
    values = {
        "date": _first_value(meta, ["date_short_interest", "dateShortInterest"]),
        "shares": _first_value(meta, ["shares_short", "sharesShort"]),
        "ratio": _first_value(meta, ["short_ratio", "shortRatio"]),
        "float_pct": _first_value(meta, ["short_percent_of_float", "shortPercentOfFloat"]),
        "shares_out_pct": _first_value(meta, ["shares_percent_shares_out", "sharesPercentSharesOut"]),
    }
    if not any(v is not None for v in values.values()):
        info = info or _yahoo(symbol)
        source = "Yahoo Finance, TEK2day"
        values = {
            "date": info.get("dateShortInterest"),
            "shares": info.get("sharesShort"),
            "ratio": info.get("shortRatio"),
            "float_pct": info.get("shortPercentOfFloat"),
            "shares_out_pct": info.get("sharesPercentSharesOut"),
        }
    if not any(v is not None for v in values.values()):
        console.print(f"[yellow]No short interest found for {symbol}[/yellow]")
        return

    t = Table(
        title=f"{symbol} — Short Interest",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        width=TABLE_WIDTH,
    )
    t.add_column("Metric", style="bold", width=24)
    t.add_column("Value", justify="right", width=16)

    short_date = values["date"]
    if isinstance(short_date, (int, float)):
        short_date = datetime.fromtimestamp(short_date).strftime("%Y-%m-%d")

    t.add_row("Shares Short", _count(values["shares"]))
    t.add_row("Short Ratio", _num(values["ratio"]))
    t.add_row("Short % of Float", _pct(values["float_pct"]))
    t.add_row("Short % of Shares Out", _pct(values["shares_out_pct"]))
    t.add_row("As of", str(short_date or "N/A"))

    console.print(t)
    console.print(f"[grey70]  Source: {source}[/grey70]")


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
        console.print("[yellow]Chart requires TEK2day data access.[/yellow]")
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
    lookup = CEORATER_ALIASES.get(symbol, symbol)
    if TEK2DAY_API_URL:
        try:
            resp = requests.get(
                f"{TEK2DAY_API_URL}/api/ceo/{lookup}",
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "error" not in data:
                    return [data]
        except Exception:
            pass
    if CEORATER_API_KEY:
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
            t.add_row("CEO", str(ceo.get("CEO Name") or ceo.get("ceo") or "N/A"))
            founder = ceo.get("Founder (Y/N)") or ceo.get("founderCEO")
            t.add_row("Founder CEO", "Yes" if founder in (True, "Y") else "No")
            tenure = ceo.get("Tenure (years)") or ceo.get("tenure")
            if tenure:
                t.add_row("Tenure", str(tenure))
            score = ceo.get("CEORaterScore") or ceo.get("ceoraterScore")
            t.add_row("CEORater Score", _num(score, 0) if score else "N/A")
            alpha = ceo.get("AlphaScore") or ceo.get("alphaScore")
            t.add_row("Alpha Score", _num(alpha, 0) if alpha else "N/A")
            t.add_row("Comp Score", str(ceo.get("CompScore") or ceo.get("compScore") or "N/A"))
            rev_cagr = ceo.get("Revenue CAGR (Adj.)")
            if rev_cagr:
                t.add_row("Revenue CAGR", str(rev_cagr))
            tsr = ceo.get("TSR During Tenure")
            if tsr:
                t.add_row("TSR (Tenure)", str(tsr))
            avg_tsr = ceo.get("Avg. Annual TSR")
            if avg_tsr:
                t.add_row("Avg Annual TSR", str(avg_tsr))
            tsr_spy = ceo.get("TSR vs. SPY")
            if tsr_spy:
                t.add_row("TSR vs SPY", str(tsr_spy))
            avg_tsr_spy = ceo.get("Avg Annual TSR vs. SPY")
            if avg_tsr_spy:
                t.add_row("Avg Annual TSR vs SPY", str(avg_tsr_spy))
            comp = ceo.get("Compensation ($ millions)") or ceo.get("compensationMM")
            if comp:
                t.add_row("Compensation", str(comp))
            cost_per_tsr = ceo.get("CEO Compensation Cost / 1% Avg TSR")
            if cost_per_tsr:
                t.add_row("Comp Cost / 1% TSR", str(cost_per_tsr))

        console.print(t)
    else:
        info = _yahoo(symbol) or {}
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
        else:
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
        t = _yf().Ticker(symbol)
        news = t.news
        if not news:
            console.print(f"[yellow]No recent news for {symbol}[/yellow]")
            return

        items = news[:10]
        for item in items:
            content = item.get("content", item)
            title = content.get("title", "")
            provider = content.get("provider", {})
            publisher = provider.get("displayName", "") if isinstance(provider, dict) else str(provider)
            click_url = content.get("clickThroughUrl", {})
            link = click_url.get("url", "") if isinstance(click_url, dict) else content.get("link", "")
            pub_date = content.get("pubDate", "")
            date_str = ""
            if pub_date and isinstance(pub_date, str):
                try:
                    date_str = pub_date[:16].replace("T", " ")
                except Exception:
                    pass
            elif isinstance(pub_date, (int, float)):
                try:
                    date_str = datetime.fromtimestamp(pub_date).strftime("%Y-%m-%d %H:%M")
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
    if len(symbols) > 6:
        console.print("[yellow]Max 6 tickers for comparison. Using first 6.[/yellow]")
        symbols = symbols[:6]
    console.print(
        f"[grey70]Reading Yahoo Finance and TEK2day data for {', '.join(symbols)}...[/grey70]"
    )

    snapshots = {}
    for sym in symbols:
        snap = _market_snapshot(sym)
        if snap:
            snapshots[sym] = snap
        else:
            console.print(f"[yellow]{sym}: no TEK2day fundamentals[/yellow]")

    if not snapshots:
        return

    t = Table(
        title="Comparison",
        box=box.SIMPLE_HEAVY, border_style="green", title_style="bold",
        expand=False, pad_edge=False,
    )
    t.add_column("", style="bold", no_wrap=True)
    col_width = max(12, max(len(s) for s in snapshots) + 2)
    for sym in snapshots:
        name = snapshots[sym].get("name", sym)
        t.add_column(f"{sym}\n[grey70]{name}[/grey70]", justify="right", width=col_width)

    metrics = [
        ("Price", lambda i: _price(i.get("price"))),
        ("Market Cap", lambda i: _dollar(i.get("market_cap"))),
        ("EV", lambda i: _dollar(i.get("enterprise_value"))),
        ("Revenue (TTM)", lambda i: _dollar(i.get("revenue"))),
        ("EBITDA (TTM)", lambda i: _dollar(i.get("ebitda"))),
        ("Net Income (TTM)", lambda i: _dollar(i.get("net_income"))),
        ("EPS (TTM)", lambda i: _eps(i.get("eps_ttm"))),
        ("EPS (Fwd)", lambda i: _eps(i.get("forward_eps"))),
        ("P/E TTM (GAAP)", lambda i: _ratio(i.get("pe_ttm"))),
        ("Fwd P/E (Est)", lambda i: _ratio(i.get("forward_pe"))),
        ("P/S (TTM)", lambda i: _ratio(i.get("ps_ttm"))),
        ("EV/Rev (TTM)", lambda i: _ratio(i.get("ev_revenue"))),
        ("EV/EBITDA (TTM)", lambda i: _ratio(i.get("ev_ebitda"))),
        ("EV/OpCF (TTM)", lambda i: _ratio(i.get("ev_opcf"))),
        ("EV/FCF (TTM)", lambda i: _ratio(i.get("ev_fcf"))),
    ]

    for idx, (label, fn) in enumerate(metrics):
        style = "on grey11" if idx % 2 == 1 else None
        row = [label] + [fn(snapshots[sym]) for sym in snapshots]
        t.add_row(*row, style=style)

    console.print(t)


# ── /AAPL — Full Report ───────────────────────────────────────────────────


def cmd_full(symbol):
    cmd_overview(symbol)
    console.print("[grey70]  Source: Yahoo Finance, TEK2day[/grey70]")
    console.print()
    cmd_estimates(symbol)
    console.print()
    cmd_short(symbol)


# ── Command router ─────────────────────────────────────────────────────────

SUBCMDS = {
    "inc": cmd_income,
    "bal": cmd_balance,
    "cf": cmd_cashflow,
    "mgmt": cmd_mgmt,
    "filings": cmd_filings,
    "news": cmd_news,
}


def main():
    _print_banner()
    _check_for_update()

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

        if first == "comp":
            if len(parts) < 3:
                console.print("[yellow]Usage: /comp AAPL MSFT (up to 6 tickers)[/yellow]")
                continue
            if len(parts) > 7:
                console.print("[yellow]Maximum 6 tickers at a time.[/yellow]")
                continue
            cmd_compare([p.upper() for p in parts[1:]])
            continue

        symbol = parts[0].upper()
        subcmd = parts[1].lower() if len(parts) > 1 else None

        if len(parts) > 2:
            console.print("[yellow]Ticker commands accept one optional subcommand.[/yellow]")
            continue

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
