# Reviewed daily-history gap repair

The reusable operator workflow fills absent daily documents for an already
reviewed security. It uses the same canonical route or ordinary enrollment
control as readers and maintenance. It does not discover identity from ticker,
CIK or name, enroll securities, or turn a merger/succession into a rename.
The first live application, BNY, was subsequently approved, executed and fully
verified September 13. See [execution record](bny-price-execution-20260913.json).
The preparation details below are retained as the exact reviewed proposal.

## BNY evidence and selection

September 13 native capture `53ecd688882b19d7e08a70bd16a4cfed95a7625d2c2d35c232be1dc9f68e899f`
enumerated all 1,328 documents, including missing ancestors and every nested
subcollection, under the canonical root and its control. It includes 1,288
selected daily bars, starting May 26, 2021. Free Yahoo evidence contains 1,327
daily rows for June 1, 2021 through September 11, 2026, with explicit
`auto_adjust=False`, `back_adjust=False`, `repair=False`, actions and exchange
timezone. No split appears in this captured provider interval. The provider
metadata is BNY / USD / NYQ / EQUITY / America/New_York.

The reviewed repair window is June 12 through September 11, 2026. All 42
previously identified missing dates remain absent. All 21 overlapping stored
closes match the provider without dividend adjustment at four-decimal storage
precision. Both stored boundary dates exist. Insert only the 42 absent dates
on that basis, preserving Yahoo's source retrieval time and the date of each
observation. Do not describe `Close` as universally split-unadjusted: Yahoo can
adjust it for splits, and split-bearing captures are refused by this workflow.

Retain all populated overlaps. The new capture has 20 differing fields across
16 overlapping bars: 16 volumes, two highs and two lows. Their original and
provider values remain in the private plan; the public manifest contains field
paths, value digests and the explicit retain-existing selection. Dividend
adjustment is deliberately excluded from the inserted OHLC. The older legacy
series is **not normalized**: of 1,285 provider/stored overlapping closes over
the larger capture, 48 match unadjusted only, six match both and 1,231 match
neither current provider basis. Different adjustment vintages are a hypothesis,
not proof those populated prices are wrong. Three earlier stored dates are
outside the provider capture. This proposal does not repair those differences.

See [exact public manifest](bny-price-gap-review-20260913.json). Plan SHA-256:
`d6bf88ee1de4bf72e03d790dac9352746ed03286cd0bbe43f5b032e15503bfb8`.
It proposes exactly **43 atomic writes: 42 absent price documents plus one
immutable source/selection audit**. It changes no metadata, financials,
estimates, alias/control contents or existing price documents. The native
transaction check returned `ready` September 13 at
16:44:27.964116Z with nonempty database commits blocked.

## Operator workflow and approval

Run from the source checkout. No new dependencies, Docker changes, services,
IAM grants or scheduled jobs are required. All root modules already ship in
the existing Docker images. Operator scripts are not scheduled entrypoints.

1. `scripts/capture_price_repair.py` takes one current reviewed symbol, an
   explicit source date range (end exclusive), and a new private output. It
   enumerates the whole routed tree, up to 8,000 documents/depth eight, and
   captures at most 1,600 provider rows. Provider symbol/currency/venue checks
   supplement the reviewed security identity; they never authorize a join.
2. `scripts/run_price_repair.py prepare` consumes that frozen capture, a new
   private plan path, a public manifest path, the repair window and a written
   basis rationale. The window is at most 100 calendar days; at most 90 absent
   dates may be repaired atomically. Incomplete rows, populated/null parents,
   missing parents with children, splits, ambiguous close bases, duplicate
   dates and missing boundary evidence require separate review. Zero volume
   is valid; null volume is unavailable. This is not an exchange-calendar
   completeness claim: absent provider rows remain unknown.
3. `check` re-enumerates every proposed target's nested tree and uses native
   transaction reads to fence the canonical control, identity, all overlaps
   and absent destinations. It blocks nonempty commits at the RPC boundary.
4. After approval of the exact plan, verify all writer builds and that older
   executions drained. `apply` additionally requires the plan SHA-256,
   `--confirm-project yfinance-cli` and `TEK2DAY_ALLOW_PRICE_REPAIR=1`.
   A changed destination or basis anchor requires a new review. All dates and
   the audit commit together; there is no partially published repair. A lost
   reply is resolved by rerunning the same plan: the exact audit and selected
   bars produce a no-op. No automatic catch-up or universe scan is enabled.
5. Use `scripts/verify_price_repair.py` to capture and compare all nested
   records afterward. Verify 42 added dates,
   43 exact writes, identical server commit timestamps, unchanged protected
   observations and no split recreation. Check first-party old/new reads and
   the authorized Kilby path separately. Follow the existing price-view cache
   TTL before display checks; current quote observation time is independent
   from historical bar coverage. Financial and estimate caches remain separate.

Reviewed repaired bars are pinned by their provenance marker. The shared
rename and enrolled-security maintenance writers retain a later differing
provider observation in their existing `identity_observations` subcollection.
Identical values with only retrieval/provenance differences create no revision.
Neither the ordinary five-day window nor this tool automatically authorizes
another security, a longer catch-up range or revised populated values.

## Recovery and rollback

Offline validation passed: 223 tests across seven isolated suites (30 price
repair, 38 identity, 31 SEC cohort, 45 succession, seven price-job exit,
12 bounded price-job selection, 60 quote observation), plus 53 pinned Kilby
producer/consumer assertions. No live partner endpoint was called.

An interrupted transaction publishes either all 43 writes or none. Recovery
uses the frozen plan and immutable source audit, without depending on Yahoo
remaining available. `pause` conditionally pauses the exact reviewed control;
reads retain the selected data while guarded writers stop. Drain older writers
and re-enumerate targets. `rollback` deletes only the exact 42 newly created
documents if their payloads and server update times still match the original
atomic audit and they have acquired no descendants. Otherwise it refuses and
preserves the observations for a new review. The audit remains with a rollback
marker; reapply is held. Verify restored absence and protected records before
`resume`. These rollback writes require approval; diagnosis never invokes them.

Code rollback before data repair can restore the prior serving commit
`14e674ab3985007c4e8f6855f997d6ae0b30ae29` and its images recorded in
`amzn-execution-20260913.json`. After an approved price repair, retain the
provenance-aware writer guard or pause writers until data rollback is reviewed.

## Remaining acceptance and subsequent work

The normal financial schedule was read again September 13: enabled,
`0 12 * * 1-6`, America/New_York; next scheduled attempt September 14 at
16:00:03.020211Z. The last run is still Saturday's pre-correction failure
`quarterly-financial-pull-g5f7z`. Latest price run
`daily-price-pull-p5gqs` completed successfully September 11 at
21:38:29.919467Z. No manual job was run. The next normal execution must still
verify BNY/AMZN financial repair retention and fallback behavior.

Issue #3 remains open. Continue the separate financial coverage/definition
reviews and authorized Kilby acceptance before closure. After this work, the
user wants CEORater re-enabled in TEK2day Finance and Kilby, using the repaired
CEORater dataset, API and MCP. Review their current published contract and
authorization at that time. No CEORater activation or chatllm change is part of
this price repair. Chatllm #221 stays open; #233 stays closed.

Private evidence: `C:/tmp/tek2day-issue3-20260909/`:
`bny-price-full-capture-20260913.json`, `bny-price-gap-plan-20260913.json`,
`bny-price-gap-native-check-20260913.json`. Never commit these raw exports.
