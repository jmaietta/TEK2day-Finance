# Quote-observation metadata for Kilby issue #233

Release candidate validated and authorized, 2026-09-06. Source of truth:
[jmaietta/TEK2day-Finance](https://github.com/jmaietta/TEK2day-Finance),
GitHub `main` verified at `3bd945b9f57e9f7603183e1949617d978ec60c51`.

Implementation branch: `issue-233-quote-observation`, in the isolated clone
`C:\Users\jmaie\chatllm\.worktrees\tek2day-quote-metadata`.
Untouched baseline: sibling `tek2day-quote-baseline`, detached at the same commit.
The existing `C:\Users\jmaie\TEK2day-Finance` checkout is unchanged.
Preflight involved no deployment or customer-data write. Jonathan subsequently
authorized releasing this candidate to TEK2day `main` on 2026-09-06. The source
base and live rollback revision/image targets were rechecked unchanged immediately
before publication. Deployment results must be checked in the release's GitHub
Actions runs and Cloud Run; this document is not a live status assertion.

## Response contract

`GET /partner/v1/equities/{symbol}/summary` adds:

- `data.quote.observed_at`: actual observation of the selected price, normalized
  to timezone-qualified UTC ISO-8601; null when unavailable or unusable.
- `data.quote.currency`: provider currency/unit for that selected price; null
  when unknown. Case is preserved (e.g. `GBp` is not converted to `GBP`).
- Definitions for both fields. `units.currency` now uses that quote currency
  instead of defaulting to USD. `units.scale` remains `units`.

These are additive fields, plus a correction to previously assumed currency and
new quality warnings. Consumers must tolerate null currency and the warning
states; API version stays `1.0.0`. No figure is converted or rebased. The existing
market-cap definition remains price times the same diluted average shares.
Existing fiscal periods, calculations, endpoint authentication, routes, cache
TTLs and deployment workflows are unchanged.

`as_of` and `retrieved_at` retain their existing response-time semantics. Neither
is a substitute for `quote.observed_at`. Cache reads never refresh observation
time, including the 30-second quote cache and 600-second Yahoo-info cache.
Old observations are returned honestly; Kilby applies its own age/day limits.

| Selected price source | Observation | Currency |
| --- | --- | --- |
| Chart metadata `regularMarketPrice` | Same metadata's `regularMarketTime` | Same metadata's `currency` |
| `fast_info.last_price` | Null; no matched timestamp | Same fast-info currency |
| Info `regularMarketPrice` | Same info's `regularMarketTime` | Same info's currency |
| Info `currentPrice` | Null; regular-market time is not attributed to it | Same info's currency |

A fallback that only fills previous close does not replace the selected price's
provenance. A fallback price never inherits a different response's chart date.
The snapshot passes this pair through to the partner response without another
provider fetch. Naive/date-only/invalid timestamps, booleans and pandas `NaT`
are refused; exchange-local date conversion is separate and unchanged.

## Quality boundary

Missing observation emits `quote_observation_unavailable`; missing currency emits
`quote_currency_unavailable`. The price remains available for ordinary consumers,
but a caller cannot treat an unstamped quote as verified fresh evidence.

Non-USD quotes preserve their raw unit and emit `summary_currency_unverified`:
this summary also contains stored fundamentals and mixed valuation calculations
whose currency has not been established here. This is not a general currency
conversion fix. Legacy dollar-formatted display strings are unchanged and must
not be used as currency evidence. Kilby's verifier rejects these warning-bearing
responses; this step therefore does not enable non-USD fresh revisions.

## Offline verification

```powershell
python -B scripts/run_offline_regressions.py
python -B scripts/check_kilby_quote_contract.py --kilby-root C:/Users/jmaie/chatllm/.worktrees/issue-233
```

- 60 new quote-provenance tests pass.
- Kilby-side evidence and TEK2day-client regression rerun: **145 passed**.
- Whole upstream suite, file-isolated after the authorized test fixes:
  **401 passed, 20/20 files pass the strict offline runner**. No failures,
  skips or external-access attempts. The runner separately
  refuses a file that attempts external data access even if code swallows the
  resulting exception. It blocks Firestore access before a client is created.
- Both original problems reproduce on untouched GitHub-main code:
  `test_partner_estimates.py::test_a_fresh_snapshot_is_not_flagged` assumes its
  fixed fixture is fresh, but it is 24 days old on the test date;
  `test_partner_metrics.py` has 15 passing assertions/tests but attempts 30
  unstubbed reads via `storage.get_all_financials`, blocked at `storage.get_db`.
  Both are now fixed locally: a scoped deterministic test clock and explicit
  synthetic financial-history fixtures, plus four regression cases. No production
  freshness rule/calculation was changed and no assertion was suppressed.
- The cross-repository check runs the actual quote/snapshot/summary producer,
  serializes JSON and calls the actual Kilby `normalize_observation`: a matched
  synthetic USD quote passes; missing/stale/future observations, missing currency
  and pence fail (six checks). Only external I/O is stubbed. This is not a live
  authenticated HTTP, container, cloud or provider acceptance test.

Baseline reproduction:

```powershell
python -B scripts/run_offline_regressions.py --repo-root C:/Users/jmaie/chatllm/.worktrees/tek2day-quote-baseline test_partner_estimates.py test_partner_metrics.py
```

## Release handoff

The two test problems are resolved and the full candidate has been reviewed
locally. Exact API/job rollback targets were verified read-only using the
authorized account. See the [preflight review](quote-observation-preflight-review.md)
and [rollback record](quote-observation-rollback-targets.json). Revalidate those
targets and authorize deployment separately. In this repository,
pushing root Python changes to `main` triggers both API deployment and data-job
image builds; the scheduled jobs consume their rebuilt latest images. This is
not the isolated `chatllm-test` deployment path. Do not push `main` as a test step.

After an approved upstream deployment, verify the real authenticated summary
preserves observation/currency and unchanged values, then continue authorized
`chatllm-test` container/cloud/provider checks using synthetic artifacts and the
existing shared-project safeguards. No second Firestore project is required.
