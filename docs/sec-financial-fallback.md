# SEC financial fallback: deployed in observation mode

September 12 follow-up: June now exists as a partial Yahoo document. The
[fresh native review and guarded repair](bny-sec-repair-20260912.md) proposes
22 missing fields and two atomic writes, retaining populated values. Its
[exact manifest](bny-sec-repair-20260912.json) supersedes the earlier dry run
based on June's absence. Financial execution and `apply` mode await approval.

Requested September 10, 2026: if Yahoo has not populated financial data one
week after a 10-Q or 10-K is filed, recover the missing values from SEC.gov.
The repository previously had CIK-based filing links and a Yahoo-only stub
repair inside `pull_quarterly_financials.py`. It had no SEC financial mapper,
filing-based grace period, or SEC financial ingestion path. The standalone
repair Dockerfile is unused; no additional job or infrastructure is needed.

GitHub/local main was verified at `ebb17ce2b48ddcbf125100e5eaa83f0be0b553a5`.
After approval, commit `0095514c3a48e395ef4faab8284e7961cc5d1dea` was pushed and
deployed as `tek2day-api-00129-dc2` at 100% traffic. Both build/deploy workflows
succeeded. The existing financial job was configured and rechecked with
`SEC_FALLBACK_MODE=observe`; SEC financial writes remain disabled. No manual job
execution was triggered. See the [deployment receipt](sec-fallback-deployment-20260910.json).
This work preserves the existing branch and unrelated `.claude/` files. No new
packages, IAM changes, partner calls or SEC database writes were performed.
The separately approved BK/BNY continuity migration
was already executed; its [receipt](issue-3-migration-20260910.json) is distinct
from this financial repair.

## Behavior

- The existing financial job runs the fallback after its Yahoo tranche. It
  checks enrolled securities each job run, even if they were outside that
  day's Yahoo tranche. The existing Mon–Sat schedule is unchanged. Eligible
  recovery occurs on the first subsequent successful run, not necessarily at
  the exact seven-day instant; Sunday and job failures can delay it.
- SEC submissions identify filed reports, including periods wholly absent from
  Yahoo. The wait is 168 hours after a supplied SEC acceptance timestamp. If
  only a filing date exists, a conservative deadline based on the latest end
  of that US filing day is used; `accepted_at` stays null. An earnings release
  alone does not start this particular fallback's timer.
- Fresh Yahoo observations get the first opportunity. The current tranche's
  already-fetched data is reused. Otherwise only enrolled eligible issuers are
  fetched. Missing mapped fields, not merely a missing document or HTTP 200,
  trigger SEC inspection. A partial statement can be repaired.
- A candidate must include revenue, parent net income, total assets and
  operating cash flow. Other supported fields can remain unavailable; neither
  zero nor an absent optional field is invented. Stored zero is populated.
- Populated financial values, including share counts, are retained. A conflict
  prevents automatic mixing; the complete candidate and conflicts are retained
  for review, and the parent job reports an unresolved outcome.
- SEC recovery never refreshes quotes, price history, earnings estimates or
  estimate horizons. Financial filing dates, actual report durations and source
  retrieval times are distinct from database application time.

## Identity and mapping

`sec_mapping.py` separates reviewed security/registrant bindings from mapping
profiles. `sec_fallback.py` performs exact-concept translation. CIK locates SEC
data; it never creates a historical join. An enrolled target must have matching
canonical issuer/security IDs, registrant, currency, exact SEC ticker and
listing venue. The existing reviewed route fences renamed/paused securities.
Unreviewed issuers are counted in the job's enrollment log and are not written.

The initial binding is **BNY only**. This respects the authorized BK/BNY scope;
this is not an approved automatic migration of the entire ticker universe.
The engine handles 10-Q and 10-K periods and includes a reusable industrial
profile, but enabling another issuer requires reviewing its identity and field
definitions. Matching names, CIKs or labels alone cannot enroll it.

