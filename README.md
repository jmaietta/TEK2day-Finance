# TEK2day Finance

Open-source stock data terminal — market data and fundamentals.

## Features

- **Live market data** — real-time prices, valuation ratios, dividends, short interest, and analyst targets from Yahoo Finance
- **Stored financials** — quarterly and annual income statements, balance sheets, and cash flow statements
- **Estimate tracking** — consensus EPS and revenue estimates with historical accumulation
- **SEC filings** — recent 10-K, 10-Q, 8-K, and other filings from SEC EDGAR
- **CEO data** — scores, compensation, and tenure via CEORater API (optional)
- **Comp tables** — side-by-side comparison of up to 20 tickers
- **Price charts** — terminal-rendered charts with volume
- **Cross-platform** — Linux, Mac, Windows

## Installation

```bash
pip install tek2day-finance
```

Requires Python 3.10+.

## Quick Start

```bash
tek2day
```

This launches the interactive terminal. All commands start with `/`.

### Commands — No Setup Required

These work immediately after install. Data comes live from Yahoo Finance and SEC EDGAR.

| Command | Example | Description |
|---------|---------|-------------|
| `/TICKER` | `/AAPL` | Overview & valuation |
| `/TICKER div` | `/KO div` | Dividends |
| `/TICKER short` | `/TSLA short` | Short interest |
| `/TICKER target` | `/NVDA target` | Analyst price targets |
| `/TICKER mgmt` | `/CSGP mgmt` | Management / CEO |
| `/TICKER filings` | `/JPM filings` | SEC filings |
| `/TICKER news` | `/AAPL news` | Recent news |
| `/compare` | `/compare AAPL MSFT GOOGL` | Comp table (up to 20) |
| `/help` | | Show command menu |
| `/exit` | | Quit |

### Commands — Require Firestore

These pull from a Firestore database of stored financial data. Set the `FIRESTORE_PROJECT` environment variable to your GCP project ID and authenticate with `gcloud auth application-default login`.

| Command | Example | Description |
|---------|---------|-------------|
| `/TICKER est` | `/AAPL est` | Consensus EPS & revenue estimates |
| `/TICKER inc` | `/MSFT inc` | Income statement (quarterly + annual) |
| `/TICKER bal` | `/GOOGL bal` | Balance sheet |
| `/TICKER cf` | `/AMZN cf` | Cash flow statement |
| `/TICKER chart` | `/META chart` | Price chart (1 year) |

```bash
export FIRESTORE_PROJECT=your-gcp-project
tek2day
```

### Examples

**Overview & valuation:**
`/AAPL` — live price, change, volume, market cap, shares outstanding, 52-week range, sector, beta, P/E, forward P/E, PEG, P/B, P/S, EV/EBITDA, and EV/Revenue.

**Compare tickers:**
`/compare CSGP SPGI VRSK FDS` — side-by-side table with price, market cap, EV, revenue, EBITDA, net income, EPS, P/E, PEG, EV/EBITDA, EV/Revenue, EV/OpCF, EV/FCF, dividend yield, and beta.

**Income statement:**
`/AAPL inc` — last 4 quarters and last 4 fiscal years: revenue, gross profit, operating income, EBITDA, net income, EPS, and more.

## Data Sources

- **Yahoo Finance** — prices, estimates, financials, dividends, short interest, analyst targets, news, company metadata
- **SEC EDGAR** — regulatory filings (10-K, 10-Q, 8-K, Form 4, etc.)
- **CEORater** — CEO performance scores, compensation, and total shareholder return (optional, requires API key)

## License

MIT
