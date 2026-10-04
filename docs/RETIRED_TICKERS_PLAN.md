# Retired tickers: plan

Owner, 2026-10-03: "these tickers are purged by Yahoo, what is the correct way to maintain these
purged tickers on TEK2day-Finance? What should the UI show for /ticker, /ticker inc, /ticker bs,
/ticker cf, /comp ticker1, ticker2 and /ticker news, not to mention these commands flow through to
Kilby." Then: "verify what the various commands show ... then write up the plan, then we will
execute the plan."

## The rule

Never delete a ticker. Mark its status, keep its history, and say plainly what happened.

## What we found (2026-10-03)

| Where | Ticker | What it shows | Right? |
|---|---|---|---|
| TEK2day site `/stock/AVB` | AVB, acquired Aug 17, 2026 | "$68.14 +2.82%, closing price as of 2026-08-24", a chart reading as a 63% crash, market cap $9.73B. No word of the acquisition. | No |
| Kilby `/AVB` | AVB | "$184.06 +$0.00", market cap $72.91B, P/E 71x, next-quarter estimates, source Yahoo. The SEC list shows Forms 15 with no explanation. | No |
| Kilby `/AVB IS` (not a command, so it went to the model) | AVB | The model researched it and said AVB was acquired and delisted. Correct, but by accident and at model cost. | Accident |
| TEK2day site `/stock/BK` | BK, renamed to BNY | Redirects to BNY with current prices. | Yes |
| TEK2day `/api/ticker/AVB` | AVB | `active: true`, no status. Its 1,260 stored prices and filed financials are intact. | Data yes, status no |

| Kilby `/AVB inc`, `/AVB bal`, `/AVB cf` | AVB | The statements as filed through the quarter ended June 30, 2026, source TEK2day. No word that AVB no longer files. | Data yes, status no |
| Kilby `/comp AVB EQR` | AVB | AVB at $184.06, market cap $72.91B, EV $82.02B, P/E 71.0x, forward P/E 36.6x, beside EQR as if both trade. Footer: "TEK2day Finance · as of 2026-10-04". | No |

