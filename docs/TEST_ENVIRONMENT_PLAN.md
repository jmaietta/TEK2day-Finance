# TEK2day test environment: the plan

Written 2026-10-07. **Status: proposed, nothing built.** Every fact below about
production was read from the live project `yfinance-cli` on 2026-10-07, not
from older notes.

## What it is, in one paragraph

A second copy of finance.tek2dayholdings.com that runs the code from a `test`
branch, reads and writes its own database, and never touches production. You
open it at `https://tek2day-api-test-568356743692.us-central1.run.app`, and it
looks like the real site, with a red bar across the top that reads "TEST — data
copied from production on Oct 8, 2026". Kilby's test site (chatllm-test) calls
it instead of production. It never calls Yahoo on a schedule, and by default it
does not call Yahoo at all.

## The real names

The branch is `test`. A push to `test` runs all the tests, then deploys the
test site. Nothing on `test` ever deploys production; only `main` does that, and
only on your word. Code reaches `main` in this order: a work branch, then
`test` (you look at it there), then `main`.

The service is `tek2day-api-test` in `us-central1`, next to production's
`tek2day-api`. It scales to zero when nobody is using it and is capped at 2
instances (production allows 20).

The database is `tek2day-test`, a second Firestore database in `yfinance-cli`,
in `us-east1` like production's `(default)`. There is already a database called
`staging`: it was created on Aug 12 for `repair_financials.py`, holds 10
tickers, and I leave it alone so the two are never confused.

The data copy is a Google "clone" of production's `(default)` database into
`tek2day-test`, taken at a whole minute I name in the command, for example
`2026-10-08T14:00:00Z`. It copies everything as it stood at that minute:
10,269 tickers, 6,826,365 price rows, 90,649 financial statements, 78,805
estimate sets, 21.6 GiB in all. It reads nothing from Yahoo; it is Google
copying its own disk. Right after the copy I delete the 12 login sessions it
brings along, so no production sign-in exists in test. The 3 saved watchlists
come along too; I will check whose they are before the copy and tell you.

The test site runs as its own account, `tek2day-test@yfinance-cli.iam.gserviceaccount.com`.
That account is allowed into the `tek2day-test` database only. Production runs
as `tek2day-api@`, and the data jobs as the default compute account; neither
changes.

The test jobs are `daily-price-pull-test`, `weekly-estimate-pull-test` and
`quarterly-financial-pull-test`. They have no Cloud Scheduler trigger, so they
only run when I start one by hand, with your OK.

The images are the same four images production uses, tagged `:test` instead of
`:latest`. This matters: production's nightly jobs run whatever is tagged
`:latest` at the moment they start, so a test build that wrote `:latest` would
put test code into tonight's production pull. The test workflow never writes
`:latest`, and it stops with an error if it is ever pointed at `tek2day-api`.

## How the Yahoo rule is kept

Your rule from Oct 4: the test environment must not keep pinging Yahoo. Four
things enforce it.

First, no schedule. The test jobs have no trigger. The nightly architecture
check gains one more assertion: if any Cloud Scheduler trigger ever points at a
`-test` job, the morning email says so.

Second, the copy comes from production's database, not from Yahoo.

Third, the test site has a switch, `YAHOO_LIVE=0`, set on by default. Today the
website calls Yahoo live in four places: the company-page quote and its
fallbacks (`terminal._live_quote` and `terminal._yahoo`), the macro dashboard's
live column (`app._macro_yahoo_chart_data`), saved-watchlist quotes, and the
partner API's check for symbols TEK2day does not hold (`partner_api._yahoo_knows`).
All four go out through just two doors: `terminal._yf()`, which loads the
Yahoo library, and the one direct web request in `app._macro_yahoo_chart_data`.
The switch sits in those two. With it on, each of the four gets "no live
quote", and the page shows the stored price instead. A test fails if anyone adds a new Yahoo call that
skips the switch. Production leaves the switch unset and behaves exactly as
today.

Fourth, a hard cap on the test jobs. The daily job can already be limited
(`PRICE_PULL_LIMIT`, `PRICE_PULL_SYMBOLS`), but the weekly and quarterly jobs
cannot: each would fetch about a sixth of all 10,269 tickers, roughly 1,700
Yahoo calls. I add a setting, `YAHOO_MAX_SYMBOLS`, to all three; a `-test` job
refuses to start unless it is set and is 25 or fewer. The daily job's universe
step (which also asks Yahoo about up to 100 new listings) gets the same cap.

