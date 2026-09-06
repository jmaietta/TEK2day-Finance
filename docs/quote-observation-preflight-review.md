# TEK2day quote metadata: preflight review and rollback

Step 1 completed locally on 2026-09-06; no commit, push or deployment was
authorized or performed during that preflight. Jonathan subsequently authorized
the TEK2day `main` release. Its source base and rollback targets were rechecked
unchanged before publication. Pre-release GitHub `main` was
`3bd945b9f57e9f7603183e1949617d978ec60c51`,
rechecked during this review. Candidate: `issue-233-quote-observation` in the
isolated `tek2day-quote-metadata` clone; the original checkout and detached
baseline are preserved.

## Test problems resolved

1. Estimates fixture: a scoped deterministic UTC clock now controls the test,
   while the original capture date remains fixed. New checks verify the real
   14-day/15-day boundary and clock restoration. No production freshness rule
   was weakened and no assertion or test was suppressed.
2. Metrics fixture: the fourth storage read, `get_all_financials`, is explicitly
   stubbed with synthetic per-company financial history. New assertions exercise
   the actual quarterly-share market-cap calculation, annual EPS mapping,
   cross-company separation and absent financials. This does not stub the
   calculation or silently treat a blocked cloud read as a successful test.

Results: **401 passed across 20/20 file-isolated upstream suites**, no failures,
skips or external-access attempts. Both repaired suites also pass their native
script harness (35 assertions each). Six actual producer/JSON/Kilby-verifier
checks pass again. The earlier Kilby evidence/client rerun passed 145 tests.

## Full candidate review

Reviewed the tracked production/test diff and new test, runner, cross-repository
check and documentation files. No blocking defect was identified in this local
review; this is not independent review or live acceptance.

- Timestamp and currency stay bound to the selected price, including fallback
  reads and both caches. Timezone-less/invalid observations remain unsupported.
- Market-cap share basis, financial arithmetic, fiscal periods, authentication,
  cache TTLs, deployment workflows and dependencies are unchanged.
- Contract impact is explicit: new fields and warnings, and unknown currency
  can now be null instead of an assumed USD. The default for other envelope
  callers remains unchanged. Warning-bearing/non-USD summaries are refused by
  Kilby's verifier; existing display strings are not currency evidence.
- Existing ordinary missing-time quotes remain available; this change does not
  claim they are current. Provider metadata and real authenticated HTTP behavior
  still need post-deployment acceptance.
- `git diff --check` passes. No customer data, credentials or tokens are recorded
  in these artifacts. The baseline test failures remain reproducible in the
  untouched baseline rather than being edited away there.

## Verified rollback targets

The [machine-readable record](quote-observation-rollback-targets.json) contains
the full immutable digests, traffic configuration and successful execution IDs.
Read-only checks used the explicitly authorized `jmaietta@ceorater.com` account;
the default active account was not changed. Project `yfinance-cli`, region
`us-central1`. Record time: 2026-09-06 20:28 UTC; revalidate before deployment.

| Resource | Recorded target |
| --- | --- |
| `tek2day-api` | Ready revision `tek2day-api-00126-5xj`, 100% traffic |
| `daily-price-pull` | `daily-prices` digest in the record; 6 tasks, 0 retries |
| `weekly-estimate-pull` | `weekly-estimates` digest; 1 task, 1 retry |
| `quarterly-financial-pull` | `quarterly-financials` digest; 1 task, 1 retry |
| `architecture-check` | Same `daily-prices` digest; 1 task, 0 retries |

All four jobs currently reference mutable `:latest` image tags. Registry digests
were matched against the images of their latest successful executions. The
separate `full-data-pull-v2` job is not part of these workflow image targets and
was not changed. No scheduler, retry or task setting was changed.

## Rollback procedure, only if separately authorized

Do not execute this procedure as a preflight test. Before an approved release,
confirm this snapshot still matches live state and that no unrelated release
has occurred. Record the new release commit and resulting images separately.

If that release must be rolled back:

1. Coordinate with any in-flight deployment/image builds so they cannot overwrite
   the rollback. Do not cancel unrelated work or change schedulers without approval.
2. Return API traffic to the recorded revision, not a rebuilt image from an old
   source commit. A rebuild is not guaranteed to reproduce the recorded artifact.
3. Restore all three job `:latest` tags to their recorded immutable digests.
   Restoring only API traffic leaves scheduled jobs on the new images. Restoring
   the `daily-prices` tag also restores the architecture-check image target.
4. Re-read traffic and tag digests to verify the restore. Keep existing task and
   retry settings; do not manually replay any data pull. An in-flight execution
   retains its resolved image, so assess it separately before deciding what to do.
5. Review/revert the release source through the normal reviewed workflow before
   a later push can reintroduce it. No destructive Git reset is required.

For reference, the API traffic operation would be:

```powershell
gcloud run services update-traffic tek2day-api --to-revisions=tek2day-api-00126-5xj=100 --account=jmaietta@ceorater.com --project=yfinance-cli --region=us-central1
```

For each exact `image` / `tag` pair in the JSON record, the tag-restoration
operation is `gcloud artifacts docker tags add IMAGE_DIGEST TAG` with the same
explicit account and project. These commands were not executed. API rollback
pins traffic to the old revision rather than the former follow-latest setting;
a subsequent authorized rollout must explicitly review its traffic policy.

Image/traffic rollback does **not** undo any data already written by a job.
Investigating or repairing such data is a separate scoped decision, not a bulk
Firestore rollback. No second project or database is needed for this preflight.

Next: obtain explicit approval for the TEK2day release, revalidate rollback
targets, and deploy/verify upstream before any chatllm `test` push. Record
chatllm-test's own prior revision at that later deployment gate. Chatllm `main`
and production remain out of scope.
