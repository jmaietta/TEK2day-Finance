# Issue #3 closure record — OPEN, migration verified; coverage work remains

This record is deliberately incomplete until the acceptance evidence exists.
It must not be used as authorization to integrate or publish renamed securities.

September 12 financial follow-up: the [fresh BNY review and rollback runbook](bny-sec-repair-20260912.md)
and [exact manifest](bny-sec-repair-20260912.json) supersede the earlier absence-based
dry run. June has a partial Yahoo stub; 22 SEC gap fills are prepared, with zero
conflicts. Native preflight and 146 offline tests passed; the pinned Kilby guard
passed 53 assertions. Financial execution and automatic apply await approval.

| Item | Recorded state |
| --- | --- |
| GitHub source base | e39cdd98707974ba324a2d6ebb67a96353f48e07 |
| Serving API revision | tek2day-api-00131-jnd; 100% traffic, verified September 13 UTC (September 12 Eastern) |
| Serving API immutable image | sha256:674bc98a1e367f38db9ffb65b05055d05cbf08a67f6feb3cea7281aaf1f8fb80 |
| Implementation commit | 30d6b90646fade38609f3df21e4ab31af5066170; published |
| Deployed commit | 8e04f0237668fb9c209b471a28df120942b7ff6a |
| Build/deploy runs | API 34735420348; maintenance images 34735420363; both successful; [exact receipt](bny-sec-repair-deployment-20260913.json) |
| SEC fallback | Deployed, BNY-only observe mode; no SEC financial writes or manual job execution |
| Live repair/migration writes in this session | BK/BNY migration September 10; approved XOM identity publication September 12; retained original history and audit snapshots; no SEC financial backfill executed |
| Migration status | Published 2026-09-10T18:09:20.380321Z, plan 17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd |
| Live partner checks | Not performed; existing authorized Kilby path required |
| Offline validation | 439 verified tests across 21 isolated files; 22 pinned Kilby producer/consumer assertions |
| Runtime validation limit | Pinned image builds, Cloud Run startup and workflow smoke test passed; native migration/readers and no-op maintenance guards passed; live Kilby acceptance pending |
| Corporate event | BK→BNY, ordinary common stock, 2026-05-21; issuer CIK 0001390777; CUSIP 064058100; NYSE/XNYS; provider-observed USD |
| Known BNY financial gap (September 12 verification) | June now exists as a partial Yahoo stub. Fresh native/SEC/Yahoo review proposes 22 gap fills with zero conflicts; March is skipped. Financial execution and enabling apply await approval; 2021-FY remains a separate historical limitation |
| API rollout mode | Route active: BNY canonical, BK 409; no security_resolution or redirect payload |
| Issue #3 | OPEN; not ready for closure |
| Chatllm #221 | Remains OPEN |
| Chatllm #233 | Not reopened; no chatllm modification |
| XOM successor correction | Deployed and published September 12; current CIK 0002115436; historical data preserved. See [execution and exact manifest](xom-succession-implementation.md) |

The [preflight](issue-3-identity-preflight.md) contains the primary evidence,
bounded comparison, exact private-evidence locations, manifest, dependency
review, rollback procedure and acceptance checks. The
[rollback receipt](issue-3-rollout-rollback.json) preserves the prior image
digests; they were rechecked immediately before pushing. The
[deployment receipt](issue-3-deployment-20260910.json) records the new API and
maintenance image digests. The [executed migration receipt](issue-3-migration-20260910.json)
records 10 financial periods, 9 estimates, 1,282 prices and 1,303 retained audit
observations. Twelve first-party website checks passed. Migration preserved
existing history; it did not supply the absent June quarter. See the
[SEC fallback implementation](sec-financial-fallback.md) for the separate local
work, deployed in observation mode with financial writes disabled. Its
[deployment receipt](sec-fallback-deployment-20260910.json) records the current
images. No manual maintenance job was triggered.

Synthetic fixtures in `test_ticker_identity.py` and
`scripts/check_kilby_identity_contract.py` are permitted offline compatibility
fixtures. They are explicitly synthetic and cannot corroborate real corporate
identity or financial values. Do not copy private Firestore exports, credentials,
tokens, customer information or unreviewed live responses into chatllm tests.
Real frozen partner response fixtures remain pending collection through the
authorized Kilby path and review for sharing. Current Yahoo captures are private
diagnostic observations, not frozen partner API responses.

## Self-contained prompt for the later chatllm session

XOM follow-up to include in the returning prompt:
the reviewed implementation is commit
`1081ab88145cda12de444a790f13d64fe4046b83`, with
[review receipt](xom-local-review-20260912.json);
deployed in e9e28afd0536c5d8bc1ee552dee6449466fdbb11, revision
tek2day-api-00130-cxf, API build 34700569623 / jobs build 34700569634.
The published metadata/identity records carry Firestore update time
September 12 at 15:44:53.252968Z; the separate client completion reading is
15:44:46.393948Z. Preserve both clock readings and do not treat either as the
corporate-event effective time.
Verify the final XOM implementation commit/build and publication receipt from
`docs/xom-succession-implementation.md`. XOM is a July 1, 2026 successor-registrant
event, CIK 0000034088/common CUSIP 30231G102 to CIK 0002115436/common CUSIP
30233Q108, not a ticker rename. Its reviewed June 10-Q appears under both
registrants with accession 0000034088-26-000093. Retain original source identity
and avoid counting that quarter twice. Current compatibility remains XOM/XOM;
no proposed ancestry envelope is enabled. The public SEC submissions fixture
may be shared as evidence; synthetic adapter financial values are not real
company financials. Private Firestore exports and unreviewed live partner
responses are not permitted frozen fixtures. The XOM plan executed one staging
write and nine atomic publication writes, preserving the existing tree;
XOM is still not enrolled in SEC financial backfill. Native and first-party
checks passed; live Kilby acceptance remains separate and authorized-path only.

