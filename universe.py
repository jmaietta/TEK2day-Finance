"""
Ticker universe management.

Provides functions to build and maintain the list of tickers
the pipeline tracks. Supports loading US equities from yfinance
screen data and filtering out SPACs.
"""
import logging
import re

import yfinance as yf

logger = logging.getLogger("ydp.universe")

SPAC_PATTERNS = [
    r"\bacquisition\b",
    r"\bmerger\b",
    r"\bholdings corp\b.*\bclass a\b",
    r"\bblank check\b",
]
_spac_re = re.compile("|".join(SPAC_PATTERNS), re.IGNORECASE)

SPAC_SUFFIX_PATTERNS = [".U", ".WS", "-UN", "-WT"]


def is_likely_spac(symbol: str, name: str) -> bool:
    for suffix in SPAC_SUFFIX_PATTERNS:
        if symbol.endswith(suffix):
            return True
    if _spac_re.search(name):
        return True
    return False


def get_sp500_tickers() -> list[str]:
    """Pull S&P 500 constituents as a starting universe."""
    import io
    try:
        import pandas as pd
        url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(url)
        df = tables[0]
        symbols = df["Symbol"].str.replace(".", "-", regex=False).tolist()
        logger.info("loaded %d S&P 500 tickers", len(symbols))
        return sorted(symbols)
    except Exception as exc:
        logger.error("failed to load S&P 500 list: %s", exc)
        return []


def validate_ticker(symbol: str) -> dict | None:
    """Check if a ticker is valid and return basic info, or None."""
    try:
        t = yf.Ticker(symbol)
        info = t.info or {}
        name = info.get("shortName", "")
        if not name:
            return None
        if is_likely_spac(symbol, name):
            logger.info("filtered SPAC: %s (%s)", symbol, name)
            return None
        return {
            "symbol": symbol,
            "name": name,
            "sector": info.get("sector", ""),
            "industry": info.get("industry", ""),
            "exchange": info.get("exchange", ""),
            "market_cap": info.get("marketCap"),
            "active": True,
        }
    except Exception:
        return None
