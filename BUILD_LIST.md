# Yahoo Data Pipeline — Build List

## Slash Command Menu
- `/AAPL` — overview + valuation (live): price, change, volume, 52wk range, market cap, shares out, sector, beta, P/E, fwd P/E, PEG, P/B, P/S, EV/EBITDA, EV/Revenue
- `/AAPL est` — consensus estimates (EPS + revenue, current Q, next Q, current year, next year)
- `/AAPL inc` — income statement (quarterly + annual history)
- `/AAPL bal` — balance sheet, labeled "As of [date]" (point-in-time snapshot)
- `/AAPL cf` — cash flow, labeled "As reported"
- `/AAPL div` — dividends (live): yield, rate, payout ratio, ex-date, 5yr avg yield
- `/AAPL short` — short interest (live): shares short, short ratio, short % of float
- `/AAPL target` — analyst targets (live): mean, median, high, low, # analysts, recommendation
- `/AAPL chart` — candlestick chart with volume
- `/AAPL mgmt` — management (TBD: Yahoo officer data + CEORater integration when ready)
- `/AAPL filings` — recent SEC filings via SEC EDGAR API (same approach as Kilby)
- `/AAPL news` — recent news via yfinance ticker.news (same approach as Kilby)
- `/compare AAPL MSFT GOOGL` — full comp table: Rev, EBITDA, EPS (CY + FY), P/E (CY + FY), EV/Rev, EV/EBITDA, EV/OpCF, EV/FCF (CY + FY where available)

## Command Bar
- Persistent at top of terminal
- Context-sensitive: user types a ticker, bar shows available functions (est | inc | bal | cf | chart)

## Split Detection Job (Mondays)
- Market cap > $1B: every Monday
- Market cap < $1B: every other Monday
- Re-pulls adjusted prices and updates shares outstanding when split detected

## Scheduled Pulls
- Prices: end-of-day Mon–Fri (historical table only)
- Estimates: weekly
- IPO detection: daily
- Split detection: Mondays
- Financials: pulled as released

## Live Query Layer
- Any customer query involving price calls Yahoo real-time — never serve stored close
- All price-derived metrics (market cap, P/E, EV/EBITDA, etc.) calculated from live price
- Stored prices are for charts and historical lookups only

## Cloud Deployment
- Dockerize and deploy to Cloud Run
- Cloud Scheduler crons for each scheduled pull
- Split detection job on separate schedule

## Data Onboard
- [x] Load 9,908 tickers into Firestore metadata
- [ ] Pull S&P 500 — 5 years of estimates, prices, financials (in progress)
- [ ] Pull remaining tickers by market cap descending (skip < $100M)
- [ ] IPO detection: daily check for newly listed tickers, auto-onboard any >= $100M market cap
- [ ] Follow-on offerings: captured automatically via shares outstanding in daily estimate pull

## Field Alias Map
- Map user-friendly names to Yahoo's stored field names
- e.g., "ebitda" → "EBITDA", "revenue" → "Total Revenue", "fcf" → "Free Cash Flow"
- Required for /compare and for consistent display labels in inc, bal, cf views
