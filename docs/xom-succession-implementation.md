# XOM successor identity — prepared September 12, 2026; not published

XOM's stored metadata pairs the successor name, ExxonMobil Holdings Corporation,
with predecessor CIK 34088. The June 2026 financial record is already present.
The SEC filing readers currently use a single CIK from the mutable ticker map,
which can omit predecessor reports. This correction preserves the existing XOM
tree and introduces an explicit, reviewed relationship between two registrants
and two common-stock securities.

## Evidence and bounded diagnosis

Primary company/SEC evidence is in [the event preflight](xom-registrant-succession-preflight.md)
and `registrant_succession.XOM_SUCCESSION`. The dated event is the July 1, 2026
holding-company redomiciliation merger: old CIK **0000034088**, common CUSIP
**30231G102**, to new CIK **0002115436**, common CUSIP **30233Q108**. One old
outstanding common share exchanged for one new share; XOM/NYSE remains the
listing. Exact effectiveness and first-trade instants are not asserted.
Old corporate debt obligations continue to have the predecessor's identity.

Both SEC submissions responses were captured September 12 in
`tests/fixtures/sec_xom_submissions_20260912.json`. This is bounded **public SEC
evidence**, with original response URLs, retrieval times and body digests; it is
not a Firestore export or fictional corporate-event fixture. Both responses list
the June 10-Q under accession **0000034088-26-000093**, accepted August 3 at
18:55:38Z. The shared accession is displayed once with both submission CIKs,
using the reviewed predecessor archive URL. Its Explanatory Note and Note 1
establish the unchanged consolidated reporting basis. Distinct or amended
filings are retained; this does not automatically select restated figures.

The verified database is `projects/yfinance-cli/databases/(default)`, Native,
`us-east1`. Maintenance reads used the existing named account
`jmaietta@ceorater.com`; no partner API request, credential/IAM change or paid
manual job was performed. [The diagnosis](xom-diagnosis-20260912.json) records
periods, null/zero counts, metadata and update-time evidence:

| Dataset | Captured coverage | What remains unverified |
| --- | --- | --- |
| Metadata | Successor name; old CIK; USD/NYQ; updated September 9 | Corrected CIK is not yet published |
| Financials | 13 documents, 2021 FY through June 2026; original periods preserved | Field presence does not verify each populated figure; historical source CIK/accession absent |
| Prices | 1,328 observations, May 27, 2021 through September 11, 2026 | Exact successor first trade/security attribution; no new price-history join |
| Estimates | 14 snapshots through September 9; none has explicit horizons | Provider revision after results and target periods |
| Current quote | No new quote request | Observation time and current-price freshness require separate post-rollout check |

The recursive capture inspected **1,356 XOM documents** and every collection
level (financials, prices, estimates; no deeper descendants present). Six new
identity destinations and the route were independently read and are absent.
Existing missing/null fields do not establish that populated values are wrong.

Private evidence, outside Git:

* `C:/tmp/tek2day-issue3-20260909/xom-before-20260912.json`
* `C:/tmp/tek2day-issue3-20260909/xom-identity-targets-20260912.json`
* `C:/tmp/tek2day-issue3-20260909/xom-succession-plan-v1.json`
* `C:/tmp/tek2day-issue3-20260909/xom-rollout-before-20260912.json`

## Implementation and compatibility

`registrant_succession.py` assigns separate internal issuer/security IDs plus
a consolidated reporting-series ID. CIK is a registrant attribute. The explicit
relationship defines the predecessor reporting boundary, successor boundary,
one-for-one common exchange and reviewed joint filing. No general CIK/name/ticker
matching enrolls another issuer. The BK/BNY rename validator remains unchanged
and rejects XOM's event.

`succession_filings.py` queries both reviewed registrants, validates source
identity and listing observations, deduplicates the reviewed shared accession,
and preserves each filing's source CIK/URL and submission associations. It
excludes post-event predecessor debt/entity filings from the successor common
view. Both web and terminal use this path. Recent submissions only; no archive
traversal. Cache keys include the immutable event digest; partial failures are
not cached as complete lists. A previously complete cache may be shown for up
to 24 hours with an explicit stale notice, preserving original capture times.

The offline financial selection adapter keeps every original observation,
including source CIK/accession, period start/end, frequency, currency and basis.
The joint report can have two filing registrants but one reporting issuer for
the consolidated group. Equal reviewed duplicates select one whole observation;
different values, complementary candidates, amendments, changed accessions or
ambiguous durations require review. There is no fieldwise cross-filing union.
Annual and quarterly reports ending on the same day remain separate. This
adapter is **not enrollment of XOM in the financial fallback**.

`succession_storage.py` uses the existing transaction mechanism for metadata,
financial, price and estimate maintenance. It checks the same route and all
immutable entity/relationship targets before active writes. Staging/paused
routes block writers; loops, aliases, missing targets, unexpected CIK/security
changes and relationships are rejected. The daily/weekly/quarterly jobs use
this dispatch and their existing failure reporting. XOM share-count changes
require observation review; the legacy populated-share overwrite exception is
disabled for this reviewed successor. Valid existing financial values, including
zero, survive ordinary gap fills. Conflicting incoming observations are retained
in audits. Quote observation times and estimate horizons are not rewritten.

