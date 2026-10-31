"""TEK2day's own beta: two years of weekly returns against SPY.

Owner decision, 30 Oct 2026, after a day on Yahoo's beta: Kilby's risk table
shows beta beside R-squared, Yahoo publishes no R-squared, and the two must come
from one calculation. Two years weekly is Bloomberg's default, and weekly closes
keep foreign listings (TSM, ASML, NSRGY) from reading low on days their home
market was shut. Spec agreed with Kilby the same day, the one-year minimum on
31 Oct. The website (/ticker, the /stock pages) and Kilby (/equities/metrics,
/equities/summary) all read it here.

THE METHOD, reproducible in Excel from /partner/v1/equities/prices?days=520:
  - Week-end dates: for each Monday-Sunday week, SPY's last stored close in it
    (Friday; Thursday in a Good Friday week).
  - A week is used once it is FINISHED: SPY's close for its Friday is stored,
    or a close from the following week is (a holiday Friday). Never by the
    clock: a late or failed Friday run does not turn Thursday into the week's
    end; the last finished week's window holds until the close is stored. A
    close dated today counts only after the price run has settled (7 PM ET).
    The nightly run rewrites five days, so a later close means Friday's is in.
  - The last 105 week-end dates: 104 weekly returns. A stock with less than
    two years of prices but at least one (owner, 31 Oct 2026) is fitted from
    the first week-end on or after its first stored close, over the weeks it
    has; `years` says how long (1.7 for 88 weeks) and every label shows it.
    It moves up a week each Friday until it is a full two years.
  - A week's return counts only when the stock has closes on both that week's
    end date and the previous one. Nothing is filled in.
  - Slope of the stock's simple weekly return on SPY's: least squares with an
    intercept, Excel's SLOPE. R-squared from the same fit, Excel's RSQ. Raw: no
    Blume adjustment.
  - Blank, with the reason, in this order: split_since_refresh (older closes on
    the pre-split basis until Thursday's 5-year re-adjust), short_history
    (fewer than 52 weekly returns since the first stored close),
    not_enough_weeks (fewer than 52 used, or more missing than 24 of 104).

STORED WEEKLY (owner's go, 31 Oct 2026, agreed with Kilby). The first version
worked every beta out on request, about 107 reads a company, and nobody had
checked that against Kilby's watchlist call (up to 250 companies, a 30 s
timeout, no partial answer). Now the weekly-beta job (weekly_beta.py, every
weekday 8 PM ET) works out every active company once a week and stores it in
its own collection, beta_weekly/{symbol}, which every reader uses: one read a
company, a set in one batched read. Each week is also kept, never overwritten,
in beta_weekly/{symbol}/history/{week ending}. Each industry's median is in
beta_industries/latest, each complete week's kept in its history. Company
records are never written. Every doc carries
its symbol, and a doc that does not match the one it is filed under is
"unavailable", as is every row when the read fails. Stored prices only: no
Yahoo request, ever.
"""
from __future__ import annotations

import logging
import statistics
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from google.api_core.exceptions import AlreadyExists

import storage
from envelope import finite

logger = logging.getLogger("ydp.beta")

BENCHMARK = "SPY"
METHOD = "weekly_vs_spy_up_to_2y"
LABEL = "Beta (Weekly)"
WEEKS = 104
MIN_WEEKS = 52               # a year: below this, blank
FULL_MIN_WEEKS = 80          # of 104; a shorter fit may miss the same share
FIELDS = ("beta", "r_squared", "weeks_used", "years", "reason", "window_start", "window_end")
UNAVAILABLE = {**dict.fromkeys(FIELDS), "reason": "unavailable"}

