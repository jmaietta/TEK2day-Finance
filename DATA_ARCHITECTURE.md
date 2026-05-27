# Data Architecture — TEK2day Finance

This document describes how TEK2day Finance captures, stores, and presents financial data.

---

## 1. Data Capture

### Sources

| Source | Data Type | Method |
|--------|-----------|--------|
| Yahoo Finance (yfinance) | Prices, estimates, financials, metadata, dividends, short interest, analyst targets, news | Python `yfinance` library |
| Yahoo Finance (live) | Real-time price, market cap, P/E, all price-derived metrics | Live `yfinance` call at query time |
| SEC EDGAR API | SEC filings (10-K, 10-Q, 8-K, Form 4, etc.) | REST API: `data.sec.gov/submissions/` |
| CEORater API | CEO name, founder status, CEORater score, alpha score, comp score, compensation, TSR | REST API: `api.ceorater.com/v1/ceo/{ticker}` |

### Capture Schedules

| Data Type | Frequency | Notes |
|-----------|-----------|-------|
| Prices (EOD) | Daily, Mon–Fri | Historical table only. Stored as `tickers/{SYM}/prices/{YYYY-MM-DD}` |
| Estimates | Weekly | Each pull creates a new dated snapshot. Accumulates as proprietary estimate history |
| Financials | As released | Quarterly and annual. Write-once — existing periods are never overwritten |
| Split detection | Mondays | Market cap > $1B: every Monday. Market cap < $1B: every other Monday. Re-pulls adjusted prices and updates shares outstanding when detected |
| IPO detection | Daily | Checks for newly listed tickers. Auto-onboards any ticker with market cap >= $100M |
| Metadata refresh | With estimate pull | Shares outstanding, market cap, sector, industry updated via `set(merge=True)` |

### Rate Limiting

- Yahoo Finance calls are spaced with a configurable delay (default 5–10 seconds between tickers)
- All Yahoo calls use retry with exponential backoff (3 attempts)
- Firestore writes use retry with exponential backoff (5 attempts, 60s × attempt)
- Ticker universe excludes companies with market cap < $100M to reduce unnecessary API load

### Capture Scripts

| Script | Purpose |
|--------|---------|
| `pull_sp500.py` | Initial onboard of S&P 500: estimates, 5-year prices, quarterly financials, annual financials, metadata |
| `pull_annual.py` | Pull annual financials for S&P 500 tickers |
| `pull_market_caps.py` | Lightweight info-only pass to classify remaining tickers by market cap (keep >= $100M, exclude < $100M) |
| `cli.py pull estimates` | Scheduled estimate pull for all active tickers |
| `cli.py pull prices` | Scheduled EOD price pull for all active tickers |
| `cli.py pull financials` | Scheduled quarterly financial pull for all active tickers |

---

## 2. Data Storage

### Database

Google Cloud Firestore (Blaze plan, pay-as-you-go). NoSQL document database with real-time capabilities.

### Schema

```
tickers/{SYMBOL}/
    (document)              → Metadata: name, sector, industry, exchange, market_cap,
                              shares_outstanding, float_shares, currency, active flag
    estimates/{YYYY-MM-DD}  → Daily estimate snapshot: EPS and revenue consensus
                              for current quarter, next quarter, current year, next year
    prices/{YYYY-MM-DD}     → Daily OHLCV: open, high, low, close, volume
    financials/{PERIOD}     → Financial statements per reporting period
                              Quarterly key format: "2026-Q2"
                              Annual key format: "2025-FY"
                              Each doc contains: income, balance_sheet, cash_flow sub-objects
```

### Key Design Principles

**Ticker-centric hierarchy.** Every piece of data lives under `tickers/{SYMBOL}/`. This makes per-ticker queries fast and allows Firestore collection group queries for cross-ticker analysis.

**Document ID = natural key.** Prices use the date as document ID. Financials use the period string. Estimates use the pull date. This prevents duplicates by design — writing the same key twice overwrites, it does not create a second document.

**Write-once for financials.** The `write_financials()` function checks `if ref.get().exists: return` before writing. Once Q2 2026 financials are stored, they are never overwritten. This preserves the original reported data.

**Append-only for estimates.** Each weekly estimate pull writes a new document with that day's date as the key. Over time this builds a historical record of how consensus estimates evolved — proprietary data that grows more valuable with each pull.

**Append-only for prices.** Each trading day adds one new price document per ticker. Historical prices are never modified (except after split detection, which re-pulls adjusted prices).

**Metadata uses merge writes.** `set(merge=True)` updates dynamic fields (market cap, shares outstanding) without deleting static fields (name, sector, CIK). This keeps metadata current without losing history.

### Storage Estimates

| Stage | Approximate Size |
|-------|-----------------|
| S&P 500 fully loaded (current) | ~150 MB |
| Full universe (~4,300 tickers) | ~1.3 GB |
| Annual growth | ~600 MB/year |
| 5-year projection | ~4.3 GB |

Firestore has no hard storage cap on the Blaze plan. At $0.18/GB/month, storage cost remains under $1/month for years.

