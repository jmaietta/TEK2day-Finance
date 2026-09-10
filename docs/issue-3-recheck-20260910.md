# September 10 read-only follow-up

Code is deployed at `e08d5492e3ffb37ca1490e55ce515f8864d2094a`, Cloud Run
revision `tek2day-api-00128-dvv`. The database migration is still unexecuted.
The user correctly identified that code deployment alone did not finish BK/BNY.
Live repair approval remains separate from the approved code deployment.

## BK/BNY repair readiness

The recursive read-only recapture completed at
`2026-09-10T14:52:48.609836+00:00`. All 1,303 inspected snapshots, including
the missing BNY root and every enumerated subcollection, match the reviewed
source exactly. Original capture-encoding tree SHA-256:
`af44febce93dc34f14d97cacaeebfeae9b7e25e63b771015d64b9ce8d8e7a068`.

`ticker_migration.verify_sources` passed against private `migration-plan-v1.json`.
The existing [manifest](issue-3-dry-run-manifest.json) remains applicable:
plan `17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd`,
2,605 staged data/audit writes, zero conflicts, then atomic publication.
The executor will lock guarded writes and recheck again before publication.

The ten most recent executions of each of the three financial/estimate/price
maintenance jobs were all completed. Latest completion times were September 9
21:38:39 UTC (prices), September 10 12:59:41 UTC (estimates), and September 9
20:05:33 UTC (financials). None of these observed executions remained running.
Recheck immediately before an approved repair; this receipt does not freeze
external state or authorize database changes.

## Separately reported NVDA July-quarter delay

Only NVDA metadata and its financial documents/descendants were captured, plus
one bounded Yahoo quarterly fetch. No partner API request or database write was
made. Private evidence:
`C:/tmp/tek2day-issue3-20260909/nvda-refresh-20260910.json`, canonical SHA-256
`94c81405299f0bf7ce81e4d6635800dcd345b6d665caa984c7a7b68bb117da66`.

Stored quarterly history ends at provider-labeled `2026-04-30`. There is no
stored document for provider-labeled `2026-07-31` (`2026-Q3` in this repository's
calendar-based keys). Yahoo's current July document has 42 finite income,
69 balance-sheet and 44 cash-flow fields, with the existing headline-anchor
stub check passing. This is evidence of missing stored coverage, not a claim
that every provider field is correct or that these labels are NVIDIA's actual
fiscal quarter names/dates. A separate old 2022-FY stub remains outside this
July-quarter check.

Stored financial `fetched_at` values are original May 26 ingestion times. The
write-once policy means those timestamps do not establish the date of the last
maintenance attempt. The eight-day NVDA-specific financial-job log query
returned no entries; normal successful fetches are not logged per ticker, so
absence of these entries does not prove NVDA was skipped.

A server-side count, exposing no other issuer metadata, found 6,381 active
symbols before NVDA at `2026-09-10T15:03:45.313585+00:00`. NVDA is active.
The actual job has no tranche-count/index overrides, so its six-way sorted
index assigns NVDA to tranche `6381 % 6 = 3`, Thursday. Sorted-index membership
can change with the active universe; this is current membership, not a claim
about historical assignment.

The matching enabled Cloud Scheduler job `quarterly-financial-pull-trigger`
is configured `0 12 * * 1-6`, timezone `America/New_York`. Its last attempt was
September 9 at `16:00:38.049561Z`; today's noon Eastern run had not started at
verification. This supports the user's tranche-timing explanation. Check the
July record after today's job processes NVDA before deciding that a code or
manual data repair is needed. No manual refresh was triggered.

Private tranche receipt:
`C:/tmp/tek2day-issue3-20260909/nvda-tranche-20260910.json`.
Private BK/BNY recapture:
`C:/tmp/tek2day-issue3-20260909/bk-bny-recheck-20260910.json`.
