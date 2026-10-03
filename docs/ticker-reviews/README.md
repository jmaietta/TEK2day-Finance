# Ticker reviews: the audit trail for removed and renamed tickers

The owner asked (2026-10-03) for a permanent record of every ticker TEK2day stops
covering: what happened to the company, the evidence, what was decided and when,
so a later question ("why did we drop AVB?") gets an accurate answer from the record.

One JSON file per review. Each file holds:

- `review`, `created_at`, `request`, `source_run`, `method`: what was reviewed, why and how.
- `summary`: tickers per status.
- `tickers`: one entry per ticker:
  - `symbol`, `company`, `cik`, `last_price_in_tek2day`
  - `status` and `proposed_action`
  - `new_ticker`: for a ticker change, the company's current ticker(s)
  - `sec`: current SEC name and tickers, names changed since 2025, last filing, and the
    filings that explain the status (Form 25 delisting, Form 15 deregistration,
    merger filings, 8-K items 2.01 / 3.01 / 3.03 / 5.01)
  - `yahoo`: what Yahoo returned, for tickers SEC could not settle
  - `held_by_kilby_customers`
  - `decision` (`pending`, `approved`, `kept`, `rejected`), `decided_at`, `applied_at`,
    `notes`: filled in when the owner decides and when TEK2day applies it. Never deleted.

Statuses: Acquired or merged · Delisted · Deregistered · Dormant · Warrant or unit
retired · Ticker changed · Foreign OTC, no price · Listed, Yahoo cannot price · No current
listing found · Check: Yahoo has a current price.

## Reviews

| File | What | Tickers |
|---|---|---|
| `held-deactivations-20261003.json` | The first universe sync's 1,132 deactivations (no price for 30+ days), held for review | 1,132 |

## Looking one up

```
python -c "import json; r=json.load(open('docs/ticker-reviews/held-deactivations-20261003.json', encoding='utf-8')); print(json.dumps(next(t for t in r['tickers'] if t['symbol']=='AVB'), indent=1))"
```