_EASTERN = ZoneInfo("America/New_York")
_HISTORY_DAYS = 560          # SPY rows read: 105 weeks of trading days and the week in progress
_RUN_START = (16, 30)        # ET, Mon-Fri, as partner_api._price_window
_RUN_SETTLED = (19, 0)
_IN_RUN_SECONDS = 300
STORED = "beta_weekly"        # the collection: beta_weekly/{symbol}
HISTORY = "history"           # beta_weekly/{symbol}/history/{week ending}: append-only
INDUSTRIES = "beta_industries/latest"
_WORKERS = 32                 # the weekly run: Firestore round trips, not work
_STOP_FAILED_SHARE = 0.02     # more than 2% of companies unreadable: fail the run
_RECHECK_SECONDS = 60         # a row newer than the cached medians re-reads at most this often
_INDUSTRY_CACHE: dict = {}    # {"expires", "checked", "window_end", "industries"}
_LOCK = threading.Lock()


def _eastern(now: datetime | None) -> datetime:
    return (now or datetime.now(timezone.utc)).astimezone(_EASTERN)


def expires_at(now: datetime | None = None) -> datetime:
    """When a beta worked out now stops being good: the next weekday 4:30 PM ET
    price run, or five minutes from now while one is writing. UTC."""
    now = _eastern(now)
    start = now.replace(hour=_RUN_START[0], minute=_RUN_START[1], second=0, microsecond=0)
    settled = now.replace(hour=_RUN_SETTLED[0], minute=_RUN_SETTLED[1], second=0, microsecond=0)
    if now.weekday() < 5 and start <= now < settled:
        return (now + timedelta(seconds=_IN_RUN_SECONDS)).astimezone(timezone.utc)
    nxt = start if now < start else start + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt.astimezone(timezone.utc)


def settled(dates, now: datetime | None = None) -> list[str]:
    """The stored dates a beta may use: every day before today, and today's
    only once the price run has settled."""
    now = _eastern(now)
    today = now.date().isoformat()
    after_run = (now.hour, now.minute) >= _RUN_SETTLED
    return [d for d in dates if d < today or (d == today and after_run)]


def week_ends(dates) -> list[str]:
    """The last date of each FINISHED Monday-Sunday week among `dates` (SPY's
    settled dates, oldest first). The newest week is finished only when its
    Friday is among them; a holiday Friday finishes when next week's first
    close arrives, which makes that week the unfinished one."""
    if not dates:
        return []
    last = {}
    for d in dates:
        last[tuple(date.fromisoformat(d).isocalendar())[:2]] = d
    ends = sorted(last.values())
    newest = date.fromisoformat(dates[-1])
    if newest.weekday() < 4:
        ends = ends[:-1]
    return ends


def compute(ends, spy: dict, stock: dict, first_close: str | None = None,
            split: str | None = None) -> dict:
    """Beta of `stock` on `spy` ({date: close}) over the last 105 of `ends`, or
    from the first of them on or after `first_close` when the stock is younger."""
    window = list(ends)[-(WEEKS + 1):]
    if first_close:
        window = [d for d in window if d >= first_close]
    out = dict.fromkeys(FIELDS)
    out["window_start"] = window[0] if window else None
    out["window_end"] = window[-1] if window else None
    possible = max(len(window) - 1, 0)
    out["years"] = round(possible / 52, 1) if possible else None
    xs, ys = [], []
    for a, b in zip(window, window[1:]):
        if all(finite(c) and c > 0 for c in (spy.get(a), spy.get(b), stock.get(a), stock.get(b))):
            xs.append(spy[b] / spy[a] - 1)
            ys.append(stock[b] / stock[a] - 1)
    out["weeks_used"] = len(xs)

    if split:
        out["reason"] = "split_since_refresh"
    elif not first_close or possible < MIN_WEEKS:
        out["reason"] = "short_history"
    elif len(xs) < MIN_WEEKS or len(xs) * WEEKS < possible * FULL_MIN_WEEKS:
        out["reason"] = "not_enough_weeks"
    if out["reason"]:
        return out

    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx == 0:
        out["reason"] = "not_enough_weeks"
        return out
    out["beta"] = sxy / sxx
    out["r_squared"] = sxy * sxy / (sxx * syy) if syy else None
    return out


