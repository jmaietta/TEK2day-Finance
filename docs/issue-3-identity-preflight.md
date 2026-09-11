# Issue #3: verified diagnosis and staged correction

Status: **code deployed and approved database migration completed September 10, 2026**.
Commit `e08d5492e3ffb37ca1490e55ce515f8864d2094a` serves as
`tek2day-api-00128-dvv`, with 100% traffic. Publication completed at
2026-09-10T18:09:20.380321Z. See the [executed receipt](issue-3-migration-20260910.json).
Native readers, original-history preservation, twelve first-party old/new reads
and a native no-op maintenance guard check passed. The latest copied financial
period is March 2026; SEC fallback work for the missing June quarter is separate.
Issue #3 remains OPEN. No local partner API call was made. Kilby live acceptance
remains pending through its authorized path.
The diagnosis below was captured September 9 Eastern time.

Subsequent serving deployment: commit `0095514c3a48e395ef4faab8284e7961cc5d1dea`,
revision `tek2day-api-00129-dc2`, 100% traffic. Its SEC fallback is in observation
mode and has made no financial repairs. See the
[current deployment receipt](sec-fallback-deployment-20260910.json).

Reviewed implementation commit: `30d6b90646fade38609f3df21e4ab31af5066170`
(included in the deployed commit; receipt documentation does not change runtime code).

## Source of truth and scope

GitHub main was verified at `e39cdd98707974ba324a2d6ebb67a96353f48e07`.
The clean tracked local main was fast-forwarded from `3bd945b` to that commit.
The pre-existing untracked `.claude/` directory was preserved. No applicable
AGENTS.md exists in this checkout, its checked ancestors, or the GitHub tree.
README, DATA_ARCHITECTURE, architecture.yaml, deployment workflows, Dockerfiles,
dependency pins and the prior quote-observation preflight were read first.
No new branch, project, grant, IAM setting, package or paid research was used.

The chatllm contract, policy and tests were read at immutable merge commit
`129c730b4afcc3156223de2dff9c84d7a313f63d` (implementation tip
`7965a4972a74101743197c19b02fc1f725c260f3`). No chatllm file or issue was changed.
Its fixture identities are fictional and its real reviewed-event registry is
empty. Neither was used as corporate-event evidence.

## Corporate event and identity

The issuer is **The Bank of New York Mellon Corporation**, SEC registrant CIK
**0001390777**. The scoped security is its **common stock, $0.01 par value**,
common-share CUSIP **064058100**, listed on **NYSE (XNYS; Yahoo NYQ)**.
BK changed to BNY effective **2026-05-21**, announced **2026-05-11**.

Primary evidence:

- [BNY announcement, May 11](https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html)
  identifies the common-stock rename, continued NYSE listing and unchanged legal
  name, capital structure, CUSIPs and securityholder rights. Saved HTML SHA-256:
  `9b722f4bc8fafe4f136c9d737e39215235e3a0bb3a0563bf97123eb635110fc6`.