---

## 3. Data Presentation

### Two Data Paths

TEK2day Finance uses two distinct data paths, and this distinction is critical:

**Stored data** — Historical records read from Firestore. Used for:
- Price charts and historical price tables
- Estimate history and consensus tracking over time
- Income statements, balance sheets, cash flow statements
- Any view labeled "as of" or "as reported"

**Live data** — Real-time queries to Yahoo Finance at the moment the user asks. Used for:
- Current stock price, change, and volume
- Market cap (live price × shares outstanding)
- All valuation ratios: P/E, forward P/E, PEG, P/B, P/S, EV/EBITDA, EV/Revenue
- Dividend yield, rate, payout ratio
- Short interest
- Analyst price targets and recommendations

**Why this matters:** A stock can move 30% intraday. Showing yesterday's close for market cap or P/E would be misleading. Every customer-facing metric that depends on price must use the live price, not the stored end-of-day close.

### Terminal Interface (`terminal.py`)

Interactive REPL with slash commands:

| Command | Data Source | Description |
|---------|-------------|-------------|
| `/AAPL` | Live Yahoo | Overview: price, change, volume, market cap, shares out, 52wk range, sector, beta. Valuation: P/E, fwd P/E, PEG, P/B, P/S, EV/EBITDA, EV/Revenue |
| `/AAPL est` | Firestore | Consensus EPS and revenue estimates: current quarter, next quarter, current year, next year. Shows # analysts, average, high, low, growth |
| `/AAPL inc` | Firestore | Income statement — quarterly (last 4 quarters) and annual (last 4 years). Key line items: revenue, COGS, gross profit, operating income, EBITDA, net income, EPS |
| `/AAPL bal` | Firestore | Balance sheet — quarterly and annual. Key line items: cash, current assets, total assets, debt, liabilities, equity |
| `/AAPL cf` | Firestore | Cash flow — quarterly and annual. Key line items: operating cash flow, capex, free cash flow, investing/financing activities |
| `/AAPL div` | Live Yahoo | Dividends: yield, rate, payout ratio, ex-dividend date, 5-year average yield |
| `/AAPL short` | Live Yahoo | Short interest: shares short, short ratio, short % of float |
| `/AAPL target` | Live Yahoo | Analyst targets: recommendation, mean/median/high/low target, # analysts, upside/downside |
| `/AAPL chart` | Firestore | 1-year price chart rendered in terminal (via plotext). Falls back to price table if plotext not installed |
| `/AAPL mgmt` | CEORater + Yahoo | CEO data (name, founder, scores, compensation, TSR) from CEORater API. Company officers from Yahoo |
| `/AAPL filings` | SEC EDGAR | 15 most recent SEC filings: date, form type, description, accession number |
| `/AAPL news` | yfinance | Recent news headlines with publisher, date, and link |
| `/compare AAPL MSFT GOOGL` | Live Yahoo | Side-by-side comp table (up to 20 tickers): price, market cap, EV, revenue, EBITDA, net income, EPS, P/E, PEG, EV/EBITDA, EV/Revenue, EV/OpCF, EV/FCF, dividend yield, beta |

### Web GUI (`app.py`)

FastAPI application serving a browser-based interface at `localhost:8050`:
- Ticker search
- TradingView-style candlestick charting (via lightweight-charts)
- Estimate and financial data tables
- Styled with TEK2day design language: DM Sans, black background, glass morphism, blue accent

### Metric Consistency

If the same metric (e.g., P/E, market cap, EV/EBITDA) appears in multiple commands — for example in both `/AAPL` and `/compare` — it uses the same formula and the same data source. All price-derived metrics are calculated from the live Yahoo price in every context where they appear.

---

## 4. Data Integrity Safeguards

| Safeguard | Implementation |
|-----------|---------------|
| No duplicate documents | Document ID = natural key (date, period, symbol). Writing the same key overwrites, never duplicates |
| No overwriting financials | `write_financials()` checks existence before writing. Returns immediately if document exists |
| No stale prices in queries | Live commands always call Yahoo real-time. Stored prices are for history/charts only |
| Retry on failure | Yahoo calls: 3 attempts with exponential backoff. Firestore writes: 5 attempts with 60s × attempt backoff |
| Rate limit protection | Configurable delay between Yahoo calls (5–10 seconds). Prevents permanent API blocks |
| Split adjustment | Weekly split detection re-pulls adjusted historical prices when a split is detected |
| Market cap filter | Tickers with market cap < $100M are excluded from the active universe to reduce API load and storage |
| Dot-ticker handling | Tickers like BF.B are mapped to BF-B for Yahoo API compatibility |

---

## 5. Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `FIRESTORE_PROJECT` | Yes | Google Cloud project ID for Firestore |
| `CEORATER_API_KEY` | No | API key for CEORater CEO data. If not set, `/mgmt` shows Yahoo officer data only |

These should be set as environment variables or managed via Google Cloud Secret Manager for production deployments. They must never be committed to the repository.
