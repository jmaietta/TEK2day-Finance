# BNY June financial repair: prepared, awaiting financial-write approval

The BK/BNY identity migration is already complete. This is a separate financial
gap repair. Native reads on September 12 found a June 2026 Yahoo stub, updated
`2026-09-12T16:31:51.304941Z`, with ten finite financial values including EPS,
share counts and a zero. Revenue, parent net income, assets and cash flows were
missing. Fresh normalized Yahoo observations have the same gaps.

The [exact public manifest](bny-sec-repair-20260912.json) proposes **22 gap fills,
zero conflicts, two atomic document writes**. Original populated values, zeros,
nulls outside the reviewed mappings, dates and ingestion timestamp survive.
March and every other financial period are outside this repair. No financial
database writes have been executed. Automatic fallback remains `observe`.

## Evidence and scope

- Firestore: existing project `yfinance-cli`, Native `(default)`, `us-east1`.
  Reads used the existing named internal maintenance account, not partner access.
- Captured all 11 financial period roots and their nested descendants: 21
  records total, five nested collection paths. The June target exists and has
  no subcollections. Tree digest:
  `e166af5466d687b6427a4156e010eaaa1889744dbba4d864addcb62ee1f0e285`.
- Private directory:
  `C:/tmp/tek2day-issue3-20260909/bny-sec-review-20260912/`.
  `native.json`, `plans.json`, `yahoo.json`, exact `companyfacts.json` and
  `submissions.json` bytes, source receipts, `review.json`, `cloud.json`,
  `approved-candidate-plan.json` and `native-preflight.json` remain outside Git.
