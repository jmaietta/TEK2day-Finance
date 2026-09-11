# Issue #3 closure record — OPEN, migration verified; coverage work remains

This record is deliberately incomplete until the acceptance evidence exists.
It must not be used as authorization to integrate or publish renamed securities.

| Item | Recorded state |
| --- | --- |
| GitHub source base | e39cdd98707974ba324a2d6ebb67a96353f48e07 |
| Serving API revision | tek2day-api-00129-dc2; 100% traffic, verified September 10 |
| Serving API immutable image | sha256:66f004b8421c9fdf5858568583726d26a7b7ce88f7acab2fc2d8ac3cee045bf1 |
| Implementation commit | 30d6b90646fade38609f3df21e4ab31af5066170; published |
| Deployed commit | 0095514c3a48e395ef4faab8284e7961cc5d1dea |
| Build/deploy runs | API 34531601591; maintenance images 34531601681; both successful |
| SEC fallback | Deployed, BNY-only observe mode; no SEC financial writes or manual job execution |
| Live repair/migration writes in this session | Approved and executed September 10; retained original history and audit snapshots |
| Migration status | Published 2026-09-10T18:09:20.380321Z, plan 17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd |
| Live partner checks | Not performed; existing authorized Kilby path required |
| Offline validation | 439 verified tests across 21 isolated files; 22 pinned Kilby producer/consumer assertions |
| Runtime validation limit | Pinned image builds, Cloud Run startup and workflow smoke test passed; native migration/readers and no-op maintenance guards passed; live Kilby acceptance pending |
| Corporate event | BK→BNY, ordinary common stock, 2026-05-21; issuer CIK 0001390777; CUSIP 064058100; NYSE/XNYS; provider-observed USD |
| Known financial gap | Stored history ends 2026-Q1; Yahoo's 2026-Q2 response is incomplete; 2021-FY is incomplete |
| API rollout mode | Route active: BNY canonical, BK 409; no security_resolution or redirect payload |
| Issue #3 | OPEN; not ready for closure |
| Chatllm #221 | Remains OPEN |
| Chatllm #233 | Not reopened; no chatllm modification |

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
