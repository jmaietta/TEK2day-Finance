# TEK2day foreign-currency companies: the plan

Written 2026-10-18 after your rulings in conversation. **Status: proposed,
nothing built.** Every figure below was read from TEK2day's own database, the
SEC, or the four Yahoo lookups you approved on 2026-10-18.

## 1. What it fixes

699 of TEK2day's 10,029 active companies file their statements in a currency
other than the US dollar. Their share price is in US dollars. TEK2day divides
one by the other without converting, so their valuation ratios are wrong.

TSM today: a P/E of 1.0x and a price-to-sales of 0.5x on its company page. The
right figures are about 27x and about 14x. The enterprise-value ratios are wrong
for the same reason. The website, the terminal, the comparison table, the
watchlist, the spreadsheet downloads and the feed Kilby reads all show these.

Since pull request #12 (merged 2026-10-18), the statements themselves are
labelled correctly ("TWD M"). This plan fixes the ratios.

## 2. The rule

- **What you see stays in the company's own currency.** Statements, revenue
  estimates and EPS estimates show in TWD for TSM, DKK for Novo, and so on.
- **Every valuation ratio is calculated in US dollars,** because the share price
  is in US dollars.
- **US companies do not change.** Their ratios are already right and keep being
  calculated exactly as today.

## 3. What Yahoo gives us (measured, not assumed)

- **Statements:** the company's own currency (TSM: Taiwan dollars).
- **Revenue estimates:** the company's own currency.
- **EPS estimates:** not consistent. TSM's come in US dollars, Novo's in Danish
  kroner. Yahoo does not say which (section 8).
- **Share price:** US dollars.
- **Yahoo's own P/E and forward P/E:** correct for all four companies checked
  (TSM 26.7x, Novo 9.5x, Itaú 12.1x, Toyota 8.6x). Yahoo's trailing EPS is in
  US dollars per New York share.
- **Yahoo's own price-to-sales:** wrong for all four. Yahoo makes the same
  mistake TEK2day makes (TSM 0.43x, Toyota 0.004x).
- **Yahoo's own enterprise-value ratios:** not reliable (wrong for TSM and
  Novo). Not used.
- **Yahoo's market value:** counted per New York share (TSM: 5.186 billion
  shares), so it avoids the share-count problem found on 2026-10-18.

## 4. Where each ratio will come from (foreign companies only)

- **P/E (TTM):** the live share price divided by Yahoo's trailing EPS in US
  dollars.
