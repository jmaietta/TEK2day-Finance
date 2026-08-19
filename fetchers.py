"""
yfinance data fetchers.

Each function pulls a specific data type for a single ticker and returns
a normalized dict ready for Firestore storage.
"""
import logging
import math
import os
import platform
import sys
from datetime import date, datetime, timezone

import yfinance as yf

logger = logging.getLogger("ydp.fetchers")

# ⚠️ THE FIRST FEW FAILURES CARRY THEIR TRACEBACK; THE REST DO NOT.
#
# A one-line `logger.error(..., exc)` made the 13-19 Aug 2026 price outage
# undiagnosable for a week. Every one of 9,911 tickers failed with the same
# pandas message and NOT ONE said where. The library worked on every version
# tested locally, so without a stack the fault could not be located at all.
#
# Full tracebacks for all 9,911 would be ~10,000 stack dumps a night, which is
# its own kind of unreadable and costs money to store. The first three are
# enough: when a run fails wholesale it fails identically every time.
_TRACEBACK_BUDGET = int(os.environ.get("FETCH_TRACEBACK_BUDGET", "3"))
_traceback_count = 0


def _log_fetch_failure(kind: str, symbol: str, exc: Exception) -> None:
    """One line per failure, with a stack for the first few of a run."""
    global _traceback_count
    if _traceback_count < _TRACEBACK_BUDGET:
        _traceback_count += 1
        logger.error(
            "%s: %s fetch failed (%d of %d with traceback) | python=%s "
            "platform=%s yfinance=%s TZ=%s",
            symbol, kind, _traceback_count, _TRACEBACK_BUDGET,
            sys.version.split()[0], platform.platform(),
            getattr(yf, "__version__", "?"), os.environ.get("TZ", "unset"),
            exc_info=True,
        )
    else:
        logger.error("%s: %s fetch failed: %s", symbol, kind, exc)


def fetch_ticker_info(symbol: str) -> dict | None:
    """Fetch metadata: name, sector, industry, exchange, market cap."""
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        if not info.get("shortName"):
            logger.warning("%s: no info returned", symbol)
            return None
        return {
            "symbol": symbol,
            "name": info.get("shortName", ""),
            "long_name": info.get("longName", ""),
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "exchange": info.get("exchange", ""),
            "market_cap": info.get("marketCap"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "float_shares": info.get("floatShares"),
            "currency": info.get("currency", "USD"),
            "active": True,
        }
    except Exception as exc:
        logger.error("%s: info fetch failed: %s", symbol, exc)
        return None


def fetch_estimates(symbol: str) -> dict | None:
    """
    Fetch consensus EPS and revenue estimates for current quarter,
    next quarter, current year, and next year.
    """
    try:
        t = yf.Ticker(symbol)
        today = date.today().isoformat()

        eps_est = t.earnings_estimate
        rev_est = t.revenue_estimate

        if eps_est is None or eps_est.empty:
            logger.warning("%s: no earnings estimates available", symbol)
            return None

        result = {"date": today, "symbol": symbol}

        for df, prefix in [(eps_est, "eps"), (rev_est, "rev")]:
            if df is None or df.empty:
                continue
            for col in df.columns:
                col_key = str(col).replace(" ", "_").replace("+", "plus").lower()
                col_data = {}
                for idx, val in df[col].items():
                    idx_key = str(idx).replace(" ", "_").lower()
                    if val is not None:
                        try:
                            col_data[idx_key] = float(val)
                        except (ValueError, TypeError):
                            col_data[idx_key] = str(val)
                if col_data:
                    result[f"{prefix}_{col_key}"] = col_data

        if len(result) <= 2:
            logger.warning("%s: estimates parsed but empty", symbol)
            return None

        return result

    except Exception as exc:
        logger.error("%s: estimates fetch failed: %s", symbol, exc)
        return None


def yahoo_epoch_seconds(value):
    """Yahoo's quote timestamp as epoch SECONDS, or None.

    ⚠️ THIS EXISTS BECAUSE `ts + offset` TOOK THE PRICE PIPELINE DOWN FOR A WEEK.
    13-19 Aug 2026: every one of 9,911 tickers failed nightly, the job reported
    success, and Firestore's prices froze. Two upstream changes lined up —
    yfinance began returning `regularMarketTime` as a pandas Timestamp instead
    of an int, and pandas 3 made `Timestamp + int` a TypeError.

    ⚠️ `int(value)` IS NOT THE FIX AND MUST NOT BE USED. On a pandas Timestamp
    it raises outright, and where a caller swallows that (app.py did) the quote
    silently vanishes instead of failing loudly. `.timestamp()` is the only
    accessor that means SECONDS for both datetime and pandas Timestamp.

    Accepts what Yahoo has actually been observed to send: int, float, numeric
    string, datetime, pandas Timestamp. Anything else is None, never a guess.
    """
    if value is None or isinstance(value, bool):
        return None
    # datetime and pandas Timestamp both expose .timestamp() in SECONDS.
    as_epoch = getattr(value, "timestamp", None)
    if callable(as_epoch):
        try:
            return float(as_epoch())
        except Exception:  # noqa: BLE001 - a broken clock is not a crash
            return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return None if seconds != seconds or seconds in (float("inf"), float("-inf")) else seconds


def yahoo_local_date(timestamp, gmtoffset=0):
    """The exchange's LOCAL trading date for a Yahoo quote, or None.

    gmtoffset converts the quote timestamp to the exchange's own date without
    needing a timezone database — a bar stamped 21:00 UTC belongs to the New
    York session that already closed, not to the next day.

    ⚠️ ONE COPY, CALLED BY ALL THREE SURFACES. This line previously existed
    four times — fetchers, terminal and twice in app — and when the type change
    landed, app happened to be written differently and the other two broke. The
    project has been bitten by exactly this before: the balance-sheet fix landed
    on the website and left the terminal blank, and the status doc's ruling was
    "Do not reintroduce a second copy." It got reintroduced. This is the one copy.
    """
    seconds = yahoo_epoch_seconds(timestamp)
    if seconds is None:
        return None
    offset = yahoo_epoch_seconds(gmtoffset) or 0.0
    try:
        return datetime.fromtimestamp(seconds + offset, tz=timezone.utc).date()
    except (OverflowError, OSError, ValueError):
        return None


def _bar_from_metadata(meta: dict, symbol: str) -> dict | None:
    """
    Build a daily bar from the chart response metadata, which carries the
    official quote (regularMarketPrice/Time, day high/low, volume). Used
    when Yahoo's bar feed has not yet published the latest day's OHLC.
    Open is not in the metadata; it stays None until the official bar lands.
    """
    ts = meta.get("regularMarketTime")
    close = meta.get("regularMarketPrice")
    if not ts or close is None:
        return None

    def _round(value):
        return None if value is None else round(float(value), 4)

    local_date = yahoo_local_date(ts, meta.get("gmtoffset") or 0)
    if local_date is None:
        logger.warning("%s: unusable quote timestamp %r", symbol, ts)
        return None
    bar_date = local_date.strftime("%Y-%m-%d")
    volume = meta.get("regularMarketVolume")
    return {
        "date": bar_date,
        "symbol": symbol,
        "open": None,
        "high": _round(meta.get("regularMarketDayHigh")),
        "low": _round(meta.get("regularMarketDayLow")),
        "close": _round(close),
        "volume": int(volume) if volume is not None else None,
    }


def fetch_prices(symbol: str, period: str = "5d") -> list[dict]:
    """
    Fetch OHLCV price history. Default last 5 days to catch up
    after weekends/holidays. For backfill, pass period='max'.
    """
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period=period, auto_adjust=True)

        if hist is None or hist.empty:
            logger.warning("%s: no price history returned", symbol)
            return []

        def _num(value):
            value = float(value)
            return None if math.isnan(value) else round(value, 4)

        rows = []
        for idx, row in hist.iterrows():
            close = _num(row["Close"])
            if close is None:
                continue  # NaN close is unusable (and not JSON-serializable)
            volume = float(row["Volume"])
            rows.append({
                "date": idx.strftime("%Y-%m-%d"),
                "symbol": symbol,
                "open": _num(row["Open"]),
                "high": _num(row["High"]),
                "low": _num(row["Low"]),
                "close": close,
                "volume": None if math.isnan(volume) else int(volume),
            })

        # If the latest day's bar was NaN (not yet published by Yahoo's bar
        # feed), build it from the official quote in the same response. The
        # next pull overwrites it with the official bar.
        meta_bar = _bar_from_metadata(getattr(t, "history_metadata", None) or {}, symbol)
        if meta_bar and meta_bar["date"] not in {r["date"] for r in rows}:
            rows.append(meta_bar)
        return rows

    except Exception as exc:
        _log_fetch_failure("price", symbol, exc)
        return []