Current issuer/security metadata describes **the current listing only**. It is
never assigned to all historical prices, estimates or financial observations.
History retains its original storage paths, original timestamps and unknown
provenance. Freshness and financial-field mapping remain separate concerns.

Kilby remains the product in `jmaietta/chatllm`; no chatllm code changed. Partner
requests, resolved tickers and payload symbols still agree: **XOM → XOM**;
**BNY → BNY**, and **BK returns 409**. No `security_resolution`, `renamed_to`,
redirect or new ancestor semantics are served. This extends the proposed contract
internally by separating reporting-series continuity from security identity;
it does not enable Kilby's proposed rename integration. Its empty reviewed-event
registry and fictional BK/BNY fixtures remain unusable as real event evidence.

No dependencies, infrastructure, IAM, schedulers or Dockerfiles change. Existing
Python 3.12 images copy root Python modules. The four new modules are also
included in the package's module list; pinned requirements are unchanged.

## Exact plan and rollback

[The dry-run manifest](xom-succession-dry-run.json) lists **all 1,362 inspected
source paths**, timestamps/digests, conflict disposition, exact writes and rollback.

* Plan SHA-256: `44bf39c167760bab8847b3cca5476ff2cb66a2040f0dbbac33c0c77df438add1`
* Generation: `880e6cab61b13713b18152f104ef47ed291697008106fa221291c409d89c9f39`
* One staging write to `security_routes/xom-common-succession-2026-07-01` pauses
  guarded maintenance while readers retain the existing XOM record.
* Nine writes publish atomically: two issuers, two securities, one relationship,
  one reporting series, the original-metadata audit, current XOM metadata, and
  the active route. One conflict: old CIK replaced in **current metadata only**
  using the reviewed event, with the original retained.
* **Zero financial-value, price-history or estimate writes. Zero deletions.**
  SEC financial fallback enrollment remains BNY-only; observe mode unchanged.

Pure reproduction (choose new outputs; no database connection):

```powershell
python -B scripts/prepare_succession_migration.py --capture C:/tmp/tek2day-issue3-20260909/xom-before-20260912.json --identity-capture C:/tmp/tek2day-issue3-20260909/xom-identity-targets-20260912.json --private-plan NEW_PRIVATE_PLAN.json --manifest NEW_PUBLIC_MANIFEST.json
```

After separate approval, deploy guarded code to API and all maintenance images,
verify running digests and drain old executions. Then invoke
`scripts/run_succession_migration.py stage` and `publish` with this private plan,
its exact approved digest, `--confirm-project yfinance-cli`,
`--writers-deployed-and-drained`, a new `--receipt`, and explicit
`TEK2DAY_ALLOW_IDENTITY_WRITES=1`. Never enable that flag during diagnosis.
The executor rechecks the entire recursive source tree under the maintenance
fence; changed inputs require a fresh capture and reviewed manifest.

Interruption before the publication transaction leaves original metadata visible;
reruns resume safely. Publication cannot expose some of the nine writes alone.
Published reruns do not reapply old data. Rollback uses the same executable with
action `rollback`: pause writers, verify the original nested tree and every
published metadata/identity/audit document, then atomically restore the original
metadata and mark the route rolled back. Immutable identities and the original
audit remain. Any newer observation causes refusal with the route left paused
for a separately reviewed reverse plan. There are no history deletions/copies.

Code rollback uses the image digests in the private rollout receipt. Roll back
the identity publication **before** restoring pre-guard images; do not remove
the guard from an active/paused succession route. The current baseline remains
`0095514c3a48e395ef4faab8284e7961cc5d1dea`, API `tek2day-api-00129-dc2`.

## Validation and pending acceptance

* 134 tests passed in four isolated offline suites: successor (45), BK/BNY
  identity (38), SEC fallback (44), share counts (7), with external access blocked.
* Existing isolated partner suites passed: symbols 126 checks, financials 34,
  summary 12, estimates 35.
* Pinned real Kilby guard passed 53 adapter assertions (22 rename, 15 SEC, 16
  XOM), with network blocked. These are synthetic producer fixtures, not live
  partner responses or evidence of actual corporate identities.

No XOM database writes, pushes or deployments have been performed for this
correction. After approval, reverify builds, native reads, first-party filings
and financial views, original nested-tree digests, quote observation, estimates,
cache transitions and an authorized maintenance rerun. Live partner acceptance
must use Kilby's existing authorized path. No local client may impersonate it.

Issue #3 remains OPEN. BK/BNY migration is already complete; its separate June
financial repair and live Kilby acceptance remain pending. Chatllm #221 remains
OPEN, #233 is not reopened. This XOM correction is not grounds to close either
issue or claim financial fallback coverage for XOM.
