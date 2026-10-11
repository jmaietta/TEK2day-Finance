# TEK2day test environment: the plan

First written 2026-10-07; revised the same day after reading the whole repo and
after your rulings in conversation. **Status: proposed, nothing built.** Every
fact about production below was read from the live project `yfinance-cli` on
2026-10-07.

## 1. What it is

A second copy of finance.tek2dayholdings.com, used only to try changes before
they reach production. It runs the code from a `test` branch, on its own copy of
the data, and it never touches production.

You would open it at `https://tek2day-test-568356743692.us-central1.run.app`.
It looks like the real site, with a red bar across the top that reads
"TEST — data copied from production on Oct 9, 2026". Kilby's test site
(chatllm-test) would call it instead of production.

Production's website is the service Google calls `tek2day-api`. Despite the
name, that one service is the whole website: the home page, the company pages,
/macro, sign-in, watchlists, exports, and the partner API Kilby uses. The test
copy is a copy of all of that.

## 2. The Yahoo rule

All of TEK2day's data comes from Yahoo, and TEK2day feeds Kilby. A second
TEK2day that also went to Yahoo would double what Yahoo sees and raise the risk
of being cut off. So the test environment never goes to Yahoo, in any way.

It gets its data only by copying production's database, which production has
already collected. Yahoo sees exactly what it sees today: one TEK2day, making
its usual scheduled calls.

The test environment has no data jobs: no nightly price pull, no estimate pull,
no financial pull, scheduled or by hand. Its website has live Yahoo calls
switched off (section 6). If a change to a data job ever needs even one call to
Yahoo to be tested, I ask you first and tell you the exact number of calls.

## 3. What you would see

Say the copy is taken on a Thursday evening and you open AAPL on the test site.
The price at the top is Apple's closing price from that Thursday. The chart is
the same five years of stored history production has. Market cap and the ratios
are worked out from that closing price. The financial statements, estimates and
short interest are the stored ones. The macro dashboard is the same as
production's, because it is read from your separate macro database. CEO data
comes through production's own CEO lookup, which asks CEORater, not Yahoo.

Two things differ from production. The price is the stored close, not a live
price moving during the day. And the news panel is empty, because news only
ever comes live from Yahoo and is not stored anywhere.

When you want fresher data, the database is copied from production again.

## 4. The names

The branch is `test`. GitHub is the record of truth. Code reaches production in
this order: I make the change on a local `test` branch and push it to the
`test` branch on GitHub, which deploys it to the test site, where you look at
it. Then you open a pull request on GitHub from `test` into `main` and merge it
yourself. That merge is what deploys production.

The website service is `tek2day-test`, next to production's `tek2day-api`. It
costs nothing when nobody is using it and is capped at 2 copies running at once
(production allows 20).

The database is `tek2day-test`, a second Firestore database in the same
project, `yfinance-cli`. There is already a database called `staging`, made on
Aug 12 for the financial repair script; it holds 10 tickers, and I leave it
alone so the two are never confused.

The test site runs as its own account, `tek2day-test@yfinance-cli.iam.gserviceaccount.com`.

## 5. The copy of the data

Google copies production's database, `(default)`, into `tek2day-test` as it
stood at one exact minute that I name. It is Google copying its own storage:
none of TEK2day's code runs, and Yahoo is never contacted. The copy brings
10,269 tickers, 6,826,365 daily prices, 90,649 financial statements and 78,805
estimate snapshots: 21.6 GB in all.

Right after the copy I delete the 12 login sessions it brings along, so no
production sign-in works on the test site. The 3 saved watchlists come along
too; I will check whose they are first and tell you.

The copy is made once, on a Saturday, at a minute when no data job is running;
I tell you the minute beforehand. It is not repeated on any schedule. A fresh
copy is made only when you ask for one, also on a Saturday.

I have not timed a copy of this size. Google's own estimate ranges from under
an hour to a few hours; I will time it and tell you how long it took.

Only your four addresses can sign in to the test site:
jonathanmaietta@gmail.com, jon@cycadcap.com, jmaietta@ceorater.com and
jmaietta@tek2day.com (section 6).

## 6. What changes in the code

Six changes. Each is off unless the test site turns it on, so production
behaves exactly as today when they eventually reach it.

1. Which database to use. Today the code always uses `(default)`. A new setting
   points the test site at `tek2day-test`; left unset, it is `(default)`, as now.
2. The Yahoo switch. Every live Yahoo call the website makes goes out through
   just two places in the code (the Yahoo library is loaded in one place in
   `terminal.py`, and the macro dashboard makes one direct request in `app.py`).
   With the switch on, both refuse to call Yahoo, and the price falls back to the
   last stored close, the way the /stock/ pages and the watchlist fallback
   already work. Without that fallback the price would show "N/A". Each refused
   call writes one line to the test site's log, so I can show you a count, and a
   test fails if anyone adds a new Yahoo call that goes around the switch.
