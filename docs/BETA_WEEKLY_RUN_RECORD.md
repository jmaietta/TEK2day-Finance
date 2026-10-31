# Weekly beta table: run record

What was run to prove the stored weekly beta (beta.py, weekly_beta.py) before
it reaches production. The owner gave GO 1 on 31 Oct 2026; the checks were
agreed with Kilby. Nothing here wrote to production (`yfinance-cli`, database
`(default)`).

The scripts are kept outside the repo, with their sha256 prefixes, in
`C:\Users\jmaie\beta-check\scripts\`:

| script | sha256 prefix |
|---|---|
| pinned.py | a286fb2ed97e86c7 |
| beta_export.py | 9ebbe9739586d467 |
| beta_tieout.py | fd5b10f21e974cbe |
| beta_trial2.py | 8ddb13386899453e |
| beta_trial_bny.py | d8f165274ecd899b |
| beta_excel.py | 0123aaf6e6bcd4d0 |

## One snapshot

Every production read below is pinned to **read_time 2026-10-31T06:00:00Z**,
using Firestore point-in-time reads (PITR is on, 7 days). This is after the
30 Oct price run finished at 21:33 UTC and the quarterly run at 23:02 UTC.

`pinned.py` sets that read_time on every get, get_all and query in the
process. It counts reads and aborts past 2x the script's estimate. It also
refuses every write call (set, update, delete, create, batch, commit, bulk
writer).

## Identity

- **Production reads:** default credentials, jonathanmaietta@gmail.com. Its only
  role on yfinance-cli is `roles/datastore.viewer`.
- **Trial writes:** `beta-trial-writer@tek2day-beta-trial.iam.gserviceaccount.com`.
  It was used through impersonation from jmaietta@ceorater.com, with no key.
  - Its only role was `roles/datastore.user` on project tek2day-beta-trial. It
    had no binding in yfinance-cli.
  - Policy Troubleshooter, permission `datastore.entities.create`:
    yfinance-cli `ALLOW_ACCESS_STATE_NOT_GRANTED`, tek2day-beta-trial
    `ALLOW_ACCESS_STATE_GRANTED`.

## Runs

| step | what | reads | result |
|---|---|---|---|
| A: closes export | every stored close 2024-10-21..2026-10-30, every active symbol plus SPY | 4,218,276 rows | `closes.csv.gz`, 9,662 symbols, 0 error rows; sha256 bf9d5087…305124 |
| B: live betas | origin/main's on-request beta.py, every active symbol | (A+B 5,335,558 in all; estimate 5.7M) | `betas_live.csv`, 10,298 rows: 8,106 betas, 2,067 short_history, 125 not_enough_weeks; sha256 a0b65549…3adac4ab5 |
| Kilby's recompute | independent, from A | none | all 10,298 match B; beta/R² largest relative gap 1.1e-15; other fields exact |
| Full tie-out | new code's fit in memory, every active symbol, against B, bit for bit (`repr`) | 1,103,106 (estimate 1.15M) | **0 differences, 0 errors**, 10,298 compared; `betas_new.csv` sha256 790308e9…378cecb2 |
| Trial trace | the new code's reads for 113 trial companies, recorded | 12,694 | 10,445 documents |
| Trial | those documents copied into tek2day-beta-trial / beta-trial; the job's code run there | free tier | all checks pass (below) |
| Excel | 87 trial betas recalculated by Excel's SLOPE/RSQ from A | none | largest gap: beta 5.8e-15, R² 1.8e-15; weeks equal |

Approximate production read cost: 6.45M reads, about $3.90. Trial: $0. The
trial project could not be linked to billing ("billing quota exceeded"), so
it ran on the free tier.

## Trial checks (all PASS)

- Run 1, the job, wrote 112 of 112 with no failures. The industry table was
  written, complete, with 53 industries.
- Every current doc equals the fit worked out from production.
- There is one history doc per company, equal to its current doc.
- Company records are byte-identical before and after.
- The same week again stops after reading SPY.
- `--force` held all 112, wrote 0, and left history unchanged.
- A doc naming another symbol reads `unavailable`.
- The 250-company batched read took 0.117 / 0.101 / 0.161 s, with none unavailable.
- SPY against itself is exactly 1.0, with R² 1.0. Other known answers:
  - VOO 0.9922 (R² 0.9996)
  - QQQ 1.280
  - RSP 0.750
  - SGOV -0.0004
  - GLD 0.229
  - XLU 0.203
  - FDRXX short_history
- BNY: the trace never read `tickers/BNY` (BNY routes through
  security_routes), so the first trial run left it out. With that doc copied,
  `--force` wrote BNY alone (1 written, 112 held), equal to the production fit
  (0.88307 / 0.39025). In production `tickers/BNY` is active, and BNY is the
  only routed active symbol; it routes to itself.

## Cleanup

- Project tek2day-beta-trial: `DELETE_REQUESTED`. Its service account was
  deleted (0 listed) and its database was deleted.
- yfinance-cli IAM policy has no binding that mentions beta-trial.
- The empty `beta-trial` database made in yfinance-cli at 05:25 UTC was
  deleted. The databases are now `(default)` and `staging` only.

## After Kilby's code review (31 Oct 2026)

Four fixes went in after the runs above, with tests:

- A row whose own beta could not be read gets no industry stand-in.
- An incomplete run keeps the previous week's medians.
- Companies that a complete week left out are retried the next evening.
- Each complete week's medians are kept in beta_industries/latest/history/{week}.

`fit()` and `compute()` did not change, so the tie-out and Excel results
stand. The trial's run() checks predate the fixes; the tests cover the new
paths.

Owner, the same day:

- The hovers for not_yet_computed and short_history now read "Insufficient
  history".
- A company added midweek gets its beta the next weekday evening. Each
  evening reads the active list (about 10,300 reads, about $0.006) and
  works out only the companies the week does not cover yet.

## Still to come (each needs the owner's go), in this order

The first production write comes BEFORE the merge (Kilby, 31 Oct 2026).
Merging first would deploy code that reads an empty beta_weekly: every stock
would read "Insufficient history" and Kilby would get no betas until the
first run finished. Today's live code never reads beta_weekly, so writing it
early is harmless.

1. **GO 2:** push the branch and open the PR. No merge.
2. **GO 3:**
   - Export the `tickers` collection to gs://yfinance-cli-firestore-backups/.
   - Run the PR's code once, by hand, writing beta_weekly (current and
     history), beta_industries/latest, its history, and
     beta_industries/coverage.
   - Read every one of those docs back and compare it bit for bit
     (computed_at aside) with a fresh in-memory computation.
3. **The owner merges.** Betas are served from the first request.
4. **GO 4:** the weekly-beta Cloud Run job and its weekdays 8 PM ET schedule.

Each step's result is added here.
