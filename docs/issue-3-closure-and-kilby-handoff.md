# Issue #3 closure record — OPEN, migration verified; coverage work remains

This record is deliberately incomplete until the acceptance evidence exists.
It must not be used as authorization to integrate or publish renamed securities.

The [September 13 cross-symbol triage](financial-gap-triage-20260913.md) confirms
AMZN's June stub, older AMD/GOOGL/JPM quarter-four stubs and NVDA's older annual
stub. NVDA's formerly missing July record is now present. These findings are
separate from BNY's approved repairs; other issuers are not enrolled in SEC
backfill, and the broad report flags are not an approved migration list.

The [September 13 scheduled-refresh follow-up](bny-refresh-followup-20260913.md)
records the failed Saturday observe-mode execution, the next Monday noon
Eastern schedule, and two subsequent maintenance corrections. The user supplied
Kilby income, balance-sheet and cash-flow screenshots showing the repaired June
data; the balance sheet also matches TEK2day. These authorized-path display
checks pass. Raw partner response and authorization checks remain separate.

The maintenance correction originated at `db81352fe071eafaa2555ce61ef5023b297dd1e7`.
The final serving build activates the approved BNY v2 mapping at
`c96d36cc0d59cad53405b7272ce73ac152934eec`, revision `tek2day-api-00134-xfn`,
API build 34741645100 and job builds 34741645108, all successful;
[execution, immutable image and first-party check receipt](bny-additional-execution-20260913.json).
The [three-field follow-up repair](bny-additional-repair-20260913.md) was approved
and applied at Firestore commit time 2026-09-13T05:59:14.439028Z: pretax income
2,267 million USD, common equity 39,910 million USD and cash dividends paid
-453 million USD. Two writes, zero conflicts; the earlier repair and its audit
were preserved. BNY's active binding is v2. New Kilby display checks for these
three fields remain to be supplied through its authorized path; the earlier
screenshots only verify the preceding repair.

September 13 financial follow-up: the approved BNY June repair was applied and
verified. The [execution receipt](bny-sec-repair-execution-20260913.json) records
22 gap fills, zero conflicts and two atomic writes. The [review and rollback
runbook](bny-sec-repair-20260912.md) and [exact manifest](bny-sec-repair-20260912.json)
remain the operation's evidence. BNY-only automatic SEC fallback is enabled.
Twelve first-party checks and native no-op maintenance reruns passed. The
pre-execution validation included 146 offline tests and 53 pinned Kilby assertions.

| Item | Recorded state |
| --- | --- |
| GitHub source base | e39cdd98707974ba324a2d6ebb67a96353f48e07 |
| Serving API revision | tek2day-api-00134-xfn; 100% traffic, verified September 13 UTC |
| Serving API immutable image | sha256:743d1571e722eb934dca8ac585437f425ee835ce0606005fd399959c5fc6d92c |
| Implementation commit | 30d6b90646fade38609f3df21e4ab31af5066170; published |
| Deployed commit | c96d36cc0d59cad53405b7272ce73ac152934eec |
| Build/deploy runs | API 34741645100; maintenance images 34741645108; both successful; [exact receipt](bny-additional-execution-20260913.json) |
| SEC fallback | BNY-only apply mode, financial job generation 4 Ready; no manual Cloud Run job execution |
| Live repair/migration writes in this session | BK/BNY migration September 10; XOM identity publication September 12; BNY June SEC repair September 13 at 03:47:44.132463Z, 22 fields/two writes; additional repair at 05:59:14.439028Z, three fields/two writes; originals and other history retained |
| Migration status | Published 2026-09-10T18:09:20.380321Z, plan 17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd |
| Live partner checks | User-supplied Kilby screenshots confirm all three statement displays; raw response and authorization checks remain pending through that authorized path |
| Latest offline validation | 173 tests and 53 pinned Kilby assertions against the active v2 binding; native post-repair reruns made zero writes |
| Runtime validation limit | Pinned image builds, Cloud Run startup and workflow smoke test passed; native migration/readers and no-op maintenance guards passed; live Kilby acceptance pending |
| Corporate event | BK→BNY, ordinary common stock, 2026-05-21; issuer CIK 0001390777; CUSIP 064058100; NYSE/XNYS; provider-observed USD |
| BNY financial coverage | June's 22 plus three approved gaps filled; SEC subset explicitly partial. March retained; its revenue basis difference and incomplete 2021-FY remain separate limitations |
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
work, now enabled for BNY after the approved June repair. The
[original repair deployment receipt](bny-sec-repair-deployment-20260913.json) records the
repair images; the [execution receipt](bny-sec-repair-execution-20260913.json)
records actual application and verification. No manual Cloud Run job was triggered.