def calendar() -> dict:
    """{"ends": SPY's last 105 finished week-end dates, "spy": its closes on them}."""
    spy = {str(r["date"])[:10]: float(r["close"])
           for r in storage.get_prices_history(BENCHMARK, limit=_HISTORY_DAYS)
           if r.get("date") and finite(r.get("close"))}
    ends = week_ends(settled(sorted(spy)))[-(WEEKS + 1):]
    return {"ends": ends, "spy": {d: spy[d] for d in ends}}


def _closes_on(symbol: str, dates) -> dict:
    """{date: close} for the stored days among `dates`: one read per date."""
    db = storage.get_db()
    prices = storage.ticker_ref(symbol, db=db).collection("prices")
    out = {}
    for snap in db.get_all([prices.document(d) for d in dates]):
        row = snap.to_dict() if snap.exists else None
        if row and finite(row.get("close")):
            out[snap.id] = float(row["close"])
    return out


def _first_close(symbol: str) -> str | None:
    prices = storage.ticker_ref(symbol).collection("prices")
    for doc in prices.order_by("date").limit(1).stream():
        return str((doc.to_dict() or {}).get("date") or doc.id)[:10]
    return None


def fit(symbol: str, cal: dict) -> dict:
    """This week's fit for one company, from its stored closes (about 107 reads)."""
    import share_counts  # noqa: PLC0415
    return compute(cal["ends"], cal["spy"], _closes_on(symbol, cal["ends"]),
                   _first_close(symbol), share_counts.split_since_refresh(symbol))


# ── reading the table: what the website and the API call ─────────────────────

def _checked(symbol: str, snap) -> dict:
    """One stored fit, checked: the doc must name the company it is filed
    under. Anything else is "unavailable", never served as a value."""
    if not snap.exists:
        return {**dict.fromkeys(FIELDS), "reason": "not_yet_computed"}
    held = snap.to_dict() or {}
    if snap.id != symbol or held.get("symbol") != symbol:
        logger.warning("beta: %s/%s holds a fit for %r; not served", STORED, snap.id, held.get("symbol"))
        return dict(UNAVAILABLE)
    return {k: held.get(k) for k in FIELDS}


def for_symbols(symbols, metas: dict) -> dict:
    """{symbol: stored fit} for a set of companies, in one batched read
    (Kilby's metrics call: up to 250). `metas` holds the company record the
    caller already read for each symbol; one with no record reads all None and
    costs no read. A covered company the weekly run has not reached reads
    "not_yet_computed". Never raises: if the read fails, every row reads
    "unavailable", which the API flags with the beta_unavailable warning
    (agreed with Kilby 31 Oct 2026), so a failed read never looks like "this
    stock has no beta"."""
    out = {s: dict.fromkeys(FIELDS) for s in symbols}
    wanted = [s for s in out if metas.get(s)]
    if not wanted:
        return out
    try:
        db = storage.get_db()
        snaps = {snap.id: snap for snap in db.get_all([db.collection(STORED).document(s) for s in wanted])}
        out.update({s: _checked(s, snaps[s]) for s in wanted})
    except Exception as exc:  # noqa: BLE001
        logger.warning("beta unavailable for %d companies: %s", len(wanted), exc)
        out.update({s: dict(UNAVAILABLE) for s in wanted})
    return out


