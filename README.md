# TEK2day Finance

TEK2day Finance is a market data, fundamentals and alternative data platform
for institutional and retail investors.

**Use it free in your browser — no install, no account:**
**[finance.tek2dayholdings.com](https://finance.tek2dayholdings.com)**

![TEK2day Finance — home](https://raw.githubusercontent.com/jmaietta/TEK2day-Finance/main/docs/screenshots/web-home.png)

![Ticker summary with interactive chart](https://raw.githubusercontent.com/jmaietta/TEK2day-Finance/main/docs/screenshots/web-ticker-summary.png)

![Comp table](https://raw.githubusercontent.com/jmaietta/TEK2day-Finance/main/docs/screenshots/web-comp-table.png)

## Features

- **Live quote data** - real-time price, price change, volume, and quote-sensitive valuation calculations
- **Interactive price charts** - selectable ranges; the chart's last point always matches the live quote
- **Stored fundamentals** - ticker metadata, estimates, quarterly and annual income statements, balance sheets, and cash flow statements from Firestore
- **SEC filings** - recent 10-K, 10-Q, 8-K, and other filings from SEC EDGAR
- **CEO Analytics** - via CEORater
- **Comp tables** - side-by-side comparison of up to 6 tickers
- **Terminal-matched Web GUI** - the web command surface runs the same terminal command functions
- **Cross-platform** - Linux, Mac, Windows

## Installation

```bash
pip install tek2day-finance
```

Requires Python 3.10+.

## Updating

TEK2day Finance checks PyPI when the terminal starts. If a newer release is
available, it prints the upgrade command:

```bash
python -m pip install --upgrade tek2day-finance
```

If you installed a local development checkout with `pip install -e .` and later
moved or renamed the checkout directory, reinstall it from the new path:

```bash
cd /path/to/TEK2day-Finance
python -m pip install -e .
```

## Quick Start

```bash
tek2day
```

This launches the interactive terminal. All commands start with `/`.

## Web GUI

Run the local web wrapper:

```bash
python app.py
```

The web command surface calls the same command functions as the terminal, so
Terminal and Web return the same data fields from the same sources.

## Commands

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

The income statement, balance sheet, cash flow, estimates, and stored metadata
commands require a configured Firestore database. Set the `FIRESTORE_PROJECT`
environment variable to your GCP project ID and authenticate with
`gcloud auth application-default login`.

```bash
export FIRESTORE_PROJECT=your-gcp-project
tek2day
```

## Examples

**Overview and valuation:**
`/TICKER` - live price, change, volume, market cap, shares outstanding, 52-week
range, sector, industry, company description, P/E, forward P/E, P/S,
EV/EBITDA, EV/Revenue, EPS/revenue estimates, and short interest. Stored
fundamentals and estimates come from Firestore; live price-sensitive values are
calculated from the current Yahoo quote. Company description comes from Yahoo
Finance. Short interest uses Firestore metadata when present and falls back to
Yahoo Finance when Firestore does not have short-interest fields.

**Compare tickers:**
`/comp TICKER1 TICKER2 ...` - side-by-side table with price, market cap, EV,
revenue, EBITDA, net income, EPS, P/E, P/S, EV/EBITDA, EV/Revenue, EV/OpCF,
EV/FCF, dividend yield, and beta. Stored fundamentals come from Firestore; live
price-sensitive values are calculated from the current Yahoo quote.

**Income statement:**
`/TICKER inc` - last 4 quarters and last 4 fiscal years: revenue, gross profit,
operating income, EBITDA, net income, EPS, and more.

## Data Sources

- **Firestore** - stored ticker metadata, estimates, prices, and financial statements
- **Yahoo Finance** - live quote fields, company description, short-interest fallback, and recent news
- **SEC EDGAR** - regulatory filings (10-K, 10-Q, 8-K, Form 4, etc.)
- **CEORater** - CEO Analytics via CEORater

## License

MIT
