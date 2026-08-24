"""Per-ticker metrics for watchlist analysis.

Pure functions over stored rows: no Firestore, no network, no live quotes. The
caller loads price and estimate history and passes it in, which is what makes
this testable and what keeps the endpoint free of the six-symbol cap that
`/comparisons` carries.

WHY NO LIVE QUOTES. `/comparisons` caps at six because every column is a live
`terminal._market_snapshot`, and six of those in series times a request out. A
watchlist is not six names. Everything here comes from the nightly price pull and
the weekly estimate pull that are already on disk, so a hundred symbols cost a
hundred Firestore reads and nothing else.

WHAT THE ANALYSIS NEEDS. Price direction alone is not a conclusion. The same
green number means opposite things depending on what the multiple did beside it:

    price up,  forward multiple down  -> earnings outrunning the stock
    price up,  forward multiple up    -> a re-rating

So every row carries the price trend AND the forward multiple AND that multiple
at prior periods. A row that reported only the move would invite exactly the
wrong reading.
"""
from __future__ import annotations

from typing import Any

# Trading days, not calendar days, because the rows are trading sessions.
MA_WINDOW = 10
ONE_MONTH = 21
THREE_MONTHS = 63
ONE_YEAR = 252

# Enough history for the one-year lookback with room for holidays and halts.
PRICE_HISTORY_LIMIT = 300

# The estimate pull is weekly, so this is comfortably more than a year.
ESTIMATE_HISTORY_LIMIT = 90


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _closes(price_rows: list[dict]) -> list[tuple[str, float]]:
    """(date, close) oldest-first, dropping rows without a usable close.

    get_prices_history already sorts ascending; this does not re-sort, it only
    discards what cannot be arithmetic. A row with a null close is a real thing
    in this data and must not become a zero.
    """
    out: list[tuple[str, float]] = []
    for row in price_rows or []:
        if not isinstance(row, dict):
            continue
        close = row.get("close")
        date = row.get("date")
        if _finite(close) and isinstance(date, str) and date:
            out.append((date, float(close)))
    return out


def _pct_change(current: float, prior: float) -> float | None:
    if not _finite(current) or not _finite(prior) or prior == 0:
        return None
    return (current - prior) / prior * 100.0


def moving_average(closes: list[float], window: int = MA_WINDOW) -> float | None:
    """Mean of the last `window` closes, or None when there are not that many.

    Returns None rather than averaging whatever is available: a "10-day average"
    computed from four days is a different statistic wearing the same label, and
    it would be read as the real one.
    """
    if len(closes) < window or window <= 0:
        return None
    return sum(closes[-window:]) / window


def _lookback_close(series: list[tuple[str, float]], sessions: int) -> float | None:
    if len(series) <= sessions:
        return None
    return series[-(sessions + 1)][1]


def _estimate_on_or_before(estimate_rows: list[dict], date: str) -> float | None:
    """Forward EPS consensus in effect on `date`.

    Prior-period multiples have to use the estimate that stood AT the time, not
    today's. Using the current estimate against an old price would attribute this
    quarter's revisions to last year's multiple and manufacture a re-rating that
    never happened.
    """
    best_date, best_eps = "", None
    for row in estimate_rows or []:
        if not isinstance(row, dict):
            continue
        row_date = row.get("date")
        eps = row.get("eps_avg")
        if not isinstance(row_date, str) or not row_date or not _finite(eps):
            continue
        if row_date <= date and row_date >= best_date:
            best_date, best_eps = row_date, float(eps)
    return best_eps


def _forward_pe(price: float | None, eps: float | None) -> float | None:
    """Price over forward EPS, or None.

    A negative or zero forward EPS yields no meaningful multiple — a loss-making
    company does not have a low P/E, it has none — so it is omitted rather than
    reported as a negative number that sorts below cheap.
    """
    if not _finite(price) or not _finite(eps) or eps <= 0:
        return None
    return price / eps


def build_row(
    symbol: str,
    name: str | None,
    price_rows: list[dict],
    estimate_rows: list[dict],
) -> dict[str, Any]:
    """One watchlist row. Never raises; thin data yields nulls, not an error."""
    series = _closes(price_rows)
    row: dict[str, Any] = {
        "symbol": symbol,
        "name": name or None,
        "covered": bool(series),
        "price": None,
        "price_date": None,
        "ma_10": None,
        "price_vs_ma_10_pct": None,
        "trend": None,
        "change_1m_pct": None,
        "change_3m_pct": None,
        "forward_eps": None,
        "forward_pe": None,
        "forward_pe_prior_q": None,
        "forward_pe_prior_y": None,
        "forward_pe_change_q_pct": None,
        "forward_pe_change_y_pct": None,
    }
    if not series:
        return row

    closes = [close for _, close in series]
    last_date, last_close = series[-1]
    row["price"] = last_close
    row["price_date"] = last_date

    average = moving_average(closes)
    if average is not None:
        row["ma_10"] = average
        row["price_vs_ma_10_pct"] = _pct_change(last_close, average)
        row["trend"] = "above" if last_close >= average else "below"

    row["change_1m_pct"] = _pct_change(last_close, _lookback_close(series, ONE_MONTH))
    row["change_3m_pct"] = _pct_change(last_close, _lookback_close(series, THREE_MONTHS))

    current_eps = _estimate_on_or_before(estimate_rows, last_date)
    row["forward_eps"] = current_eps
    row["forward_pe"] = _forward_pe(last_close, current_eps)

    for sessions, pe_key, change_key in (
        (THREE_MONTHS, "forward_pe_prior_q", "forward_pe_change_q_pct"),
        (ONE_YEAR, "forward_pe_prior_y", "forward_pe_change_y_pct"),
    ):
        if len(series) <= sessions:
            continue
        prior_date, prior_close = series[-(sessions + 1)]
        prior_pe = _forward_pe(prior_close, _estimate_on_or_before(estimate_rows, prior_date))
        row[pe_key] = prior_pe
        if prior_pe is not None and row["forward_pe"] is not None:
            row[change_key] = _pct_change(row["forward_pe"], prior_pe)

    return row


def _pct_change_or_none(current, prior):
    """Public-ish alias kept for callers that want the same rounding rules."""
    return _pct_change(current, prior)