def _build_financial_docs(symbol, income, balance, cashflow, freq):
    """Shared logic for quarterly and annual financials.
    freq: 'Q' for quarterly, 'FY' for annual.
    """
    if income is None or income.empty:
        return []

    results = []
    for period_dt in income.columns:
        if freq == "Q":
            period_str = period_dt.strftime("%Y-Q") + str((period_dt.month - 1) // 3 + 1)
        else:
            period_str = period_dt.strftime("%Y") + "-FY"

        doc = {
            "period": period_str,
            "period_end": period_dt.strftime("%Y-%m-%d"),
            "symbol": symbol,
            "freq": freq,
            "income": {},
            "balance_sheet": {},
            "cash_flow": {},
        }

        if income is not None and period_dt in income.columns:
            for field, val in income[period_dt].items():
                if val is not None:
                    try:
                        doc["income"][str(field)] = float(val)
                    except (ValueError, TypeError):
                        pass

        if balance is not None and period_dt in balance.columns:
            for field, val in balance[period_dt].items():
                if val is not None:
                    try:
                        doc["balance_sheet"][str(field)] = float(val)
                    except (ValueError, TypeError):
                        pass

        if cashflow is not None and period_dt in cashflow.columns:
            for field, val in cashflow[period_dt].items():
                if val is not None:
                    try:
                        doc["cash_flow"][str(field)] = float(val)
                    except (ValueError, TypeError):
                        pass

        results.append(doc)

    return results


def fetch_financials(symbol: str) -> list[dict]:
    """
    Fetch quarterly income statement, balance sheet, and cash flow.
    Returns one dict per reporting period with all three merged.
    """
    try:
        t = yf.Ticker(symbol)
        return _build_financial_docs(
            symbol, t.quarterly_income_stmt, t.quarterly_balance_sheet, t.quarterly_cashflow, "Q"
        )
    except Exception as exc:
        logger.error("%s: quarterly financials fetch failed: %s", symbol, exc)
        return []


def fetch_annual_financials(symbol: str) -> list[dict]:
    """
    Fetch annual income statement, balance sheet, and cash flow.
    Returns one dict per fiscal year with all three merged.
    """
    try:
        t = yf.Ticker(symbol)
        return _build_financial_docs(
            symbol, t.income_stmt, t.balance_sheet, t.cashflow, "FY"
        )
    except Exception as exc:
        logger.error("%s: annual financials fetch failed: %s", symbol, exc)
        return []