Use this only after TEK2day issue #3 has actually been resolved and closed;
replace the pending receipt entries with verified final values first.

> Work only in jmaietta/chatllm. GitHub is the source of truth. Read AGENTS.md and
> the published TEK2day issue #3 closure record before changing code. Keep chatllm
> #221 OPEN; do not reopen #233. Its September 9 continuity hardening merged in
> PR #238, main commit 129c730b4afcc3156223de2dff9c84d7a313f63d, implementation
> tip 7965a4972a74101743197c19b02fc1f725c260f3. The original proposed contract
> and its BK/BNY fixtures are not proof of the real event. The reviewed-event
> registry was empty and live clients enforced requested/resolved/payload symbol
> equality, rejecting renamed_to and security_resolution ancestry.
>
> Independently verify TEK2day's final commit/build, migration receipt and final
> API contract from docs/issue-3-closure-and-kilby-handoff.md in
> jmaietta/TEK2day-Finance. The preflight baseline was
> e39cdd98707974ba324a2d6ebb67a96353f48e07 / tek2day-api-00127-9z5; it is not the
> repaired build. Do not treat the dry-run manifest as an executed repair.
> The reviewed implementation was 30d6b90646fade38609f3df21e4ab31af5066170,
> included in deployed commit e08d5492e3ffb37ca1490e55ce515f8864d2094a on
> September 10, revision tek2day-api-00128-dvv. Builds and the workflow smoke
> test passed. Migration was published September 10 at 18:09:20.380321Z;
> native readers, first-party old/new reads and maintenance guards passed.
> Live Kilby acceptance and remaining coverage work were still pending.
> Verify the final published receipt; this code deployment alone is not closure.
> A subsequent SEC fallback deployment is commit
> 0095514c3a48e395ef4faab8284e7961cc5d1dea / tek2day-api-00129-dc2, with successful
> API workflow 34531601591 and maintenance workflow 34531601681. Its financial
> job is in observe mode, not apply mode. The June-quarter repair has not been
> applied. SEC financial envelopes passed the pinned Kilby guard offline;
> validate the final live responses through the authorized Kilby path.
> A fresh September 12 native review found a June Yahoo stub. The new plan is
> 1d2d3401552330e6c2ef12645e44482a8d4765ea3f9eb47e5869c208565eecd1:
> 22 gap fills, two atomic writes, no populated-value replacements. Check
> docs/bny-sec-repair-20260912.md and its manifest for approval/execution state;
> preparation and native preflight do not establish that the repair was applied.
> The June SEC accession is 0001390777-26-000086, filed July 31, accepted
> 2026-07-31T20:31:16Z, eligible after August 7 at 20:31:16Z. The verified source
> is https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf.
> Statements remain explicitly partial where unsupported bank mappings are absent.
> The guarded repair code was subsequently deployed as commit
> 8e04f0237668fb9c209b471a28df120942b7ff6a / tek2day-api-00131-jnd,
> with API workflow 34735420348 and maintenance workflow 34735420363 successful.
> Exact images are in docs/bny-sec-repair-deployment-20260913.json. Observation
> mode was reverified; this deployment did not apply the June financial repair.
>
> The verified event is the May 21, 2026 BK→BNY ticker change for The Bank of
> New York Mellon Corporation common stock, $0.01 par, CUSIP 064058100, NYSE
> XNYS, registrant CIK 0001390777. USD was observed in stored BK and current
> Yahoo BNY metadata, not stated by the company rename announcement. Review
> BNY's May 11 announcement at
> https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html
> and its effective-day company announcement at
> https://www.linkedin.com/posts/bnyglobal_announcement-bny-common-stock-is-trading-activity-7463211196343586816-nNu_.
> Corroborate the common-share CUSIP with OCC memo
> https://infomemo.theocc.com/infomemos?number=58945 and the SEC common-stock
> listing at https://www.sec.gov/Archives/edgar/data/1390777/000119312526314242/R1.htm.
> Exact transition and publication instants were not established. Do not invent
> midnight. CIK identifies the registrant; separate internal issuer/security IDs
> and the reviewed event authorize continuity. Preferred classes, ADRs, reuse,
> mergers, successors, spinoffs and delistings require separate review.
>
> The staged upstream design keeps canonical BNY responses exact-symbol and
> returns BK retirement as HTTP 409; it does not serve the proposed ancestry
> envelope. Confirm the FINAL served behavior before integrating. A future
> version needs explicit date precision, nullable non-ADR ratio, multiple source
> records, and separate migration completion, dataset coverage and freshness.
> Do not enable an alias merely to make a constructed evaluator pass.
>
> Use only the existing authorized Kilby path to collect live TEK2day partner
> responses. Do not impersonate Kilby from a local client, add grants or change
> IAM. Freeze only reviewed permitted response fixtures; never raw private
> Firestore exports or credentials. Check quotes, earnings, estimates, history
> and metadata independently, retaining quote observation time, reporting
> periods, estimate horizons and provenance through recovered/revised reports.
> The preflight discovered stale BK data, an absent BNY root, an incomplete
> 2021-FY, and an incomplete Yahoo 2026-Q2 despite the July 15 results release.
> Verify their final dispositions; a live quote or successful migration does
> not establish financial completeness. Continue the remaining Deep Research
> hardening under #221 with no paid run or deployment unless separately
> authorized in that session.
