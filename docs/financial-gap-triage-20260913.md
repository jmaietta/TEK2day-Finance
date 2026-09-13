# Financial coverage triage, September 13, 2026

Read-only diagnosis requested after the BNY three-field repair. No financial,
price, metadata, identity or scheduler writes were made in this follow-up.
No partner API calls, paid research or chatllm changes occurred.

## Confirmed current examples

| Symbol | Stored period key | Finding |
| --- | --- | --- |
| AMZN | 2026-Q2, end 2026-06-30 | Stub: four finite income fields, no balance-sheet or cash-flow fields. Fresh Yahoo has the same coverage gap. Revenue, net income, operating cash flow and assets are absent/nonfinite. |
| AMD | 2024-Q4, end 2024-12-31 | Historical quarterly stub; all five headline anchors unavailable. Finite field counts: income 1, balance sheet 5, cash flow 7. |
| GOOGL | 2024-Q4, end 2024-12-31 | Historical quarterly stub; all five headline anchors unavailable. Counts: 2, 4, 0. |
| JPM | 2024-Q4, end 2024-12-31 | Historical quarterly stub; all five headline anchors unavailable. Counts: 1, 4, 3. |
| NVDA | 2022-FY, provider end 2022-01-31 | Historical annual stub remains. Counts: income 4, balance sheet 6, cash flow 6. |
| NVDA | 2026-Q3, provider end 2026-07-31 | Earlier missing July record is now present, fetched September 10 at 18:29:57.655013Z. Counts: 42, 69, 44; passes the repository's headline-anchor check. Fresh Yahoo has matching field counts. |

Period keys and end dates above are repository/provider labels, not independently
verified SEC fiscal calendars. Counts and headline checks establish coverage,
not correctness of every number. No populated financial value was classified
as wrong merely because adjacent fields are missing. The NVDA stored timestamp
fits the previously observed Thursday tranche schedule; this is not a full
execution-log attribution or proof that every financial field is complete.

Quarter-four stubs need special review: a 10-K annual duration cannot be inserted
as a standalone quarter. Deriving a quarter may require same-basis annual minus
nine-month observations, appropriate restatement handling and separate per-share
treatment. Do not borrow GOOG observations for GOOGL solely because the registrant
is shared. JPM needs bank definitions; ADR/foreign issuers in the broader reports
may require different forms, reporting currencies and share-class treatment.

## Existing reports are an initial candidate list

The bounded query read the latest eight `repair_proposals` documents, ordered by
`generated_at`, with report dates August 29 through September 12. This was not a
scan of the ticker universe. The latest September 12 report contains 854 period
entries across 636 symbols: 759 `NO_SOURCE`, 93 `RETRY_LATER`, and two `REVIEW`
entries already marked populated. It is the report from the failed Saturday
execution previously investigated for BNY.

Selecting only the latest observation per symbol/period across those eight
reports leaves 5,505 unpopulated entries labeled `NO_SOURCE` or `RETRY_LATER`,
across 4,124 ticker symbols. These are **report flags, not a verified current
repair count**, and symbols are not unique issuers. The reports even include
BNY's June gap, subsequently repaired. The selected report dates do not cover
every day of the latest week; absence of a report does not prove a job was absent.

`NO_SOURCE` means this Yahoo-based check supplied no additional fields for an
older period. It does not mean the company never filed with the SEC or that no
primary-source backfill exists. `RETRY_LATER` likewise is not proof of an actual
earnings/filing date. Those labels must be reconciled with current records and
dated primary filings before deciding which repairs are appropriate.

The SEC fallback currently enrolls **BNY only**, using reviewed profile v2.
Reusable translation code exists, but other issuers are not automatically
eligible solely because a CIK is present. A review must establish reporting
entity, security/class where relevant, fiscal dates, units and field definitions.

## Work order and boundaries

1. Finish the BNY price-basis review and prepare its exact price repair manifest,
   retained observations and rollback. The [42-date inventory](bny-price-gap-inventory-20260913.json)
   remains unchanged; no price repair was executed here.
2. Prepare AMZN's recent-quarter financial comparison and reviewed SEC mapping.
   A second Yahoo retry alone cannot currently supply the missing fields.
3. Turn the existing review flags into a dated coverage inventory, separating
   recent missing reports, historic partial periods, unavailable provider fields,
   incompatible definitions and corporate/security identity events. Revalidate
   before proposing bounded repairs. Do not automatically apply the report list.

This record authorizes no new live repair or broad issuer enrollment. Existing
authorization permits investigation and local preparation; concrete database
repairs require approval. TEK2day issue #3 and chatllm #221 remain open, and
chatllm #233 remains closed.

## Private evidence

All files are under `C:/tmp/tek2day-issue3-20260909/`, outside Git:

| File | Canonical SHA-256 |
| --- | --- |
| financial-maintenance-reports-20260913.json | 029587e62c2330565ea13fd1fd44f22490eabd4a2dd5f04dd3cb44c484d54895 |
| financial-gap-sample-20260913.json | 5ee72f2b0a224e8d0626a113234e4def2e48bcba96c2542c9784833da53e081d |
| nvda-refresh-20260913.json | 5729be7b861d37a2b1ddc07a4b5e60d5724bb2ef4e64419a3187f2d1ac4c071a |

The first file preserves the eight reports and their individual paths, update
times and digests. The second preserves the four selected financial documents
and AMZN's current Yahoo quarterly response. The third captures NVDA's bounded
financial tree and current Yahoo quarterly observations. Raw private exports
and unreviewed response fixtures are not approved for sharing with chatllm.