Remaining acceptance: raw partner verification through Kilby; the next ordinary
financial-job execution in apply mode; existing price gaps (no stored bars
between July 2 and July 10, or July 17 and September 3, 2026); incomplete 2021-FY;
March revenue reconciliation. The financial repair preserved all 1,288 prices
and ten estimate observations. Latest estimates were fetched September 12,
with explicit September/December 2026 and December 2027 horizons; the provider
observation instant is absent, and nine older observations lack horizons.
An independent Yahoo quote was $162.66 USD observed September 11 at 20:00:02Z,
matching the first-party chart. These separate timestamps do not prove that
estimates reflect every earnings release or that financial mappings are complete.

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
> Independently verify the final TEK2day state in
> docs/issue-3-closure-and-kilby-handoff.md. The serving commit is
> c96d36cc0d59cad53405b7272ce73ac152934eec, revision tek2day-api-00134-xfn,
> with API build 34741645100 and maintenance build 34741645108 successful.
> Exact image digests are in docs/bny-additional-execution-20260913.json.
> This includes db81352's scheduled-refresh correction and the approved active
> BNY v2 mapping. Plan
> 05fbe370165a5a2118b157c4647df5c949b5a78dbbddc3c290f3e41d58591f04
> was applied in two atomic writes at 2026-09-13T05:59:14.439028Z, adding June
> pretax income 2,267 million USD, common equity 39,910 million USD and cash
> dividends paid -453 million USD. Earlier selected values and audits remain
> intact. Review docs/bny-additional-repair-20260913.md and its execution receipt.
> Native post-repair reruns made zero writes; 173 offline tests and 53 pinned
> Kilby assertions passed against v2. All 12 first-party checks passed after the
> existing financial cache TTL. The next scheduled financial job is Monday,
> September 14, noon America/New_York; no apply-mode scheduled run has yet
> occurred. User screenshots confirm Kilby receives all three repaired statement
> displays for the earlier 22-field repair. Kilby checks for the three new
> fields, and raw partner/authorization checks, remain pending.
> The BK/BNY continuity migration was published September 10 at
> 18:09:20.380321Z; its plan is
> 17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd.
> The original rename implementation is 30d6b90646fade38609f3df21e4ab31af5066170.
>
> The separate BNY June SEC financial repair was approved and applied at
> Firestore commit time 2026-09-13T03:47:44.132463Z under plan
> 1d2d3401552330e6c2ef12645e44482a8d4765ea3f9eb47e5869c208565eecd1.
> It filled 22 missing fields in two atomic writes and preserved populated
> values, originals, other financial periods, metadata, prices and estimates.
> Native verification, bounded no-op maintenance reruns and 12 first-party
> checks passed. The existing financial job now uses SEC_FALLBACK_MODE=apply
> with BNY as its only enrollment. No full Cloud Run job was manually triggered.
> Review docs/bny-sec-repair-execution-20260913.json for exact results and limits.
> June's SEC accession is 0001390777-26-000086, filed July 31, accepted
> 2026-07-31T20:31:16Z, eligible after August 7 at 20:31:16Z. The source is
> https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf.
> Unsupported mappings remain unavailable and statements explicitly partial.
> Do not equate the seven-day 10-Q/10-K policy with earnings-release freshness.
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
> XOM is a different event: a July 1, 2026 holding-company merger with a
> successor registrant, CIK 0000034088/common CUSIP 30231G102 to CIK
> 0002115436/common CUSIP 30233Q108, one-for-one common shares, ticker XOM
> and NYSE unchanged. Review the primary SEC source
> https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm
> and notice https://www.sec.gov/Archives/edgar/data/34088/000087666126000593/ruleprovisionnotice.htm.
> Implementation 1081ab88145cda12de444a790f13d64fe4046b83 is included in the
> current serving build. The approved identity publication used one staging
> and nine atomic publication writes; its server time is September 12 at
> 15:44:53.252968Z. Historical financials, prices and estimates were preserved.
> Joint June report 0000034088-26-000093 occurs under both registrants; retain
> the source CIK and count the report once. XOM is not enrolled in SEC backfill.
> See docs/xom-succession-implementation.md and docs/xom-execution-20260912.json.
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
> Remaining acceptance at the September 13 receipt: raw Kilby checks, the
> next ordinary apply-mode execution, existing price-history gaps, incomplete
> 2021-FY and March revenue reconciliation. June repair is verified complete
> for the reviewed subset. Verify final dispositions before integrating; a live
> quote or successful migration does not establish financial completeness. Continue the remaining Deep Research
> hardening under #221 with no paid run or deployment unless separately
> authorized in that session.

## September 13: scalable cohort implementation

The [cohort workflow](sec-cohort-backfill.md) generalizes ordinary-security
enrollment and SEC backfill without adding ticker-specific application code.
The production catalog remains empty; only the previously approved BNY binding
is active. The [AMZN dry run](amzn-cohort-review-20260913.json) proposes ten gap
fills through two financial/audit writes and a separate control publication;
it is not executed or enrolled. Its immutable cohort digest is
`b08ebdc2b886c1577df06bab0797578a5ff0e454d1b1ac80bfecff43bd925b99`.

For the returning Kilby session, also read `docs/sec-cohort-backfill.md`.
Ordinary enrollment keeps requested/resolved/payload ticker semantics intact;
it does not enable the proposed security-resolution contract. The new permitted
fixture `tests/fixtures/sec_amzn_2026_q2.json` contains bounded public SEC facts,
not a live partner response or private financial export. Existing acceptance
items above remain open, including BNY's 42 price-history gaps and ordinary
scheduled-run verification. TEK2day #3 and chatllm #221 remain open; #233 stays
closed. This session does not modify chatllm.