3. The red TEST bar, with the copy date.
4. Keeping the test site out of Google: a "do not index" signal on every page
   and a robots file that asks search engines to stay away.
5. No Google Analytics on the test site. The home page, the About page, the
   company pages and the All Tickers page carry production's analytics tag; without this, your visits to the test site would be counted as
   real traffic.
6. Sign-in limited to your four addresses. Today anyone with a Google account
   can sign in to production. On the test site, a sign-in from any other
   address is refused, using the same list that already guards the admin page,
   and only when Google has verified the address.

Plus a new workflow file that deploys the `test` branch to the test site.

## 7. Keeping the test site away from production

Two fences, so one mistake is not enough to reach production's data.

The test site is told to use `tek2day-test`. And its account is allowed into the
`tek2day-test` database only. If a wrong setting ever pointed it at production,
Google would refuse the request rather than let it write. Before anything is
deployed, I show you that refusal: a read of production's database, attempted
as the test account, failing.

The test site never writes production's program images either. Production's
nightly jobs run whatever is tagged `latest` when they start, so a test build
that wrote `latest` would put test code into that night's price pull. The test
workflow tags its builds with the commit only, and stops with an error if it is
ever about to write `latest` or deploy to `tek2day-api`.

Many of the repair and migration scripts are hard-wired to production's
database name and refuse to run anywhere else. That stays as it is; it is a
safety, not a problem.

## 8. How a change would travel

Take the fix for the false morning email, which is waiting now. It goes to the
`test` branch first. All 677 tests run. The test site is rebuilt from exactly
that code. The workflow also builds the three data-job programs and checks that
each one starts and loads all its code, without running any of them against
Yahoo. (Three times, in June and August, a program shipped missing one of its
files and died at startup; this check would have caught each one.) You look at the test site. Only then do you open a
pull request on GitHub from `test` into `main`; merging it sends that same code
to production.

## 9. What the test site shares with production

Three things are shared, and each is safe.

Sign-in uses the same Google sign-in setup as production. The test address has
to be added to its list of allowed sites. That only adds a site; it changes
nothing for finance.tek2dayholdings.com. Today the list holds
finance.tek2dayholdings.com, tek2dayholdings.com, mail.tek2dayholdings.com,
localhost and two Firebase addresses.

The macro dashboard is read from your macro project, `macro-data-222cd`.
Production's website account has read-only access there; the test account would
get the same read-only access, nothing more.

CEO data: the CEORater key is stored in Kilby's project, `chatapp-488502`, where
I cannot change who may read it. Rather than ask for that, the test site gets
CEO data the way the terminal does: through production's own CEO lookup, a
read that goes to CEORater, never to Yahoo.

## 10. Kilby

Nothing more is needed on TEK2day's side: since a8ea0db the partner API already
accepts Kilby's test account (`chatllm-test-run@chatapp-488502`). On Kilby's
side the TEK2day address is fixed in code (`kilby_backend/tek2day.py:51`), so
the Kilby session makes it a setting and points chatllm-test at the test site.
I send that session the address once the test site is up; I do not edit the
chatllm repo.

## 11. Order of work

You review this plan with me, one section at a time. Then I do the work below,
recording each step in the tracker. Nothing in it touches production, and
nothing goes to `main`.

1. The six code changes and their tests, on a work branch, tried on my machine.
2. The test account and its fence, then the database copy on a Saturday
   (timed), then deleting the copied sessions. Here I stop and show you the refusal from section 7.
3. Add the test address to sign-in's allowed sites; give the test account
   read-only macro access; create the `test` branch and the workflow; first
   deploy.
4. You look at the test site: a company you know, side by side with production.
5. I send the address to the Kilby session.

## 12. Cost

The copy costs about $4.33 each time it is taken, and keeping the test database
about $3.90 a month. The test site and its builds add a dollar or two a month.
Deleting the test database stops the main charge.

## 13. Risks, and what guards each

The test site writes to production's data: guarded twice (section 7), and the
refusal is shown to you before anything is deployed.

Test code reaches production's nightly jobs: the test workflow never writes
`latest` and refuses to deploy to `tek2day-api`.

The test site calls Yahoo: no data jobs, live calls switched off, refused calls
counted in the log, a test that fails if a new Yahoo call skips the switch.

The test site is mistaken for the real one: the red bar names it and gives the
copy date; search engines are asked to stay away; no analytics.

The data goes stale: it is a dated copy; a fresh one is one command. I still
have to confirm whether Google lets the name `tek2day-test` be reused at once
after deleting the old copy; if not, a refresh goes under a new name.

Sign-in does not work at first on the test address: checked at the first
deploy; it only matters for watchlists, exports and the admin page.

The `test` branch drifts from `main`: after each pull request is merged, `test`
is brought level with `main`, so test always shows production plus the one
change under review.