def for_symbol(symbol: str, meta: dict | None = None) -> dict:
    """One company's stored fit (the website, the summary): one read, and the
    company record first when the caller does not hold it. Never raises."""
    if meta is None:
        try:
            meta = storage.get_ticker_meta(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: beta unavailable: %s", symbol, exc)
            return dict(UNAVAILABLE)
    return for_symbols([symbol], {symbol: meta})[symbol]


def industry_standin(industry: str | None, window_end: str | None = None) -> dict:
    """The industry's typical beta, for a portfolio sum when a stock has none of
    its own (Kilby, agreed 31 Oct 2026; owner's go): the median of TEK2day's own
    betas of the active companies in that industry, from the same weekly run,
    with how many went into it. An estimate: never shown as the stock's beta.

    One stored document, cached until the next price run. A row from a newer
    week than the medians (the weekly run is part way through) re-reads it, at
    most once a minute, and until it catches up reads "unavailable": never last
    week's medians beside this week's betas. Unreadable: "unavailable" too.
    Never raises."""
    out = {"industry_beta_median": None, "industry_beta_peers": 0,
           "industry_beta_industry": industry or None, "industry_beta_reason": None}
    if not industry:
        return out
    try:
        table, table_end = _industries(window_end)
    except Exception as exc:  # noqa: BLE001
        logger.warning("industry betas unavailable: %s", exc)
        table, table_end = None, None
    if table is None or (window_end and window_end > (table_end or "")):
        return {**out, "industry_beta_peers": None, "industry_beta_reason": "unavailable"}
    held = table.get(industry) or {}
    if held.get("peers"):
        out.update(industry_beta_median=held.get("median"), industry_beta_peers=held["peers"])
    return out


def _industries(window_end: str | None):
    now = datetime.now(timezone.utc)
    with _LOCK:
        held = dict(_INDUSTRY_CACHE)
    if held and held["expires"] > now and (
            not window_end or window_end <= (held["window_end"] or "")
            or now - held["checked"] < timedelta(seconds=_RECHECK_SECONDS)):
        return held["industries"], held["window_end"]
    snap = storage.get_db().document(INDUSTRIES).get()
    doc = (snap.to_dict() or {}) if snap.exists else {}
    entry = {"expires": expires_at(), "checked": now, "window_end": doc.get("window_end"),
             "industries": doc.get("industries") or {}}
    with _LOCK:
        _INDUSTRY_CACHE.clear()
        _INDUSTRY_CACHE.update(entry)
    return entry["industries"], entry["window_end"]


# ── the weekly run (weekly_beta.py, Cloud Run job weekly-beta) ───────────────

def _same(a: dict, b: dict) -> bool:
    return {k: v for k, v in a.items() if k != "computed_at"} == {k: v for k, v in b.items() if k != "computed_at"}


def _write(db, symbol: str, held: dict) -> str:
    """The week's fit, added to the company's history and set as its current
    doc in one batch, so the two never disagree. History is created, never
    overwritten: if this week's is already there (a rerun after a partial
    failure, or --force) and matches, nothing is written ("held"); if it
    differs, this raises, because a week worked out again must come out the
    same. Company records are never written."""
    current = db.collection(STORED).document(symbol)
    history = current.collection(HISTORY).document(held["week_ending"])
    batch = db.batch()
    batch.create(history, held)
    batch.set(current, held)
    try:
        batch.commit()
        return "written"
    except AlreadyExists:
        if _same(history.get().to_dict() or {}, held):
            return "held"
        raise RuntimeError(f"history for the week ending {held['week_ending']} differs") from None


def run(force: bool = False, symbols=None) -> dict:
    """Work out every active company's beta for the newest finished week and
    store it, with each industry's median.

    Runs every weekday evening after the price run. First reads SPY alone: if
    the newest finished week is not newer than the stored table's (Friday's
    close not in yet, or already done), it stops there, unless the week's run
    left some companies out (at most 2%): then it works out just those, so
    none waits a week. `symbols` limits the run to those companies and leaves
    the industry table alone (a trial).

    More than 2% failing makes the run incomplete: the industry table keeps the
    previous week's medians (readers then serve no stand-in for this week's
    rows), is marked incomplete so the next evening runs the week again, and
    the job fails. A complete week's medians are also kept, never overwritten,
    in beta_industries/latest/history/{week ending}."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    db = storage.get_db()
    cal = calendar()
    window_end = cal["ends"][-1] if cal["ends"] else None
    if not window_end:
        raise RuntimeError("beta: no finished week in SPY's stored prices")
    state = db.document(INDUSTRIES).get()
    state = (state.to_dict() or {}) if state.exists else {}
    retry = []
    if symbols is None and not force and state.get("complete") and state.get("window_end") == window_end:
        retry = sorted(state.get("failed_symbols") or [])
        if not retry:
            logger.info("beta: the table already covers the week ending %s; nothing to do", window_end)
            return {"skipped": True, "window_end": window_end}
        logger.info("beta: the week ending %s left %d companies out; working out those", window_end, len(retry))

    if symbols is None and not retry:
        metas = {doc.id: doc.to_dict() or {}
                 for doc in db.collection(storage.COLLECTION_ROOT).where("active", "==", True).stream()}
    else:
        metas = {s: storage.get_ticker_meta(s) or {} for s in (symbols if symbols is not None else retry)}
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def one(symbol):
        try:
            held = {"symbol": symbol, **fit(symbol, cal), "method": METHOD,
                    "week_ending": window_end, "computed_at": computed_at}
            return symbol, held, _write(db, symbol, held), None
        except Exception as exc:  # noqa: BLE001
            return symbol, None, None, f"{type(exc).__name__}: {str(exc)[:160]}"

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(one, sorted(metas)))
    failed = {s: e for s, _, _, e in results if e}
    done = {s: h for s, h, _, _ in results if h}
    for symbol, error in list(failed.items())[:20]:
        logger.warning("beta: %s failed: %s", symbol, error)
    summary = {"window_end": window_end, "companies": len(metas),
               "written": sum(1 for _, _, how, _ in results if how == "written"),
               "held": sum(1 for _, _, how, _ in results if how == "held"),
               "with_beta": sum(1 for h in done.values() if h["beta"] is not None), "failed": len(failed)}
    if symbols is not None:
        logger.info("beta trial: %s", summary)
        return {**summary, "results": done, "errors": failed}
    if retry:
        db.document(INDUSTRIES).set({"failed_symbols": sorted(failed), "failed": len(failed),
                                     "retried_at": computed_at}, merge=True)
        logger.info("beta: retried %s", summary)
        return {**summary, "retried": len(retry)}

    counts = {k: v for k, v in summary.items() if k != "window_end"}
    if len(failed) > _STOP_FAILED_SHARE * max(len(metas), 1):
        db.document(INDUSTRIES).set({
            **state, "complete": False,
            "attempt": {"window_end": window_end, "computed_at": computed_at, **counts},
        })
        raise RuntimeError(f"beta: {len(failed)} of {len(metas)} companies failed; the table keeps "
                           f"the week ending {state.get('window_end')} and is marked incomplete")

    peers = {}
    for symbol, held in done.items():
        industry = metas[symbol].get("industry")
        if industry and held["beta"] is not None:
            peers.setdefault(industry, []).append(held["beta"])
    week = {"window_end": window_end, "computed_at": computed_at, "method": METHOD,
            "industries": {name: {"median": statistics.median(b), "peers": len(b)}
                           for name, b in sorted(peers.items())}}
    latest = {**week, "complete": True, **counts, "failed_symbols": sorted(failed)}
    history = db.document(f"{INDUSTRIES}/{HISTORY}/{window_end}")
    batch = db.batch()
    batch.create(history, week)
    batch.set(db.document(INDUSTRIES), latest)
    try:
        batch.commit()
    except AlreadyExists:
        if not _same(history.get().to_dict() or {}, week):
            raise RuntimeError(f"beta: industry medians for the week ending {window_end} differ "
                               "from the ones kept") from None
        db.document(INDUSTRIES).set(latest)
    logger.info("beta: %s, %d industries", summary, len(peers))
    return summary
