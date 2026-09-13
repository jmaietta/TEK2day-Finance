# Proposed BNY June repair: three additional fields

**Approved September 13; execution receipt pending.** The user approved the
BNY-only three-field repair and activation of its reviewed mapping. The local
binding is v2; deployment and execution are recorded separately below when
verified. The original preparation manifest remains immutable.

The [exact public manifest](bny-additional-repair-20260913.json) identifies plan
`05fbe370165a5a2118b157c4647df5c949b5a78dbbddc3c290f3e41d58591f04`,
Firestore yfinance-cli/(default), the canonical security version, both existing
documents and their timestamps/digests, both proposed write paths/digests, and
the conditional rollback. The private plan freezes the full original statement,
earlier repair audit, source facts and exact write templates.

| June 30, 2026 field | Proposed value, $M | Selection rule |
| --- | ---: | --- |
| Pretax Income | 2,267 | Consolidated profit 1,792 plus income tax 475; do not use parent-only net income |
| Common Stock Equity | 39,910 | Parent stockholders' equity 44,664 less preferred carrying value 4,754 |
| Cash Dividends Paid | (453) | Negative of six-month cash payments 887 less first-quarter payments 434; not declared dividends |

These definitions agree with the [BNY Q2 10-Q](https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf),
printed pages 46, 48–50, and the [BNY Q1 10-Q](https://www.bny.com/assets/corporate/documents/pdf/investor-relations/form-10-q-1q26-final.pdf),
printed pages 44, 46–47. Q2 labels include “Income before income taxes” and “Cash
dividends paid”; page 50 explicitly reports common shareholders' equity. Q1
values are 2,016, 39,452 and (434), respectively. Pretax and equity formulas use
same-accession, same-period observations; the cash bridge retains both filings.

Q2 accession is `0001390777-26-000086`, filed July 31, accepted at
20:31:16Z, eligible after August 7 at 20:31:16Z. Q1 bridge accession is
`0001390777-26-000060`. The SEC source URLs, captured timestamps and byte digests
are retained in the manifest/provenance. Company PDF hashes are
`7e004391586962b2d0ff73c43d540f88b45bc80052b4099b4dd65a99fe2dce2d`
(Q2) and `ba5624c10f0e70b7fea1b71a3fb32ed040fcf671eaa2156213cfa518cb45f63c`
(Q1). PDF page numbers in this review are printed page labels.

The three destination fields are absent. All 26 previously mapped candidate
fields match the selected values: zero conflicts. Two atomic writes would
update this one statement and retain the original under a new source audit.
The September 13 22-field repair and its audit remain intact. Every nested
collection was enumerated: two existing documents, one child collection.
Quotes, estimates, prices, metadata and other financial periods are excluded.

## Validation and activation

Eight additional offline tests cover real public facts, missing and zero terms,
wrong dates, conflicting source observations, preserved populated values,
inactive enrollment, interrupted commit, rerun and rollback retaining the earlier
repair. Final validation passed 173 tests across seven isolated suites and
53 pinned Kilby producer/consumer assertions, with external access blocked.

Native read-only preflight passed with three fills and zero conflicts. All
nonempty RPC commits were blocked. Fresh SEC facts still produced the same
semantic candidate; fresh Yahoo still lacked the three fields. The preflight
simulated v2 only inside the local process; it did not change live enrollment.
Its private receipt digest is
`5aba9a65337aff8f564474e98122c0f6eb28e5bf93298f153e18498fc03fda4e`.

After approval, change only BNY's binding profile to v2, preserving all identity,
period and share-class constraints. Re-run tests and native preflight before
execution. Coordinate publication and repair with the existing scheduled job;
do not trigger the full-universe job. If any source, destination, route or child
observation changes, stop for a refreshed plan rather than overwrite it.

Execute with the existing `scripts/run_sec_repair.py apply` explicit write gate
and approved plan digest. Replays use the retained audit. Afterward verify the
three fields, unchanged other datasets, cache expiry, BNY/BK compatibility and
the next ordinary scheduled run. Partner checks use Kilby's authorized path.

Rollback uses the existing plan-bound pause/rollback/resume commands. Pause the
reviewed route, require the exact applied document and no newer descendants,
restore the pre-additional-repair statement (including the earlier 22 fields),
retain both audit histories and the rollback marker, then verify and resume.
Revert BNY's profile to v1 if activation is also being reversed. Do not roll back
the earlier June repair or the original ticker migration.

Private artifacts are under `C:/tmp/tek2day-issue3-20260909/`:
`bny-additional-native-20260913.json`, `bny-additional-plan-20260913.json`,
`bny-additional-preflight-20260913.json`. Raw private exports are not shareable
fixtures. `tests/fixtures/sec_bny_2026_additional.json` contains only the two
additional public SEC concepts and can be shared with the existing public facts
fixture. Synthetic storage tests do not verify real corporate identity.

## Remaining gaps

Cash equivalents, debt, intangibles, capitalization, working capital and capital
stock repurchases still need definition review. Q1 Yahoo debt includes other
borrowings; its cash and intangibles also differ from the identically sounding
SEC concepts. Matching one numeric decomposition does not establish a reliable
field definition. Preferred redemptions complicate repurchase aggregation.
These fields remain missing, with the existing mapped-subset warning.

March revenue is 5,336 in Yahoo versus 5,409 in the filing. The difference equals
the filing's 73 distribution/servicing expense, suggesting a netting convention;
Yahoo's method has not been independently established. Preserve both source
observations and the selected history. Incomplete 2021-FY needs its own dated
historical filing review.

The price comparison found 42 absent dates and 16 revised overlaps in a bounded
63-session capture. All 21 overlapping stored closes equal fresh **unadjusted**
closes; all 21 newly captured adjusted closes agree with Yahoo's adjusted series.
Fifteen older closes reflect the subsequent July dividend adjustment. All 16
revised overlaps also have volume differences. BNY's [dividend history](https://www.bny.com/corporate/global/en/investor-relations/dividend-history.html)
confirms July 27 as ex-date for $0.63. This establishes the recent price-basis
problem, not a coherent basis for the entire five-year stored series. A separate
bounded historical-basis review and exact price manifest are still required.
No price writes are included or authorized here.

Private price evidence digests: raw-basis capture
`3bed9d00f668ce246e6e7996a5e105e0c55bbf09591fee50ec8e407647cbf42c`;
comparison `ab9d76280726258cc3298cea085e81176aa6042e36e53ee94e17863379b65385`.