- SEC Company Facts retrieved September 12 at `23:46:47.552784Z`, 7,827,785 bytes,
  SHA-256 `5e1d416fbbc5df89085c4e9d2f32ee91dfae321cb8f3a29a107699e974d926cd`.
  [Exact endpoint](https://data.sec.gov/api/xbrl/companyfacts/CIK0001390777.json).
- [SEC submissions](https://data.sec.gov/submissions/CIK0001390777.json) SHA-256
  `0c75a2258b993056338adaa465256cc5b3ed52096aa8d25532567b5fbe6c9ba5`.
  Eligible reports are March and June 2026; no eligible amendment replaces June.
- June accession `0001390777-26-000086`, report end June 30, filed July 31,
  accepted `2026-07-31T20:31:16Z`; seven-day eligibility began
  `2026-08-07T20:31:16Z`. [Company-hosted 10-Q](https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/form-10-q-2q26.pdf).
  Existing reviewed Q1/Q2 PDF digests and cash-flow bridge remain in
  `sec_mapping.py`; all 26 candidate values match the saved public SEC fixture.
- Candidate semantic key (excludes source retrieval time):
  `1d0d3aa5275462a6d911d0b2332d87df854a5cf671d1138481e340e18f0b989b`.
  Four per-share fields already exist, leaving 22 new fields. Revenue $5.698bn,
  parent net income $1.761bn, assets $525.019bn and quarterly operating cash
  flow $2.462bn come from the reviewed mapping. This remains a supported subset
  of the statements, explicitly marked partial.
- March has every mapped field and the scheduled engine skips it. A diagnostic
  comparison found Yahoo revenue $5.336bn versus SEC $5.409bn. This does not
  establish that the stored observation is wrong. No overwrite or March audit
  is proposed; reconciliation of that difference is separate work.
- Narrow existing financial-job logs independently recorded June `fill`,
  22 fields, zero conflicts, mode `observe` at September 12 `19:51:34.759321Z`
  and `23:37:42.708093Z`. No job was triggered by this review.
- Native plan check completed September 13 `03:18:36.010527Z` (September 12
  Eastern time): exact original digest/update time unchanged, proposed audit
  absent, source candidate unchanged, fresh Yahoo still missing the 22 fields.
  Nonempty Firestore commits were blocked at the RPC layer during this check.

## Exact operation and recovery

Private plan SHA-256:
`1d2d3401552330e6c2ef12645e44482a8d4765ea3f9eb47e5869c208565eecd1`.
The public manifest gives both full paths, original and write-template digests,
all field names, source receipt and timestamp substitution rules. Full original
and proposed document payloads are in the private plan. No execution timestamp
has been invented. Source dates stay fixed; operation time is captured only
when execution actually occurs.

`scripts/run_sec_repair.py prepare` produces that plan entirely offline.
`check` rereads current SEC/Yahoo and every nested target collection, then
validates the original record, update time, known descendants, identity route,
source and exact proposed payloads inside a native read-only transaction.
A new amendment, changed facts, newly supplied Yahoo gap, changed financial
record, unknown audit or mismatched identity refuses execution. Descendants
are never overwritten or deleted. Concurrent inserts after enumeration cannot
be excluded by Firestore collection listing; they are retained, and all known
document reads plus the selected parent participate in transaction conflicts.

After separate approval, use the same command with `apply` and the explicit
`TEK2DAY_ALLOW_SEC_REPAIR=1` environment gate:

```powershell
python -B scripts/run_sec_repair.py apply `
  --plan C:/tmp/tek2day-issue3-20260909/bny-sec-review-20260912/approved-candidate-plan.json `
  --approved-plan-sha256 1d2d3401552330e6c2ef12645e44482a8d4765ea3f9eb47e5869c208565eecd1 `
  --confirm-project yfinance-cli `
  --receipt C:/tmp/tek2day-issue3-20260909/bny-sec-review-20260912/applied.json
```

The transaction replaces the partial June document with its gap-filled version
and creates its SEC observation containing the complete original, candidate,
selected fields and approval digest. A crash cannot expose only one write.
Repeating the same plan returns a no-op if its applied values still match.
Changed applied values or a prior rollback hold the candidate for review.

Rollback uses the same arguments and write gate, with separate `pause`,
`rollback` and `resume` actions and a new receipt path for each. Pause validates
the applied record and approved audit before fencing all BNY maintenance.
Rollback enumerates every descendant, refuses later observations or a changed
financial document, restores the exact original stub, retains all children and
marks the audit rolled back. Server commit time determines observation order;
missing ancestors remain absent. The route stays paused until restoration has
been verified. Resume requires the retained rollback marker and original
financial digest. Do not roll back the earlier ticker migration to undo this
financial repair. If newer data prevents reversal, retain it and prepare a
separately reviewed reverse plan.

## Rollout and acceptance

The existing push authorization covers publishing the tested operator guards.
The separate financial action awaiting approval is: apply this June plan and
enable `SEC_FALLBACK_MODE=apply` on the existing `quarterly-financial-pull` job,
project `yfinance-cli`, region `us-central1`. No scheduler trigger is proposed.
The unchanged registry enrolls **BNY only**. Current March is skipped; June
becomes a no-op after repair. Future supported BNY reports can use the same
seven-day policy. Unreviewed YTD bridges, amendments and unsupported mappings
hold for review and use existing failure reporting. Other issuers require
separate identity/mapping enrollment. This is not a universal CIK-based join.

After approval: record both native commit timestamps and payload digests,
verify retained originals and all prior periods, check first-party BK/BNY
income/balance/cash-flow views after the five-minute financial cache TTL, then
verify a native no-op candidate rerun with writes blocked. Check quotes,
estimates/horizons and metadata independently; SEC financial ingestion cannot
make those inputs fresh. Observe the next ordinary maintenance execution.

The partner contract stays strict: BNY request/resolved/payload ticker BNY;
BK returns 409. No `security_resolution` or transparent redirect is introduced.
SEC attribution and partial-statement warnings are already supported by the
offline Kilby adapter. Actual live partner response checks must use the existing
authorized Kilby path. No local partner calls or chatllm modifications.

Offline validation: 19 new repair tests, 44 SEC tests, 38 identity tests and
45 registrant-succession tests passed (146 total); the pinned real Kilby policy
passed 53 assertions. These cover exact writes, zeros/nulls, existing/missing
periods, source changes, amendments, interrupted commits, nested missing
ancestors, reruns, rollback, route fencing and current consumer semantics.
Dependencies, Dockerfiles and workflow definitions are unchanged.

Issue #3 remains OPEN pending financial-write approval, execution and acceptance.
Chatllm #221 remains OPEN; #233 remains closed. The
[closure record and return prompt](issue-3-closure-and-kilby-handoff.md) remain
the integration handoff; private exports and current Yahoo diagnostic captures
are not permitted frozen partner fixtures.
