# Stock splits: the plan

For jmaietta/chatllm#155. Written 2026-10-19. **Status: proposed, nothing built.**

## On one page

**The problem.** TEK2day saves each day's price once, already adjusted for
splits as of that day. The nightly job only rewrites the last 5 days. When a
company splits, every older price stays on the old basis. The result is a false
cliff on the chart, and wrong returns and watchlist changes across the split.

**The fix.** When a company splits, divide its older stored prices by the split
ratio, keep a copy of the originals, and check the result.

**Yahoo calls: zero extra.** Yahoo's nightly price answer already includes any
split from the last 5 days, and the weekly profile includes the last split date
and ratio. TEK2day receives both today and ignores them.

**Kilby: not touched.** Kilby's code does not change. The prices it reads from
TEK2day become correct.

**Your decisions:** approve the plan; approve the read-only scan (step 1); and
choose between apply automatically or ask you each time (step 4).

---

## 1. First, find out if it has already happened (read-only)

Scan TEK2day's stored prices for overnight jumps that match a split ratio, such
as a halving, a third, a tenth, or ×10 for a reverse split. This needs no Yahoo
calls and writes nothing. The result is a list for you to see.

## 2. Catch every new split (no extra Yahoo calls)

- **Nightly:** read the split column already in the price answer.
- **Weekly safety net:** save Yahoo's last split date and ratio from the profile
  the weekly pull already fetches. This catches a split missed when the nightly
  job was down for more than 5 days.

## 3. Fix the stored prices

- Divide the company's prices before the split date by the ratio, and multiply
  volume by it.
- Save the originals first, one copy per split, so it can be undone.
- Check afterwards: the jump across the split date must now look like an
  ordinary day. If it doesn't, change nothing and flag it for review.
- Each company records the splits already applied, so nothing is adjusted twice.

## 4. Apply automatically, or ask first

- **Watch-only for the first 2 weeks:** log what it would do, change nothing.
- After that, your choice:
  - **(a)** apply automatically, with a daily email listing what changed; or
  - **(b)** list each split and wait for your yes.

## 5. Splits already missed

Whatever step 1 finds is repaired with the same tool, using the ratio Yahoo's
profile gives, which the weekly pull will have saved within 6 days. A company
with two splits since TEK2day began is flagged for you, not fixed automatically.

## 6. Not in this plan

- **Dividends.** Yahoo's adjusted prices also shift slightly at every dividend.
  The stored history drifts by roughly the dividend yield each year. Fixing that
  would mean re-reading every dividend payer, which is thousands of Yahoo calls.
  It's left as a separate decision.
- **Statements before Yahoo's window.** Share counts follow a split only for the
  periods Yahoo still returns. Older stored periods keep the old count. That's
  noted for a later fix.

## 7. How we will know it works

- Tests with a made-up 2-for-1 and a 1-for-10 reverse split: the chart is flat
  across the date, returns are right, the originals are saved, it can be undone,
  and a second run changes nothing.
- The 2 weeks of watch-only logs, checked against the splits that actually
  happened.
- Production only by a pull request you merge.

## 8. Where it shows

Every place that reads stored prices: the website chart, the public /stock/
pages, the terminal chart, the watchlist, the downloads, and the price history
Kilby reads.