The SEC [API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
states that Company Facts aggregates standard-taxonomy facts applying to the
whole filing entity. Custom/segment/class-specific concepts are not a universal
substitute. We select the exact accession, form, filed date, concept, unit,
start and end; `fy`, `fp` or a calendar frame cannot turn year-to-date values
into quarterly figures. Currency/scale are explicit: SEC USD facts are dollars,
not the millions printed in a financial statement.

Direct quarterly durations are 70–105 days; annual durations are 330–400 days,
allowing ordinary 52/53-week calendars. Unusual transition periods are held for
review. Repository keys retain the calendar bucket of the actual period end,
while exact start/end dates identify the fiscal period. A 10-K creates an annual
period, never a mislabeled fourth quarter. Fourth-quarter derivation is not yet
supported.

Cash-flow YTD subtraction requires an explicit reviewed bridge between the two
filings and a contiguous quarter boundary. There is currently one such bridge:
BNY June 2026. Other Q2/Q3 filings needing cross-filing subtraction are held for
review until that basis is verified. EPS and weighted average shares are never
derived by subtraction and are enabled only for explicitly reviewed periods.
Future amendments or multiple filings for the same period are retained as a
review condition; the engine does not automatically choose the newest figures.

## Verified BNY example

The public bounded [fixture](../tests/fixtures/sec_bny_2026.json) comes from
`data.sec.gov` CIK **0001390777**, not the private Firestore export. SEC
submissions identify accession **0001390777-26-000086**, report end June 30,
filed July 31, accepted **2026-07-31T20:31:16.000Z**. Its seven-day deadline is
**2026-08-07T20:31:16Z**. The July 15 earnings release and the July 31 filing
are different events.

The [Q2 company-hosted 10-Q](https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf)
was checked against the [Q1 company-hosted 10-Q](https://www.bny.com/assets/corporate/documents/pdf/investor-relations/form-10-q-1q26-final.pdf)
and the prior stored Q1 field definitions. The private comparison was retained;
it is not committed. Dissimilar staff expense, intangible and debt definitions
were excluded rather than treated as evidence that existing values were wrong.

| June-quarter field | SEC candidate |
| --- | ---: |
| Total revenue | $5.698 billion |
| Net income attributable to parent | $1.761 billion |
| Net income available to common | $1.696 billion |
| Diluted EPS / average shares | $2.45 / 692,223,000 |
| Total assets | $525.019 billion |
| Quarterly operating cash flow | $2.462 billion |
| Quarterly capital expenditure | −$487 million |

Operating cash flow is six-month **−$551 million** minus Q1 **−$3.013 billion**.
Bank revenue sums noninterest income and net interest income. Total equity and
minority interest include redeemable temporary equity to match the existing
Yahoo definitions. Generic cash, debt and intangible field guesses are excluded.

The [reproducible dry run](sec-fallback-bny-dry-run.json) proposes **26 fields**
and **two atomic document writes**: canonical 2026-Q2 plus its audit document.
It is based on the saved migration verification, not a new live inspection.
Exact paths, candidate digest, source lineage, proposed writes and rollback
parameters are in the manifest. Re-read the current period and descendants
before approving a live repair. No June financial repair has been executed.

## Writes, recovery and serving

`sec_maintenance.commit_candidate` reads the route, canonical root, destination
and audit inside a Firestore transaction. The selected period and its original/
candidate audit publish atomically. No document copy is assumed to copy nested
collections; descendants are retained in place. A repeated unchanged source is
a no-op even when its retrieval time changes. The first ingestion timestamp
survives gap filling. All selected values and calculations retain per-field SEC
provenance; conflicting candidates retain their own observations.

Rollback first requires pausing the existing identity route. It enumerates all
descendants, refuses newer observations or a changed selected document, restores
the captured original (or original absence) and retains the audit with a rollback
marker. That marker stops automatic reapplication. The route stays paused until
restoration is verified; a changed financial observation requires a reviewed
reverse plan. Do not roll back to identity-unaware serving code.

The existing financial cache expires after 300 seconds. Route pause/activation
also changes its existing identity cache key. No new cache infrastructure is
introduced. After approved application, validate after cache expiry and browser
reload. Quotes retain their own observation time and cache.

Financial responses identify SEC EDGAR in provenance and provide filing/field
lineage. A mapped subset remains `partial`, not a claim of full statement
coverage. Summary/comparison/metrics provenance includes SEC when its stored
financial inputs are present. Partner symbol behavior stays exact: BNY remains
BNY; BK is refused as retired. No alias envelope, transparent redirect or
`security_resolution` contract is introduced. Chatllm is untouched.

## Operational rollout and validation

`SEC_FALLBACK_MODE` defaults to **off**, which performs no SEC/database work.
`observe` fetches and plans but writes no financial or audit documents. `apply`
enables the guarded writes for reviewed bindings. Begin with BNY observe mode,
review a fresh manifest, then approve application. Do not set apply merely by
deploying this code. Existing scheduled job retries, logs and failure monitoring
remain in use; SEC errors and unresolved amendments propagate to job failure.

SEC requests identify TEK2day, run sequentially at at most two per second, obey
bounded Retry-After handling and do not cache failures. Each response is limited
to 25 MB, the job cache to 16 responses and eligible recent filings to 16 per
issuer. Requests outside the known SEC endpoints are rejected. The
[SEC fair-access guidance](https://www.sec.gov/about/developer-resources)
sets an aggregate ceiling of ten requests per second across machines. This
implementation adds no concurrent SEC worker pool. It deliberately does not
download bulk archives; older history outside recent submissions is disclosed.

Validation: 44 new offline regression tests pass, including public BNY figures,
the seven-day boundary, annual/noncalendar periods, wrong units/contexts,
different registrants/classes/ADRs, conflicts, null/zero handling, partial
statements, HTTP/cache behavior, atomic interruption/retry, nested-history
preservation, conditional rollback and job failure reporting. The pinned real
Kilby guard passes 22 existing identity assertions plus 15 SEC response
assertions offline. Relevant existing financial/partner/valuation/identity tests
also pass. The new pinned container builds, deployment workflow smoke test and
post-deployment BK/BNY first-party income reads passed. Local libraries differ
from deployed pins; actual observation-job results and authorized live Kilby
checks remain rollout validations. March remains the latest live financial
period; observation mode does not apply the pending June repair.

No dependency pins or Dockerfiles changed. All runtime modules are root Python
files, included by the existing image `COPY *.py`, and listed in package modules.
The earlier scripts `sec_financial_fallback.py` and `prepare_bny_sec_backfill.py`
under `scripts/` remain a read-only diagnostic prototype; the scheduled runtime
uses the root modules described here.
