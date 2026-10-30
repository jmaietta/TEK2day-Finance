# Share counts: the plan

For issue #16. Written 2026-10-29. **Status: proposed, nothing built.**
Replaces the share-count part of the old split plan (branch `split-plan`).
Price splits are already handled by the weekly 5-year price refresh.

## On one page

**The problem.** TEK2day's market cap is the price times the share count in the
latest quarterly statement. P/E, P/S and every EV ratio follow from it. The
quarterly EPS on the income statement is net income divided by that quarter's
share count. Measured 2026-10-29: of 6,734 companies, the market cap is more
than 1.5x away from Yahoo's own for 622 (24 of them over $10B), and 25–50% away
for 338 more.

**Five causes.** (1) companies with several share classes, (2) foreign
companies switching between ADR shares and local shares, (3) bad numbers in the
statements, (4) splits and reverse splits, (5) shares issued since the last
report, or a last report that is old.

**The fix.** Three sources are checked against each other for every company.
Where two agree, they win. Where none agree, the company is listed for review
rather than guessed. Corrections are kept beside the statements, never written
over them.

**Yahoo calls: zero extra.** Everything uses data TEK2day already receives,
plus SEC filings (free, about 10 requests a week).

**Kilby.** Market cap, P/E, P/S and EV ratios change for about 1,000
companies. No field changes unless the Kilby session agrees first.

**Your decisions** are in section 10.

---

## 1. How we tell which number is right

Every source has errors, so no single one is trusted. Three witnesses:

- **The statements** (from Yahoo; a few from SEC): the share count per quarter.
- **Yahoo's market cap**, already saved weekly with each company's details.
  Divided by that day's close, it gives Yahoo's share count on the same basis
  as the price, with every share class included.
- **SEC filings**: each quarter's diluted share count, from one free SEC file
  per quarter that covers every US filer (4,286 companies for June 2026).

Examples, 2026-10-29:
- **TTD:** statements 4.7M; SEC 470M; Yahoo 462M. The statement is wrong (100x).
- **FirstEnergy:** statements 2,316M; SEC filings about 578M; Yahoo 579M. The
  statement is wrong (4x). Yahoo's own quarterly EPS is wrong by the same 4x.
- **McDonald's:** statements 711M; SEC file 711.1 (filed in the wrong units);
  Yahoo 705M. Here SEC is the one that is wrong.

So: a number is used only when another witness confirms it.

## 2. Bad numbers in the statements (cause 3)

For each stored quarter, compare the statement's share count with SEC's for
the same quarter. If they differ by more than 10% and Yahoo's market cap sides
with SEC, use SEC's count for that quarter. If SEC sides with the statement,
keep the statement. Measured for June 2026: 3,354 of 3,480 checkable companies
agree within 10%. The 126 that don't are mostly statement errors (TTD) and a
few SEC unit errors (MCD, WAT, TEM). A second check inside each record: EPS
times shares should equal net income. TTD's own record fails it, which flags it.

Effect: TTD's quarterly EPS returns to about $0.14 (today it shows about $13.70).

## 3. Several share classes (cause 1)

IBKR, Blackstone, TKO, Carvana and similar companies report EPS for one class
only. IBKR's statement says 455M shares; SEC agrees (450M); Yahoo's market cap
counts all units, about 1,741M. EPS per share is right as stated. The market
cap is not.

Rule: when the statement and SEC agree but Yahoo's count is more than 25%
higher, it is a class difference. Market cap, P/S and EV ratios use Yahoo's
all-class count. EPS and P/E keep the statement's count. Recorded reason:
"several share classes".

## 4. ADR shares and local shares (cause 2)

KOF's statements switch between 210M New York shares and 1,681M local units
(8 units per share). HDB, CIB, Uxin and others do the same. The price is per
New York share.

Rule: for a foreign filer, if a quarter's count is a steady multiple of the
price-basis count (a whole number such as 2, 5, 8 or 10, or its inverse, the
same in every quarter it occurs), divide by it. The multiple comes from Yahoo's
market cap, or, for the 151 companies without one (KOF is one), from Yahoo's
year-ago EPS estimate, which is per New York share. A multiple that isn't
steady is listed for review, not applied.

## 5. Splits and reverse splits (cause 4)

**Prices** are already fixed by the weekly refresh.

**Share counts.** Yahoo restates recent quarters after a split, but not the
older ones it no longer returns.

What we will save: the split dates and ratios. Yahoo's price answer already
includes them, and the weekly 5-year answer holds every split in the last
5 years. Today they are thrown away. Saving them costs zero Yahoo calls.

Rule: for a quarter that ended before a split, check which basis its count is
on. If it matches the current count divided by the split ratio, multiply it by
the ratio. If it already matches the current count, leave it. Yahoo has
already restated those, so nothing is adjusted twice.

## 6. Shares issued since the last report, or an old report (cause 5)

Skyworks reported 151M shares for June; Yahoo now counts 229M after issuing
shares since. HDB's latest stored quarter is June 2025.

Rule: market cap uses the newest count that a second source confirms. If
Yahoo's count is more than 25% away from the last quarter's, and SEC's
cover-page count (dated after the quarter) agrees with Yahoo, use Yahoo's. If
the last quarter is more than 9 months old, use Yahoo's. Recorded reason:
"shares changed since the last report" or "last report is old".

## 7. Where corrections are kept

- **Not** in the statements. Financial statements stay write-once, as you
  ruled. BNY and AMZN keep their reviewed records.
- A new record per company, `share_counts/{TICKER}`, holds:
  - the share count used for market cap, its source and the reason;
  - any corrected quarter counts, each with the source;
  - the split list;
  - anything listed for review.
- It is rebuilt weekly at the end of the Thursday price refresh. That run
  already has the splits and the week's Yahoo market caps; SEC adds about
  10 requests.
- If the record is missing or unreadable, everything reads exactly as today.

## 8. What changes, and where it shows

- **Market cap, P/E, P/S, EV ratios:** website, public /stock/ pages,
  terminal, downloads, and Kilby's feed. They all read the same calculation.
- **Quarterly EPS on income statements:** corrected quarters.
- **Kilby:** values change, no fields change. Before switching it on, the
  Kilby session gets the list. A field saying why a count was replaced (for
  example `shares_basis`) is added only if Kilby wants it.

## 9. Order of work and how we will know it works

1. **Read-only report.** No changes. A spreadsheet with every company: its
   three counts, the cause, the count we would use, and a status label (fixed
   automatically / listed for review / no change).
2. **Build it, with tests.** Made-up cases for each cause, plus the real ones:
   TTD, FE, IBKR, KOF, HDB, SWKS, GIPR, MCD.
3. **Watch-only for one weekly run.** The record is written; nothing reads it
   yet. Compare against the report.
4. **Switch on** by a pull request you merge, after the Kilby session has
   seen the list.

## 10. Your decisions

1. Approve the approach (three witnesses; corrections kept beside the
   statements).
2. The 25% line for replacing a market-cap share count. Recommended: 25%.
3. When no two sources agree, the company keeps today's figure and goes on the
   review list. Recommended: yes, never guess.
4. Show a small note on the site when a count was replaced (for example
   "shares: all classes")? Recommended: yes.
