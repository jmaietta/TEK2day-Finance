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

Stored prices only: no Yahoo request, ever. A symbol costs about 107 Firestore
reads (one per week-end date, its first stored date, its split record), cached
until the next price run like the price feed's copy.
"""
from __future__ import annotations

import logging
import statistics
import threading
from collections import OrderedDict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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

_EASTERN = ZoneInfo("America/New_York")
_HISTORY_DAYS = 560          # SPY rows read: 105 weeks of trading days and the week in progress
_RUN_START = (16, 30)        # ET, Mon-Fri, as partner_api._price_window
_RUN_SETTLED = (19, 0)
_IN_RUN_SECONDS = 300
_CACHE_SYMBOLS = 5000        # a few hundred bytes each
_CACHE: "OrderedDict[str, tuple[datetime, dict]]" = OrderedDict()
_CALENDAR: dict = {}
_LOCK = threading.Lock()
_INDUSTRY: dict = {}             # industry -> (expires, {symbol: beta})
_INDUSTRY_LOCKS: dict = {}
_PEER_WORKERS = 16


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


def _calendar() -> dict:
    """{"ends": SPY's last 105 finished week-end dates, "spy": its closes on them}."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        if _CALENDAR and _CALENDAR["expires"] > now:
            return _CALENDAR
    spy = {str(r["date"])[:10]: float(r["close"])
           for r in storage.get_prices_history(BENCHMARK, limit=_HISTORY_DAYS)
           if r.get("date") and finite(r.get("close"))}
    ends = week_ends(settled(sorted(spy)))[-(WEEKS + 1):]
    entry = {"expires": expires_at(), "ends": ends, "spy": {d: spy[d] for d in ends}}
    with _LOCK:
        _CALENDAR.clear()
        _CALENDAR.update(entry)
    return entry


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


def for_symbol(symbol: str) -> dict:
    """{beta, r_squared, weeks_used, reason, window_start, window_end}. Never
    raises: unreadable prices give every field None and are not cached."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        held = _CACHE.get(symbol)
        if held and held[0] > now:
            _CACHE.move_to_end(symbol)
            return dict(held[1])
    try:
        import share_counts  # noqa: PLC0415
        cal = _calendar()
        split = share_counts.split_since_refresh(symbol)
        result = compute(cal["ends"], cal["spy"], _closes_on(symbol, cal["ends"]),
                         _first_close(symbol), split)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: beta unavailable: %s", symbol, exc)
        return dict.fromkeys(FIELDS)
    with _LOCK:
        _CACHE[symbol] = (min(cal["expires"], expires_at()), result)
        _CACHE.move_to_end(symbol)
        while len(_CACHE) > _CACHE_SYMBOLS:
            _CACHE.popitem(last=False)
    return dict(result)


def _industry_betas(industry: str) -> dict:
    """{symbol: beta} for the active companies in `industry` that have one,
    cached until the next price run. One build per industry at a time."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        held = _INDUSTRY.get(industry)
        if held and held[0] > now:
            return held[1]
        lock = _INDUSTRY_LOCKS.setdefault(industry, threading.Lock())
    with lock:
        with _LOCK:
            held = _INDUSTRY.get(industry)
            if held and held[0] > datetime.now(timezone.utc):
                return held[1]
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
        members = [doc.id for doc in storage.get_db().collection(storage.COLLECTION_ROOT)
                   .where("industry", "==", industry).stream()
                   if (doc.to_dict() or {}).get("active") is not False]
        with ThreadPoolExecutor(max_workers=_PEER_WORKERS) as pool:
            fits = dict(zip(members, pool.map(for_symbol, members)))
        betas = {s: f["beta"] for s, f in fits.items() if f["beta"] is not None}
        with _LOCK:
            _INDUSTRY[industry] = (expires_at(), betas)
        return betas


def industry_standin(industry: str | None, symbol: str) -> dict:
    """The industry's typical beta, for a portfolio sum when `symbol` has none
    of its own (Kilby, agreed 31 Oct 2026; owner's go). The median of TEK2day's
    own betas of the other active companies in the same industry, same method
    and same week, with how many went into it so the reader can refuse too few.
    An estimate: never shown as the stock's beta. Never raises."""
    out = {"industry_beta_median": None, "industry_beta_peers": 0, "industry_beta_industry": industry or None}
    if not industry:
        return out
    try:
        peers = [b for s, b in _industry_betas(industry).items() if s != symbol]
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s: industry beta unavailable: %s", industry, exc)
        return out
    if peers:
        out.update(industry_beta_median=statistics.median(peers), industry_beta_peers=len(peers))
    return out
