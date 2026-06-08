# Data Architecture - TEK2day Finance

This document describes how TEK2day Finance captures, stores, and presents financial data.

## 1. Data Capture

### Sources

| Source | Data Type | Method |
|--------|-----------|--------|
| Yahoo Finance via yfinance | EOD prices, financial statements, estimates, and ticker metadata | Scheduled ingestion scripts |
| Yahoo Finance live quote | Current price, price change, volume, and 52-week price range | Narrow live quote call at command runtime |
| SEC EDGAR API | SEC filings | REST API |
| CEORater API | CEO analytics | REST API |

### Scheduled Capture

| Data Type | Frequency | Script |
|-----------|-----------|--------|
| Prices (EOD) | Daily, Mon-Fri | `pull_daily_prices.py` |
| Estimates | Weekly | `pull_weekly_estimates.py` |
| Financials | Weekly/quarterly as configured | `pull_quarterly_financials.py` |
| Ticker metadata | With the existing metadata pull path | `fetchers.fetch_ticker_info()` |

No new Firestore metadata fields are required for the current Terminal/Web hardening pass.

## 2. Data Storage

Firestore is the durable data store.

```text
tickers/{SYMBOL}/
    document metadata       -> symbol, name, sector, industry, exchange,
                               market_cap, shares_outstanding, float_shares,
                               currency, active flag, and any existing metadata
    estimates/{YYYY-MM-DD}  -> EPS and revenue estimate snapshots
    prices/{YYYY-MM-DD}     -> EOD OHLCV
    financials/{PERIOD}     -> income, balance_sheet, and cash_flow objects
```

Key rules:

- Prices use the date as document ID.
- Estimates use the pull date as document ID.
- Financials use the period as document ID.
- Financials are write-once; existing periods are not overwritten.
- Metadata writes use `set(..., merge=True)` through `storage.write_ticker_meta()`.

## 3. Presentation Rule

Terminal and Web must match. The Web GUI calls the same command functions in
`terminal.py`, captures the terminal output, and returns that output to the
browser.

### Stored Data

Firestore is used for:

- ticker metadata that already exists in the store, such as name, sector,
  industry, shares outstanding, and float shares
- estimates
- income statements
- balance sheets
- cash flow statements
- EOD price history

### Live Data

Yahoo Finance live calls are used for:

- current price
- price change
- price change percent
- volume
- 52-week price range
- company description
- short interest when Firestore metadata does not contain short-interest fields
- recent news

SEC EDGAR is used for filings. CEORater is used for management/CEO analytics.

### Calculated Data

TEK2day calculates price-sensitive valuation fields from stored fundamentals
plus the live Yahoo quote:

- market cap = live price times stored shares
- enterprise value = market cap plus stored debt minus stored cash
- P/E = live price divided by stored EPS
- P/S = market cap divided by stored revenue
- EV/Revenue = enterprise value divided by stored revenue
- EV/EBITDA = enterprise value divided by stored EBITDA
- EV/OpCF = enterprise value divided by stored operating cash flow
- EV/FCF = enterprise value divided by stored free cash flow

## 4. Public Commands

| Command | Source Rule |
|---------|-------------|
| `/TICKER` | Firestore fundamentals and estimates, live Yahoo quote, Yahoo company description, and short interest from Firestore when present with Yahoo fallback |
| `/TICKER inc` | Firestore financials |
| `/TICKER bal` | Firestore financials |
| `/TICKER cf` | Firestore financials |
| `/TICKER mgmt` | CEORater, with existing Yahoo officer fallback |
| `/TICKER filings` | SEC EDGAR |
| `/TICKER news` | Yahoo Finance news |
| `/comp TICKER1 TICKER2 ...` | Firestore fundamentals plus live Yahoo quote |
| `/help` | Terminal menu |
| `/exit` | Terminal exit |

## 5. Current Guardrail

Do not add or backfill new Firestore metadata fields as part of the current web
launch hardening work. The immediate goal is source consistency: Terminal and
Web should return the same data fields from the same source paths.