## What changes in the code

Five small changes, each off by default so production is unaffected:
`storage.get_db()` reads a new `FIRESTORE_DATABASE` setting (unset means
`(default)`, as now); the `YAHOO_LIVE` switch; the `YAHOO_MAX_SYMBOLS` cap; the
red TEST bar plus a "do not index" header and robots.txt when
`TEK2DAY_ENV=test`, so Google never lists the test site; and a new workflow,
`.github/workflows/deploy-test.yml`. `architecture.yaml` declares the three
test jobs (the nightly check emails about any job it does not know).

## Kilby

Nothing more is needed on TEK2day's side: a8ea0db already lets Kilby's test
account (`chatllm-test-run@chatapp-488502`) call the partner API. On Kilby's
side the TEK2day address is fixed in code (`kilby_backend/tek2day.py:51`,
`BASE_URL = "https://finance.tek2dayholdings.com"`), so the Kilby session makes
it a setting and points chatllm-test at the test address. I will send that
session the address once the test site is up; I do not edit the chatllm repo.

## Order of work

Each step ends with something you can look at, and I stop for your OK.

Before anything else, I fix the false "architecture drift" email (it comes
every morning because the check wrongly flags the test gate added in d7fabce).
While it fires every day, a real problem caused by building the test
environment would arrive buried in the same email.

Step 1, on branch `test-environment`: the five code changes and their tests.
You see the diff and the test run. Not merged anywhere.

Step 2, the cloud pieces, by hand, every command shown to you first: the
`tek2day-test` account and its database-only permission, the clone, deleting
the copied sessions. Then I prove the fence works: from the test account, a
read of production's database must be refused.

Step 3: create the `test` branch and the test workflow, and deploy. You open
the test site and compare a page you know, for example `/AVB`, against
production.

Step 4: the three test jobs, with no triggers, declared in `architecture.yaml`.
None is run until you ask for a specific run (for example "daily prices for
AAPL and MSFT only", which is 2 Yahoo calls).

Step 5: the Kilby session points chatllm-test at the test site.

## Cost

These are Google's list prices for `us-east1` and `us-central1`, read from the
billing catalog on 2026-10-07.

The copy costs $0.20 per GiB, so about $4.33 each time it is taken. Keeping the
test database costs $0.18 per GiB per month, about $3.90 a month; Google's free
allowance covers only one database per project, and production's already uses
it. The test site costs only while someone is using it, likely under $1 a month,
and the 2-instance cap keeps a burst of traffic from running it up. Extra
images and hand-run jobs add well under $1. In all, about $5 to $6 a month,
plus about $4.33 whenever you want fresher data. Deleting the test database
stops the main charge.

## Risks, and what guards each

Test writes to production by mistake. Guarded twice: the test site is told to
use `tek2day-test`, and its account is not allowed into production's database
at all, so a wrong setting fails with a refusal instead of writing. Step 2
proves the refusal before anything is deployed.

Test code reaches production's nightly jobs through the `:latest` image tag.
The nightly architecture check would not catch this (the jobs would still
say `:latest`; only what the tag points to would have changed), so the guard is
in the test workflow itself: it tags only `:test`, and a step fails the run if
any tag it is about to push ends in `:latest`.

Test calls Yahoo. Covered in the section above: no schedule, copied data,
live calls off, and a cap of 25 on any hand-run job.

The test site is mistaken for the real one, or found by search engines. The red
bar names it and gives the copy date; "do not index" keeps it out of Google.

The data goes stale. It is a snapshot; the bar shows its date, and a fresh copy
is one command and about $4.33. I still have to confirm whether Google lets the
name `tek2day-test` be reused immediately after deleting it; if not, a refresh
goes into a new name and the test site is repointed.

Sign-in may not work on the test address at first. Sign-in uses the same
Firebase project as production, and the test address may need adding to its
list of allowed sites. I check it in step 3; it only matters for watchlists
and `/admin`.

The `test` branch drifts from `main`. After each merge to `main`, `test` is
reset to match it, so test always shows production plus the one change under
review.
