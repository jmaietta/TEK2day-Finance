# Data Architecture - TEK2day Finance

This document describes how TEK2day Finance captures, stores, and presents financial data.

## 1. Data Capture

### Sources

| Source | Data Type | Method |
|--------|-----------|--------|
| Yahoo Finance via yfinance | EOD prices, financial statements, estimates, and ticker metadata | Scheduled ingestion scripts |
| Yahoo Finance live quote | Current price, price change, volume, and 52-week price range | Narrow live quote call at command runtime |
| SEC EDGAR API | SEC filings; reviewed financial fallback (off until enabled) | REST API |
| CEORater API | CEO analytics | REST API |

### Scheduled Capture

| Data Type | Frequency | Script |
|-----------|-----------|--------|
| Prices (EOD) | Daily, Mon-Fri | `pull_daily_prices.py` |
| Estimates | Weekly | `pull_weekly_estimates.py` |
| Financials | Weekly/quarterly as configured | `pull_quarterly_financials.py` |
| Ticker metadata | With the existing metadata pull path | `fetchers.fetch_ticker_info()` |

No new Firestore metadata fields are required for the current Terminal/Web hardening pass.

The separately requested [SEC financial fallback](docs/sec-financial-fallback.md)
is implemented inside the existing financial job, default off; the approved
BNY rollout is currently configured in observation mode (no SEC writes).
After a seven-day filing grace period it can fill reviewed missing financial
fields with atomic original/source audits. Its initial enrollment is BNY; CIK
alone does not enroll an issuer or join histories. See that document for exact
period selection, accounting mappings, rollback and coverage limitations.

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

Reviewed ticker continuity is a staged exception to the ticker-keyed layout.
See [issue #3 preflight](docs/issue-3-identity-preflight.md) for its current
deployment status. An internal issuer ID (with CIK as a registrant attribute)
and a separate security ID authorize a reviewed common-stock rename. A single
`security_routes/{event_id}` pointer publishes a fully reconciled tree under
`security_data/{security_id}/versions/{generation}`. Raw ticker trees and
original observations are retained. CIK/name/ticker matches alone never join
histories. Readers and guarded maintenance use the same route; partner requests
remain exact-symbol until a separate Kilby integration is agreed.

The [deployed XOM successor correction](docs/xom-succession-implementation.md)
handles two registrants and two securities with the same ticker through an
explicit dated relationship and a separate reporting-series identity. It keeps
the existing ticker storage tree; current metadata and identity records were
atomically published after approval September 12. Historical observations are not relabelled.
Reviewed filing lookup queries both CIKs and preserves original accession/CIK
associations; this does not enroll XOM in the SEC financial fallback.

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

The following guardrail was scoped to the earlier web-launch hardening work.
The separately authorized issue #3 identity migration is documented above and
adds reviewed issuer/security identity fields only through its approval gate.

Do not add or backfill new Firestore metadata fields as part of the current web
launch hardening work. The immediate goal is source consistency: Terminal and
Web should return the same data fields from the same source paths.
