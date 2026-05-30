# TEK2day Finance

Open-source stock data terminal — market data and fundamentals.

## Features

- **Live market data** — real-time prices, valuation ratios, company metadata, and news from Yahoo Finance
- **Stored financials** — quarterly and annual income statements, balance sheets, and cash flow statements
- **SEC filings** — recent 10-K, 10-Q, 8-K, and other filings from SEC EDGAR
- **CEO Analytics** — via CEORater
- **Comp tables** — side-by-side comparison of up to 6 tickers
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

## Screenshots

![TEK2day Finance terminal menu](https://raw.githubusercontent.com/jmaietta/TEK2day-Finance/main/docs/screenshots/terminal-menu.png)

![Ticker summary](https://raw.githubusercontent.com/jmaietta/TEK2day-Finance/main/docs/screenshots/ticker-summary.png)

![Comp table](https://raw.githubusercontent.com/jmaietta/TEK2day-Finance/main/docs/screenshots/comp-table.png)

### Commands

The terminal displays the following public slash-command menu:

| Command | Description |
|---------|-------------|
| `/TICKER` | Summary |
| `/TICKER inc` | Income statement |
| `/TICKER bal` | Balance sheet |
| `/TICKER cf` | Cash flow |
| `/TICKER mgmt` | Management / CEO |
| `/TICKER filings` | SEC filings |
| `/TICKER news` | Recent news |
| `/comp TICKER1 TICKER2 ...` | Comp table (up to 6) |
| `/help` | Show command menu |
| `/exit` | Quit |

The income statement, balance sheet, and cash flow commands require a configured Firestore database. Set the `FIRESTORE_PROJECT` environment variable to your GCP project ID and authenticate with `gcloud auth application-default login`.

```bash
export FIRESTORE_PROJECT=your-gcp-project
tek2day
```

### Examples

**Overview & valuation:**
`/TICKER` — live price, change, volume, market cap, shares outstanding, 52-week range, sector, beta, P/E, forward P/E, PEG, P/B, P/S, EV/EBITDA, and EV/Revenue.

**Compare tickers:**
`/comp TICKER1 TICKER2 ...` — side-by-side table with price, market cap, EV, revenue, EBITDA, net income, EPS, P/E, P/S, EV/EBITDA, EV/Revenue, EV/OpCF, EV/FCF, dividend yield, and beta.

**Income statement:**
`/TICKER inc` — last 4 quarters and last 4 fiscal years: revenue, gross profit, operating income, EBITDA, net income, EPS, and more.

## Data Sources

- **Yahoo Finance** — prices, financials, news, company metadata, and related market data
- **SEC EDGAR** — regulatory filings (10-K, 10-Q, 8-K, Form 4, etc.)
- **CEORater** — CEO Analytics via CEORater

## License

MIT