Two different prices for the same dead stock: $184.06 is Yahoo's frozen last quote, which both
Kilby's quote card and TEK2day's comparison endpoint use; $68.14 is the last row of TEK2day's
stored price history (Aug 24, 2026), which the TEK2day site shows.
Step 1 done 2026-10-03 (owner's screenshots). News not checked: Kilby has no `/AVB news` command.

Deactivation (`storage.deactivate_ticker`) already keeps every stored price, financial and
estimate; it only stops the nightly pulls. `/partner/v1/symbols` already lists inactive tickers
with `deactivated_reason`. The rename path (ticker identity, BK → BNY) already works and is the
model for renamed tickers.

## Approved: what the screens show

Owner, 2026-10-03, after seeing these examples: "OK. I like this approach."

`/AVB` today:
```
AVALONBAY COMMUNITIES, INC. (AVB)
$184.06   +$0.00 (+0.00%)
Market Cap $72.91B · P/E 71.0x
Estimates: next quarter EPS $1.28 ...
```

`/AVB` after:
```
AVALONBAY COMMUNITIES, INC. (AVB)
ACQUIRED · Delisted Aug 17, 2026
Acquired by Vivmark Residential (take-private)

Last trade: $184.06 on Aug 14, 2026
No live price: AVB no longer trades.

Source: SEC Form 25, filed Aug 17, 2026 (link)
```
No market cap, P/E or estimates. (The last-trade figure and date shown here are illustrative;
the real ones come from the stored history.)

- `/AVB inc` after: the same statement, under "AvalonBay no longer files with the SEC. Last report:
  quarter ended June 30, 2026."
- `/ASGN` after: opens EFOR with "ASGN is now EFOR (renamed Apr 22, 2026)."
- `/comp AVB EQR` after: EQR as normal; AVB's row reads "Acquired Aug 17, 2026" with price
  figures blank.

## What each command should show for a retired ticker

| Command | Shows |
|---|---|
| `/AVB` | "AvalonBay Communities: acquired, delisted Aug 17, 2026" first. The last trade price and its date, labeled "last trade". No live quote, no day change, no market cap, no P/E, no estimates. Link to the SEC filing. |
| `/AVB inc`, `bal`, `cf` | The statements as filed, unchanged, under "No longer files with the SEC. Last report: 10-Q for the period ended June 30, 2026." |
| `/ASGN` (renamed) | The company under its new ticker, with "ASGN is now EFOR (renamed Apr 22, 2026)". |
| `/comp AVB EQR` | AVB keeps its row, marked "Delisted Aug 17, 2026"; price-based figures blank; fundamentals labeled with the last filing date. |
| `/AVB news` | News as now, the delisting or merger first. |
| Kilby chat | Every TEK2day answer carries the status, so the model never presents a last price as current. |
| TEK2day site `/stock/AVB` | Same as `/AVB`; the chart ends at the last trade with a marker for the event. |

## Steps

Each step is tested before the next starts; nothing ships without the owner's word.

1. **See the rest.** Run `/AVB inc`, `/AVB bal`, `/AVB cf`, `/comp AVB EQR` and AVB news in Kilby
   test, and the same on TEK2day's site. Add the results to the table above.
2. **Read the identity code** (`security_identity.py`, `ticker_migration.py`, `identity_storage.py`,
   `registrant_succession.py`) before changing anything. Status must sit on the security, keyed by
   CIK / security ID, so a reused ticker never inherits another company's history.

   Done 2026-10-03. What it is: two hand-reviewed registries, written as Python constants, one
   event each so far: the BK → BNY rename (`security_identity.BNY_EVENT`) and the XOM holding-company
   succession (`registrant_succession.XOM_SUCCESSION`). Each event carries CUSIP, share class,
   exchange, internal issuer and security IDs and primary sources, and moves its stored history
   through a staged, verified, resumable Firestore migration (`ticker_migration`,
   `succession_migration`). It is built for exact, one-at-a-time cases, and it refuses anything else
   (ADRs, conversions, chained or reused tickers).

   What that means here:
   - **Acquired, delisted, deregistered** need none of it: nothing moves; the ticker's record just
     gains its status fields. Writes go through `storage` / `identity_storage.guarded_write`, which
     already protects identity-managed tickers.
   - **Renames** do not need history moved either. Yahoo already carries five years of history under
     the new ticker (checked: EFOR, AHRT, VAI, AIFA each return 1,255 days from 2021-10-04), and the
     new tickers the sync added today get that history at the next price pull. So a rename is a
     pointer, old → new with its SEC evidence, not a migration. The reviewed-rename process stays
     for cases where the history cannot come from Yahoo.
3. **Store the status.** On each retired ticker: `status` (acquired, delisted, deregistered,
   renamed, no longer priced), `status_date`, `status_evidence` (form and SEC link), `successor`
   (acquirer or new ticker), `last_trade_price`, `last_trade_date`. Filled from the audit trail in
   `docs/ticker-reviews/held-deactivations-20261003.json` after the owner approves it.
4. **Serve the status.** `partner_api._resolve` is the one place every endpoint resolves a symbol:
   add the status there, so summary, financials, comparisons, metrics and estimates all carry it.
   A retired ticker's summary returns the last trade, labeled, and no live quote.
5. **Kilby shows it.** The quote card, statements, comp table and chat read the status from
   TEK2day. Neither Kilby nor TEK2day asks Yahoo for a live quote on a ticker TEK2day marks
   retired; Yahoo's frozen last quote is where $184.06 came from, on the card and in /comp.
6. **TEK2day site shows it.** `/stock/{symbol}` and the terminal show the status header.
7. **Apply the review.** With the owner's approval: deactivate the reviewed tickers, record the
   decision and date in the audit trail, and point the 167 renames at their new tickers (see step 2).
8. **Keep it current.** The daily universe sync records the status when it sees a Form 25 or 15,
   so future retirements need no manual review.

## Open

- Acquirer names (e.g. AVB → Vivmark Residential) are not in SEC's structured data; they come
  from the 8-K text or the web. Decide whether to store them.
- The two AVB prices disagree; find out which feed wrote $68.14.
