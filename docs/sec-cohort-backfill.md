# SEC fallback for reviewed cohorts

The fallback now supports ordinary existing securities through a packaged JSON
catalog. Adding another company with the same reviewed accounting profile does
not require a company-specific Python implementation or a ticker migration.
The catalog is initially empty; BNY's already approved binding remains active.
No additional production security or historical repair was activated by this
framework change.

Deployed build: `ed341e71c6177f16cd37ddc99c349164c1341079`, API revision
`tek2day-api-00136-5h2`, with all three maintenance images verified and old
writers drained. Implementation commit:
`650c4fe1c21acb59963353c6b70ac9604ff302d3`. Both GitHub workflows passed.
The [deployment record](sec-cohort-deployment-20260913.json) contains exact
image digests, verification receipts and outstanding acceptance items.
All twelve first-party BK/BNY checks passed at 07:29:45 UTC on September 13.
AMZN's read-only native enrollment preflight passed; no AMZN control or repair
was written. No live partner API was called.

## Shared pipeline

1. Capture a named cohort's metadata and complete financial document trees,
   including nested observations and missing ancestors. Capture submissions
   and Company Facts from the stored CIK. This is evidence discovery, not an
   identity match authorizing a join.
2. Review issuer and security UUIDs, CIK, share class, listing venue, currency,
   dated primary evidence, accounting profile digest and reporting interval.
   Store these as catalog data. Multiple classes may share one registrant but
   retain distinct security IDs and storage trees. An issuer ID cannot acquire
   another CIK through this ordinary-enrollment workflow.
3. Translate eligible 10-Q/10-K observations using exact accession, unit,
   duration and consolidated concepts. Yahoo retains first chance. The grace
   period is 168 hours after SEC acceptance; date-only evidence uses the
   existing conservative deadline. Processing happens on the next scheduled
   run, not precisely at the deadline. A retrieved quote proves nothing about
   financial coverage or estimate horizons.
4. Prepare exact per-period writes, source/native digests, conflicts and
   rollback instructions. Preserve populated values, including zero, and all
   descendants. Any conflicting candidate is held; no mixed-basis statement
   is automatically published.
5. Publish reviewed controls before deploying their catalog entries. Each
   control targets only `tickers/<same symbol>` and never redirects readers.
   Each financial repair remains a two-document transaction. Replaying a
   completed repair is a no-op; interruption cannot expose half a statement.

The implementation is in `sec_catalog`, `sec_enrollment.py`, `sec_cohort.py`,
`sec_batch.py` and the shared `sec_fallback.py` / `sec_maintenance.py` engine.
Controls are `sec_bindings/<security UUID>`. Metadata, financial, price and
estimate maintenance use the same control and identity guards. Missing,
changed or paused controls block writes instead of recreating a parallel tree.
An incoming financial record's supplied identity is checked before gap merging
can discard that metadata. Selected history and provider revisions remain
separate observations.

## Operator workflow

All raw captures and immutable private plans belong outside Git. The following
commands prepare work; they do not activate a security or repair the database:

```powershell
python -B scripts/capture_sec_cohort.py --symbols AMZN --output C:/tmp/cohort-evidence.json
# Add reviewed `bindings` to a private copy of the evidence bundle.
python -B scripts/prepare_sec_cohort.py --bundle C:/tmp/cohort-reviewed.json --private-plan C:/tmp/cohort-plan.json --manifest docs/cohort-review.json
python -B scripts/run_sec_enrollment.py check --cohort C:/tmp/cohort-plan.json --approved-cohort-sha256 <digest> --confirm-project yfinance-cli --receipt C:/tmp/enrollment-check.json
```

After approval of the concrete cohort, control publication uses `apply` with
`TEK2DAY_ALLOW_SEC_ENROLLMENT=1`. It preflights all items and repeats metadata
fences atomically per item. Rerun the same plan after interruption; already
published matching controls are no-ops. Keep the catalog absent until all
controls are verified. Then publish the reviewed bindings in
`sec_catalog/enrollments.json`, deploy every writer/API image, and verify old
writer executions have drained. Existing BNY apply-mode permission does not
authorize adding another issuer to that mode.

Export each private `items[].repairs[]` object to its own immutable file using
`scripts.run_sec_repair.save`. The existing `run_sec_repair.py check/apply`
commands recheck SEC amendments/facts, current Yahoo availability, descendants,
control digest and exact destination before committing the approved plan.
Approval must account for the scheduled worker becoming eligible on catalog
deployment. If it has already filled a planned gap, recapture/review; do not
force a stale approval. No full-universe job trigger is needed.

For recovery, `run_sec_enrollment.py pause` can pause a cohort even before its
first financial write. After an applied repair, the existing per-period
`run_sec_repair.py pause/rollback/resume` commands restore the exact original
conditionally while retaining audits and nested observations. Pause all
affected writers, verify each restoration and only then resume. Retiring an
enrollment is a separate catalog deployment after draining old writers; keep
its control and audit history. No control or historical tree deletion is needed.

