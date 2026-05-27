# TEK2day Finance

Open-source stock data terminal — prices, fundamentals, estimates, and financials.

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
pip install -r requirements.txt
```

### Requirements

- Python 3.10+
- Google Cloud Firestore (for stored data commands)

## Usage

```bash
python terminal.py
```

### Slash Commands

Type `/` followed by any ticker symbol. For example, `/AAPL`:

| Command | Example | Description |
|---------|---------|-------------|
| `/TICKER` | `/AAPL` | Overview & valuation (live) |
| `/TICKER est` | `/AAPL est` | Consensus estimates |
| `/TICKER inc` | `/MSFT inc` | Income statement |
| `/TICKER bal` | `/GOOGL bal` | Balance sheet |
| `/TICKER cf` | `/AMZN cf` | Cash flow |
| `/TICKER div` | `/KO div` | Dividends (live) |
| `/TICKER short` | `/TSLA short` | Short interest (live) |
| `/TICKER target` | `/NVDA target` | Analyst targets (live) |
| `/TICKER chart` | `/META chart` | Price chart (1 year) |
| `/TICKER mgmt` | `/CSGP mgmt` | Management / CEO |
| `/TICKER filings` | `/JPM filings` | SEC filings |
| `/TICKER news` | `/AAPL news` | Recent news |
| `/compare` | `/compare AAPL MSFT GOOGL` | Comp table (up to 20 tickers) |
| `/help` | | Show command menu |
| `/exit` | | Quit |

### Examples

**Overview & valuation:**
```
tek2day> /AAPL
```
Returns live price, change, volume, market cap, shares outstanding, 52-week range, sector, beta, P/E, forward P/E, PEG, P/B, P/S, EV/EBITDA, and EV/Revenue.

**Compare tickers:**
```
tek2day> /compare CSGP SPGI VRSK FDS
```
Returns a side-by-side table with price, market cap, EV, revenue, EBITDA, net income, EPS, P/E, PEG, EV/EBITDA, EV/Revenue, EV/OpCF, EV/FCF, dividend yield, and beta for each ticker.

**Income statement:**
```
tek2day> /AAPL inc
```
Returns the last 4 quarters and last 4 fiscal years: revenue, gross profit, operating income, EBITDA, net income, EPS, and more.

## Data Pipeline

TEK2day Finance maintains a Firestore database of historical prices, estimates, and financial statements. See [DATA_ARCHITECTURE.md](DATA_ARCHITECTURE.md) for the full schema, capture schedules, and storage design.

### Admin Commands

```bash
python cli.py pull estimates            # Pull consensus estimates for all active tickers
python cli.py pull prices               # Pull daily OHLCV prices
python cli.py pull financials           # Pull quarterly financials
python cli.py ticker list               # List all active tickers
python cli.py ticker add NVDA           # Add a ticker to the universe
```

## Data Sources

- **Yahoo Finance** — prices, estimates, financials, dividends, short interest, analyst targets, news, company metadata
- **SEC EDGAR** — regulatory filings (10-K, 10-Q, 8-K, Form 4, etc.)
- **CEORater** — CEO performance scores, compensation, and total shareholder return (optional, requires API key)

## License

MIT
