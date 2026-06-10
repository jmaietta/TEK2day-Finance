"""
yfinance data fetchers.

Each function pulls a specific data type for a single ticker and returns
a normalized dict ready for Firestore storage.
"""
import logging
import math
from datetime import date, datetime, timezone

import yfinance as yf

logger = logging.getLogger("ydp.fetchers")


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

    # gmtoffset converts the quote timestamp to the exchange's local date
    # without needing a tz database.
    offset = meta.get("gmtoffset") or 0
    bar_date = datetime.fromtimestamp(ts + offset, tz=timezone.utc).strftime("%Y-%m-%d")
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
        logger.error("%s: price fetch failed: %s", symbol, exc)
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