- **Forward P/E:** the live share price divided by Yahoo's forward EPS in US
  dollars. Note: Yahoo's forward EPS is not quite the figure TEK2day uses for US
  companies (next year's consensus). For TSM it is $23.39 against $22.30, so
  17.6x against 18.5x (question 2 in section 13).
- **Market value:** Yahoo's, moved with the live share price.
- **Price-to-sales, and enterprise value to revenue, EBITDA, operating cash flow
  and free cash flow:** calculated by TEK2day. Market value in US dollars;
  revenue, EBITDA, cash flows, debt and cash taken from TEK2day's stored
  statements, exactly as today, and converted to US dollars at the latest
  exchange rate.

## 5. The exchange rates

- From Yahoo, as you ruled: **one lookup per currency, once a night,** inside
  the nightly price update. 28 currencies today, so 28 lookups on top of the
  ~10,000 the update already makes (about 0.3% more).
- Done once per night, not once per part of the job: the update runs in several
  parts at once, and only the first part fetches the rates.
- The list of currencies comes from the companies themselves. If the weekly
  update finds a company filing in a 29th currency, that is one more lookup a
  night (question 3 in section 13).
- Each rate is saved with its date. If a night's lookup fails, the previous
  rate is used for up to 5 days; after that the ratios for that currency show
  blank instead of a stale answer.
- One rate per currency, the latest, is used for all conversions. That is the
  normal practice for a valuation ratio.

## 6. The live call (option 3, your ruling)

- When someone views a foreign company, on the website or through Kilby,
  TEK2day makes one extra Yahoo call, the company profile, next to the price
  call it already makes. US companies stay at one call.
- The profile is kept for 10 minutes, so repeated views do not repeat the call.
  TEK2day already keeps profiles this way today.
- If the profile call fails, that company's ratios show blank rather than a
  wrong figure, because the market value comes from the same call.

## 7. Companies whose currency is not yet known

- The weekly update records each company's filing currency from Monday
  2026-10-19 (pull request #12). It reaches every company within six days (one
  sixth each morning, Monday to Saturday). The 699 found in SEC filings can be
  filled in at once (step 4 of the currency fix, waiting on your yes).
- Until a company's currency is known, it is treated as today. After the first
  full week, every company Yahoo gives a currency for is covered.

## 8. EPS estimates in the company's own currency

- Revenue estimates are already shown in the company's own currency.
- EPS estimates are the hard part: Yahoo gives some in US dollars (TSM) and some
  in the company's currency (Novo), without saying which.
- They can be told apart by comparing them with Yahoo's forward EPS, which was
  in US dollars for all four companies checked. This is reliable where the currency is far from the
  dollar: Taiwan dollar (32 to the dollar), yen (158), yuan, real, krone and
  most others.
- It is NOT reliable for currencies close to the dollar: euro, pound, Canadian
  dollar, Swiss franc, Singapore dollar and Australian dollar. That is 344
  companies. A 10% to 40% difference could be the currency or could be an
  ordinary gap between two estimates (question 4 in section 13).
- Where the EPS estimates are in US dollars, they are converted to the company's
  currency for display at the latest rate.

## 9. Where it shows

All of these read one calculation, so they cannot disagree:

- The stock card on the website, and the terminal.
- The comparison table and its spreadsheet download.
- The feed Kilby reads (P/E, forward P/E, price-to-sales, and the
  enterprise-value ratios; field names unchanged).

Two places do not make live Yahoo calls and need their own handling:

- **The public company pages** (/stock/TSM). They use the stored closing price,
  never a live call. The weekly update would also keep Yahoo's trailing and
  forward EPS (in the profile it already pulls; no extra calls), so these pages
  can show the same P/E.
- **The watchlist.** It shows forward P/E today and three months and a year ago,
  from stored estimates. For foreign companies whose estimates are not in US
  dollars, the past ones need past exchange rates, which TEK2day does not have
  (question 5 in section 13).

## 10. Kilby

- Field names do not change. The numbers become correct.
- Kilby's report-writing code checks that market value is "live price x diluted
  shares". For foreign companies it will now be Yahoo's market value, so Kilby
  will refuse to cite their market value until a small Kilby change is made. It
  fails safe: nothing wrong is cited.
- The feed's note that says the currency is unverified will say instead which
  currency the statements are in and that the ratios are in US dollars.

## 11. Yahoo calls, exactly

- **28 a night** for exchange rates (approved 2026-10-18).
- **One per view of a foreign company,** at most once per 10 minutes per company
  (option 3, approved 2026-10-18).
- **Nothing else.** Building and testing makes no Yahoo calls. Any check that
  needs one is asked first, with the count.

## 12. How we will know it works

- Tests, without Yahoo, for TSM, Novo, Itaú and Toyota, using the figures from
  the four lookups: TSM's P/E 26.7x and price-to-sales about 14x.
- Tests that every US company's ratios come out exactly as before.
- After you merge: TSM, Novo, Itaú and Toyota checked on the live site against a
  hand calculation, and two US companies checked unchanged.
- Production only by a pull request you merge. There is still no test site, so
  merging deploys directly.

## 13. Questions for you

1. Approve this plan, section by section?
2. Forward P/E for foreign companies: Yahoo's forward EPS (simple, slightly
   different definition), or TEK2day's own next-year consensus converted (same
   definition as US companies, but it depends on question 4)?
3. A new currency appears: add its lookup automatically (one more call a night),
   or ask you first?
4. EPS estimates for the 344 companies in currencies close to the dollar: show
   them as Yahoo gives them, labelled "currency not confirmed", or something
   else?
5. Watchlist forward P/E history for foreign companies: leave the past values
   blank, or one-time fetch of two years of past exchange rates (28 Yahoo
   lookups, once)?