- [BNY effective-day announcement](https://www.linkedin.com/posts/bnyglobal_announcement-bny-common-stock-is-trading-activity-7463211196343586816-nNu_)
  states that the common shares are trading as BNY effective May 21.
- [OCC information memo 58945, May 12](https://infomemo.theocc.com/infomemos?number=58945)
  corroborates the underlying common-share change and CUSIP. Browser PDF text
  was reviewed. Direct download returned 403; no raw-file digest was invented.
- [SEC 8-K cover, report date July 22](https://www.sec.gov/Archives/edgar/data/1390777/000119312526314242/R1.htm)
  identifies that CIK, BNY common stock and the NYSE listing separately from the
  preferred securities. The report date is not represented as publication time.

Trading currency is **USD**, observed in stored BK metadata and estimate currency
fields and independently corroborated by the current Yahoo BNY chart response:
NYSE/NYQ, EQUITY, USD. The rename announcement itself does not state the trading
currency. Do not substitute par value or reporting currency as currency evidence.
Yahoo's captured quote was 162.51 USD, observed at `2026-09-09T20:00:02Z`
(`regularMarketTime=1788984002`), retrieved at `2026-09-09T21:56:20.391299Z`.
That observation is historical evidence, not a claim about the price now.

Exact stock-transition/publication instants were not established. The registry
stores day precision and null instants; no midnight or exchange-open instant is
invented. Date-only transitions are not activated intraday on the effective date.

Internal issuer/security UUIDs are assigned separately in `security_identity.py`.
CIK anchors the SEC registrant; it does not identify all its share classes or
guarantee continuity through reorganizations. The explicit reviewed event and
record bindings authorize this join. No EQR/VMRK, SATS/ECHO, CTRA or HOLX action
is authorized or classified by this case. Merger, successor, delisting, spinoff,
reuse, conversion and ADR events are refused by the simple-rename rule.

## Actual database and bounded comparison

At diagnosis the serving revision was `tek2day-api-00127-9z5`, 100% traffic, image tagged with
GitHub commit e39cdd9. Its FIRESTORE_PROJECT is `yfinance-cli`; storage creates
the default client database. Read-only database describe verified
`projects/yfinance-cli/databases/(default)`, FIRESTORE_NATIVE, `us-east1`.
The separate macro project was not inspected or modified.

Using the existing `jmaietta@ceorater.com` maintenance account, the capture
enumerated **only tickers/BK and tickers/BNY and their recursive descendants**.
It used list_documents to include missing parents with descendants, with an
8,000-document/8-level cap that aborts rather than silently truncates.

| Dataset | BK capture | BNY capture |
| --- | --- | --- |
| Root | Active; CIK 1390777; NYQ/USD; populated name, sector, industry | **Does not exist** |
| Metadata updated | 2026-07-21T10:23:26.381118Z | None |
| Prices | 1,282 documents, 2021-05-26 through 2026-07-17 | None |
| Estimates | 9 snapshots, 2026-05-26 through 2026-07-21 | None |
| Financials | 10 documents: 2021–2025 FY and 2025 Q1–2026 Q1 | None |
| Latest financial period | 2026-03-31; fetched 2026-05-26 | None |
| Additional/deeper collections | None found | None found |
| Old/new populated conflicts | Zero, because BNY is absent | — |

Capture interval: `2026-09-09T19:46:50.860488Z`–`19:50:35.666180Z`.
It is an enumeration over that interval, not a database-wide transactional
snapshot. The executor must lock guarded maintenance and reverify the entire
source tree before staging/publication. Source changes invalidate the plan.

2021-FY has 138 non-finite fields and no populated headline anchors. Non-finite
field counts in the other financial documents are 4, 1, 5, 9 for 2022–2025 FY;
1 each for 2025 Q1–Q4; and 2 for 2026 Q1. Zero remains a populated value.
These gaps do not establish that other populated values are incorrect.

This corrects the issue's reported two-document premise: there is no stored BNY
document today. Code inspection explains how an unstored BNY can show a live
quote while having no Firestore metadata/history. A live partner request was
not used to prove that page behavior. The original onboard/rename sequence and
why Yahoo stopped returning BK later than the effective date remain hypotheses.

Verified causes: ticker-keyed storage; no shared identity resolver; ingestion
enumerates ticker keys; no SEC corporate-action detection in these maintenance
jobs; financial write-once behavior; swallowed write failures counted as success.
Quote caches are 30 seconds, Yahoo info 600 seconds, stored inputs 300 seconds;
the partner unknown-symbol cache can retain misses for an hour. Retrieval times
do not prove completeness or provider observation freshness.

## Separate current-provider diagnosis

A free, bounded BNY Yahoo capture obtained 11 metadata fields, six quarterly
periods, five annual periods, one estimate snapshot and 63 price rows
(2026-06-10–2026-09-09). No database writes occurred.

Estimates supply explicit horizons: 0q 2026-09-30, +1q 2026-12-31, 0y
2026-12-31, +1y 2027-12-31. The older stored snapshots lack horizons; these must
not be backdated or inferred from the new response. Provider estimate observation
time is unavailable; capture time alone does not prove incorporation of results.

The current 2026-Q2 Yahoo financial document has only 8 populated income fields,
2 populated balance-sheet fields and 0 cash-flow fields. It lacks total revenue
and net income although EPS is 2.45. It cannot certify financial completeness.
For all ten overlapping periods the bounded comparison found zero conflicting
populated observations. Some incoming fields are missing where storage is
populated, including 2025-Q1 EPS. Those stored values must survive.

[BNY's July 15 second-quarter release](https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/earnings/earnings-press-release-2q-2026.pdf)
establishes that results for 2026-06-30 are available. The local warning/freshness
floor uses that release independently of the later 10-Q. **A separate reviewed
primary-source financial backfill remains required before full closure.** This
rename manifest does not silently insert the provider stub or claim to repair it.

The [2026 second-quarter SEC 10-Q](https://www.sec.gov/Archives/edgar/data/1390777/000139077726000086/bk-20260630.htm)
is an identified primary backfill source. Its cash-flow statement covers the
**six months** ended June 30. A future Q2 repair must derive quarterly cash flows
from comparable year-to-date and Q1 observations, preserving both source
periods and any restatements; it must not label six-month cash flows as Q2.
No figures from this filing have been inserted into this rename plan.

## Candidate implementation and compatibility

The reviewed route document is `security_routes/bny-common-2026-05-21`.
The complete reconciled tree lives under
`security_data/{security_id}/versions/{generation}`. Neither key is a CIK or ticker.
The event registry is code-reviewed; matching names/tickers/CIKs cannot create it.

Staging pauses reviewed-security writes, copies every enumerated document and
all descendants, and saves each original snapshot under deterministic audit
paths. Populated destination observations win conflicts; source observations
fill only absent/null/non-finite values. Conflicts retain both values and require
review. Reporting-period identity conflicts abort. Original symbols and capture
times remain in the audit; the selected view uses the canonical symbol.

A single transaction publishes the ready route, canonical BNY directory metadata
and inactive BK directory metadata. No reader points at a partially copied tree.
All reviewed maintenance writes read that route in their transaction, reject
retired tickers/paused states and preserve prior observations. Financial updates
retain restatements as observations rather than treating every new response as
authoritative. Existing share-count repair selection remains, with prior values
retained. Direct financial/summary repair paths use the same route.

Active ticker enumeration canonicalizes and deduplicates reviewed identities.
Collection-group estimate reads exclude staged, retired and superseded roots.
First-party website/terminal lookups resolve BK to BNY only after activation.
Stored-input cache keys include generation/status; quote observation timestamps
and existing quote TTLs remain unchanged. SEO cached pages include the route key;
directory/sitemap and browser caches still require rollout invalidation/expiry.

The live partner API remains Kilby-only with unchanged authorization. No local
partner API call, impersonation, grant, or IAM change was made.

- BNY partner requests keep requested/resolved/data symbols BNY.
- BK partner requests after activation return the existing integrity-error shape
  with HTTP 409 and an explicit retirement detail. There is no redirected payload.
- No `renamed_to` tombstone or `security_resolution` ancestry envelope is served.
- New estimate snapshots may expose additive `target_period_ends` and nullable
  `provider_observed_at`; old rolling labels/capture fields are retained.
- Existing quality warnings independently flag earnings older than released
  results and estimates with missing horizons or unverified post-results timing.
  A good quote never clears those warnings.
- A newly captured estimate with future horizons still warns when incorporation
  of released results is unverified. That provenance warning does not by itself
  fail a successful maintenance fetch; missing horizons, stale coverage and
  failed writes do. Yahoo's unavailable observation time remains null.

This is a **staged exact-symbol compatibility mode**, not Kilby rename integration.
The real pinned Kilby guard accepts canonical producer envelopes and refuses
contradictory ancestry/retired requests in offline tests. Kilby's registry remains
empty; it must not automatically join BK history until its separate integration.

The proposed future contract should be versioned again to support date precision
(`effective_on`, nullable `effective_at`), multiple dated primary sources, issuer
and security IDs separately, `adr_ratio: null` for non-ADRs, and migration-complete
versus dataset-complete separately. Do not label a copied incomplete dataset
complete merely to pass the existing proposed evaluator. No future v2 contract
is currently served or agreed.

## Exact dry run, rollback and approval gate

[Exact path/digest manifest](issue-3-dry-run-manifest.json):

- Plan SHA-256 `17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd`.
- Generation `95d50ef03127c3d502bec0ff8e03cbdf3328f0c9b974958fdc97f9994c4aaa6f`.
- 1,302 selected data documents plus 1,303 source-observation documents = 2,605
  idempotent stage writes. No source subcollection is removed.
- Separate operations: one route-lock write; publication transaction updates
  exactly the route, tickers/BNY and tickers/BK. Publication time is assigned when
  the transaction actually executes, not prefilled in this dry run.

Private evidence stays outside Git in `C:/tmp/tek2day-issue3-20260909/`:
`bk-bny-before.json`, `migration-plan-v1.json`, `bny-provider-refresh.json`,
`bny-yahoo-quote.json`, saved primary HTML/PDF and the pinned Kilby policy.
The original capture digest is
`af44febce93dc34f14d97cacaeebfeae9b7e25e63b771015d64b9ce8d8e7a068`;
provider capture digest is
`d251f28e8ead08a1f582d7e80067201c7b45153e1efcd8d31a9664f87956a2e6`.
The migration uses its own canonical typed digest encoding (including NaN and
timestamps); its source digest intentionally differs from the capture encoding.

The executor requires the approved plan hash, project confirmation, an explicit
write-enable environment flag and confirmation that guarded code is deployed and
old executions drained. It defaults to no action; the preparation script has no
Firestore write code. These guards do not replace Jonathan's explicit approval.

Rollback first pauses reviewed maintenance. Before publication it only releases
the route and retains the partial stage. After publication it restores original
root documents and legacy routing only if staged data and published roots remain
unchanged. A missing original BNY root is restored to absence, but its original
descendants (if any in another reviewed plan) are never deleted. Audit/version
trees remain. If maintenance has added or changed observations, rollback fails
closed and leaves the route paused: a reviewed reverse plan is required, rather
than silently losing those new observations. Reruns and interrupted staging are
covered offline. A failed rollback pause can be examined without deleting data.

[Deployment rollback receipt](issue-3-rollout-rollback.json) records the actual
API revision/image digest and all three job image digests, including the daily
image shared by architecture-check. Recheck it immediately before an approved
push: root Python changes on main deploy the API and rebuild all job images.
Restore API traffic to the saved revision and restore the three latest tags to
their saved immutable digests if an approved deployment rollback is required.
Code/image rollback does not undo data. **Once identity data is active, do not
roll back to identity-unaware code while the route remains active.** Drain writes
and perform the applicable data rollback/reverse plan first.

The code rollout was approved and completed September 10. Database staging and
publication still require separate approval of this bounded migration and a
successful source recheck, with explicit incomplete financial-data status.
Do not close issue #3 after that alone. Image publication does not establish a
successful maintenance rerun; no manual maintenance execution was triggered.

## Validation and closure acceptance

Validation results: the full isolated run passed **437 tests across 21 files**.
Two additional maintenance cases were then added and the expanded
38-test identity file passed, bringing verified coverage to **439 tests**.
The actual pinned Kilby producer/consumer check passed **22 assertions** across
symbol resolution, summary, populated estimates and populated financials.
No external-access attempts were recorded by the strict offline runner. Python
syntax, pyproject parsing, runtime module packaging and `git diff --check` passed.
The saved private plan and public manifest reproduced exactly from the final
planner. Migration execution tests use an in-memory transaction model, not a
live Firestore database or emulator. Native transaction and deployed behavior
remain post-approval acceptance checks.

Run `python -B scripts/run_offline_regressions.py` for isolated, network-blocked
tests; this avoids legacy tests' cross-file global patch contamination. Run
`scripts/check_kilby_identity_contract.py --policy-file <saved-policy>` against
the pinned policy SHA-256
`4fe8485849398dc13972425540cd5e9e99b706bd960bc8b2ed5c6a44c557e137`.
This uses actual TEK2day producers and actual Kilby response_problem after JSON
serialization. It is not a live authorization/deployment test.

Requirements pins and Dockerfiles remain unchanged. Existing Docker COPY *.py
includes the new runtime modules; setuptools explicitly includes them too.
The local migration scripts run from the repository, not from a new cloud job.
No Docker image was built or deployed during diagnosis. The subsequently
approved workflow built the pinned images and deployed the API successfully;
startup and the existing live smoke test passed. This does not replace active
identity/native transaction validation after migration approval.

Local tests used Python 3.12.10 and the workstation's installed dependencies:

| Dependency | Tested locally | Existing deployment pin |
| --- | --- | --- |
| yfinance | 1.2.0 | 1.6.0 |
| google-cloud-firestore | 2.25.0 | 2.28.1 |
| pandas | 3.0.1 | 3.0.5 |
| numpy | 1.26.4 | 2.5.2 |

The exact pinned wheels were downloaded into memory and checked against primary
PyPI release metadata, without installing or changing any package. Source review
confirmed yfinance 1.6.0 retains the raw `earningsTrend` cache used for explicit
`endDate` capture. Firestore 2.28.1 provides the document transaction reads,
create-only writes, recursive document listing and transactional wrapper used
here. The fake transaction also rejects reads after queued writes, matching the
SDK constraint. This source check is **not execution in the pinned container**;
pinned-runtime/native transaction verification remains a rollout gate.

- [yfinance 1.6.0 metadata](https://pypi.org/pypi/yfinance/1.6.0/json), wheel
  SHA-256 `2ced6339a90e5721269a18d245757c7592163c897a7cd2b9a2dfd1d8fd30590e`.
- [Firestore 2.28.1 metadata](https://pypi.org/pypi/google-cloud-firestore/2.28.1/json), wheel
  SHA-256 `972ac92717c8c0c1ead18c28a3d6f739253cdf0940039bc172d5cbbbb399c6fd`.

Before closure, record the approved commit/builds, successful source recheck,
migration/rollback receipts, complete first-party BK/BNY reads, canonical partner
BNY responses through the existing authorized Kilby path, legacy BK refusal,
auth rejection for unauthorized callers, separate metadata/quote/history/estimate/
financial checks, cache transition/expiry and a bounded maintenance rerun.
Complete the primary-source financial backfill or agree explicitly on remaining
coverage limitations. Preserve original observation times and exact estimate
horizons. A healthy HTTP response or quote does not satisfy these criteria.

No issue was closed or commented on. Chatllm #221 remains OPEN; #233 is not
reopened. See the [closure record and return prompt](issue-3-closure-and-kilby-handoff.md).
