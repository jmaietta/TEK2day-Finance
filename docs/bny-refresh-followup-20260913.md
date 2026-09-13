# BNY scheduled refresh follow-up, September 13, 2026

Issue #3 remains open. No new live data repair is authorized by this record.

## Schedule and verified failure

The existing `quarterly-financial-pull-trigger` is enabled with schedule
`0 12 * * 1-6`, America/New_York, in yfinance-cli/us-central1. Its last attempt
was September 12 at 16:00:25.936334Z. The next scheduled attempt is Monday,
September 14, at noon Eastern. No scheduled execution has yet used the
BNY-only SEC apply mode enabled early September 13 UTC.

September 12 execution `quarterly-financial-pull-g5f7z` failed with exit 1,
completing at 23:37:48.044913Z after retry. It ran in observe mode. Its final
summary identified five failed BNY quarterly writes and an incomplete June
quarter. All five write failures were `maintenance financial period identity
changed`, for 2025-Q1 through 2026-Q1. The stored documents omit `freq`; fresh
Yahoo documents identify the same periods/end dates with `freq=Q`. The original
source of that metadata omission is unknown.

The separate, approved June repair subsequently filled 22 fields. The
pre-repair Saturday failure does not show that the repair failed.

## Bounded code correction

The reviewed-security writer accepts an absent/null quarterly frequency only
when the document key is a valid quarterly key and both reporting period and
end date agree. Actual frequency/period changes and ambiguous annual records
still fail. The stored selected document is retained; real provider revisions
remain separate observations. A frequency-only difference does not manufacture
a revision or normalize the stored metadata.

The SEC fallback now reports `stored_complete` when the reviewed mapped fields
are already present. The parent job checks actual stored coverage for the
required earnings period before clearing a Yahoo-stub failure. Annual failures,
write failures, amendment holds and unresolved missing data still fail the job.
This prevents a later refresh from reporting failure solely because Yahoo still
returns a stub after a successful SEC repair.

Validation: 165 offline tests across six isolated suites (13 frequency, 38
identity, 6 parent-job coverage, 44 SEC fallback, 19 repair, 45 succession).
No API response semantics, dependencies, Docker configuration, issuer enrollment
or SEC financial mappings changed. No manual scheduled-job execution occurred.

## Coverage still under review

User-supplied Kilby income, balance-sheet and cash-flow screenshots confirm that
the authorized consumer display receives the repaired June data. The balance
sheet also matches the TEK2day screenshot. These are display checks, not a raw
partner response/authorization audit or approved frozen API fixtures.

The three-month Yahoo capture contains 42 dates absent from stored BNY history,
including June 22, July 6–9, July 13–16, July 20–September 2 trading dates.
Sixteen of 21 overlapping bars differ. Since ingestion uses adjusted prices,
gap-only insertion needs an adjustment-basis review first. Populated overlaps
will not be silently replaced. The ordinary five-day refresh cannot reach
these older gaps.

June unsupported financial fields, incomplete 2021-FY and March's revenue-basis
difference remain separate investigations. Missing fields are not proof that
populated history is wrong. The current SEC mapped-subset warning remains valid.

Private evidence lives under `C:/tmp/tek2day-issue3-20260909/`:
`bny-scheduled-refresh-20260913.json`, `financial-refresh-error-20260912.json`,
`financial-write-errors-20260912.json`,
`bny-remaining-gaps-provider-20260913.json` (canonical SHA-256
`cfb41b1fa82b2c16e2e9047393f5b09d17c0e94577208ed1d5ad5e94bffb6d44`), and
the existing `bny-sec-review-20260912/maintenance-rerun.json` captured after repair.
Raw exports and credentials are excluded from Git.

Code rollback uses the prior immutable images in
`bny-sec-repair-deployment-20260913.json`, with API revision
`tek2day-api-00131-jnd`. A rollback requires deliberately restoring the reviewed
images; it must not reverse the earlier approved financial repair or change
SEC mode. This correction introduces no data migration requiring reversal.