## Capacity and mapping limits

- Named capture: at most 20 securities, 100 financial roots per security,
  2,000 captured documents and depth 8. Planning: at most 500 securities per
  reviewed cohort. A financial repair has at most 200 documents in its tree.
- Runtime: default 500 securities / 900 seconds per job, controlled by
  `SEC_FALLBACK_MAX_SYMBOLS` and `SEC_FALLBACK_SECONDS`. Work uses one sequential
  client; the time budget is checked between securities, allowing the current
  bounded issuer operation to finish. The
  SEC client uses at most two requests per second, bounded retries/body sizes and
  a 16-response cache. No new package, service, scheduler or IAM grant.
- `maintenance_state/sec_fallback_cohort` persists the last processed symbol
  and unresolved failures in apply mode. Observe mode writes no checkpoint.
  Crashes replay uncheckpointed work safely. Concurrent checkpoint changes
  fail visibly. Deferred work and prior unresolved symbols keep the existing
  job's failure alert active. Measure runtime before approving a cohort larger
  than one run's capacity; the seven-day grace is not a guarantee of an
  additional unbounded queue delay.
- Industrial standard US-GAAP concepts are reusable. BNY's bank profile is
  not automatically reused for other banks. ADRs, other currencies, mergers,
  successor registrants, delistings and ticker reuse require separate review.
- Optional contiguous-YTD cash-flow subtraction uses actual adjacent report
  dates, matching fiscal starts, exact original accessions and a reviewed
  consolidated basis. Ambiguous or amended prior filings block it. Per-share
  data is excluded from ordinary cohort insertion until split/class basis
  handling is separately reviewed.
- Annual filings produce annual periods. This change does not manufacture Q4
  from annual totals or scan archived submissions. Thus the old AMD/GOOGL/JPM
  Q4 flags are not automatically repaired. Closed reporting intervals expire
  visibly and need a reviewed extension; the narrow AMZN demonstration is not
  ongoing enrollment authority for later periods.
- Existing API ticker semantics, authorization and cache TTLs remain in force.
  Financial cache expiry is 300 seconds; verify stored and served statements
  after expiry. Quotes and estimates keep their own timestamps/horizons.
  Kilby's pinned strict contract remains unchanged, including BNY reads and
  BK's unresolved-rename rejection. Live partner checks use Kilby's authorized
  path only.

SEC's [API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
describes CIK-scoped submissions and Company Facts. Standard consolidated facts
are not a universal mapping of issuer-specific statement definitions; exact
fiscal dates matter more than calendar frames.

## AMZN validation, not execution

The read-only AMZN capture contains ten financial documents. Private bundle
digest: `99d4f81eb76390efe34aea068815477dd0cc7b52a6ca56c0b19cbb8d9d6bd7d0`.
The [proposed enrollment](amzn-proposed-enrollment-20260913.json) and
[exact dry-run manifest](amzn-cohort-review-20260913.json) use the shared
industrial profile with no AMZN-specific application branch.

The [June 2026 10-Q](https://www.sec.gov/Archives/edgar/data/1018724/000101872426000026/amzn-20260630.htm)
identifies Amazon.com, Inc. common shares, par value $0.01, symbol AMZN and the
Nasdaq listing. SEC submissions identify CIK 0001018724; stored provider
metadata corroborates USD/NMS. Filing date is July 31; supplied acceptance is
July 30 at 22:11:13 UTC. These are distinct source fields, not invented times.
The enrollment's source effective date denotes the June 30 report observation,
not a corporate action. Its UUIDs are proposed internal IDs.

The shared translator selects April 1–June 30 revenue $200.606 billion, net
income $62.647 billion, operating cash flow $45.387 billion and June 30 assets
$1,095.689 billion. The plan fills ten missing fields, with zero populated
conflicts, through two atomic financial/audit writes plus one separate control
publication. Other nine financial periods and all descendants remain in place.
Absent standard concepts, including this profile's PPE/capital expenditure
concepts, remain unavailable; this is not a complete balance-sheet mapping.

Cohort digest: `b08ebdc2b886c1577df06bab0797578a5ff0e454d1b1ac80bfecff43bd925b99`.
No AMZN control, financial repair or scheduled eligibility has been applied.
The permitted frozen fixture is `tests/fixtures/sec_amzn_2026_q2.json`, bounded
public SEC data only. No private Firestore payload is committed or permitted
as a consumer fixture without separate review.

Validation: 203 offline tests across eight isolated suites, including 30 new
cohort tests; 53 pinned Kilby contract assertions; seven repository/cloud
architecture checks. The new cases include AMZN public-source translation,
two synthetic issuers through the scheduled worker, interruptions, rollback,
CIK/class isolation, zero conflicts, noncalendar Q3 cash flow and packaging.
