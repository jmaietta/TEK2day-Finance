"""TEK2day's realized volatility: one year of daily log returns, annualized.

Agreed with Kilby 31 Oct 2026; owner's go the same day ("I approve the
volatility design"). Kilby's watchlist shows it beside Beta and R-squared, and
its chat works the same figure out from /equities/prices, so the two are one
definition: Kilby's price_statistics.volatility(), window 252.

THE METHOD, reproducible in Excel from /partner/v1/equities/prices?days=253:
  - The calendar is SPY's stored dates (the feed's trading_days); a date
    counts once the price run has settled (7 PM ET), as beta's.
  - The window is SPY's last 253 dates: 252 daily return slots.
  - A slot's return is LN(close / previous close), counted only when the
    stock has a positive close on both of those SPY dates. Nothing is filled
    in, and a close on a date SPY has no close for is not used.
  - Volatility = STDEV.S(returns) x SQRT(252): the sample standard deviation
    (n - 1), as a fraction (0.314 is 31.4%).
  - Null, with the reason, in this order: split_since_refresh (older closes
    on the pre-split basis until Thursday's 5-year re-adjust), short_history
    (no stored close on or before window_start: under a year of prices),
    not_enough_days (fewer than 200 of the 252 returns).

STORED, KEPT UP TO DATE FROM THE NEW PRICES (owner, 31 Oct 2026: "Why
wouldn't we just run the calc on the new prices?"). The daily-volatility job
(daily_volatility.py, every weekday 8:30 PM ET) keeps each active company's
252 returns in its volatility_daily/{symbol} doc. Each evening it reads only
the closes that can have changed: from the oldest date any price write since
the last run touched (volatility_dirty/{symbol}, which storage's price
writers set after every write) or five SPY dates before the stored window
end, whichever is older. Thursday's 5-year re-adjust rewrites every close,
so on Thursday evenings every window is read again in full. A changed price
path (a ticker migration), a price repair, a SPY calendar change, a gap of
more than 20 SPY dates or a failed mark also reads the window again in full.
Every distinct figure is kept, never overwritten, in
volatility_daily/{symbol}/history/{window_end}-{version}, with the time it
began serving. Stored prices only: no Yahoo request, ever.
"""
from __future__ import annotations

import logging
import math
import statistics
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists, FailedPrecondition, NotFound

import storage
from beta import settled
from envelope import finite

logger = logging.getLogger("ydp.volatility")

BENCHMARK = "SPY"
METHOD = "daily_log_close_to_close_252"
DAYS = 252
MIN_DAYS = 200               # of 252: below this, blank (Kilby's chat uses the same)
FIELDS = ("volatility", "days_used", "reason", "window_start", "window_end")
UNAVAILABLE = {**dict.fromkeys(FIELDS), "reason": "unavailable"}

STORED = "volatility_daily"            # volatility_daily/{symbol}: the figure and its 252 returns
HISTORY = "history"                    # .../history/{window_end}-{version}: every distinct figure
DIRTY = "volatility_dirty"             # volatility_dirty/{symbol}: {since: yyyymmdd, marked_at}
FAILED_MARKS = "volatility_meta/failed_marks"     # {symbols}: marks that could not be written
REPAIRS_SEEN = "volatility_meta/price_repairs"    # {seen: {audit id: status}}
LATEST = "volatility_meta/latest"                 # the last run's summary

_SPY_ROWS = 300              # SPY rows read: 253 settled dates and the day in progress
_REWRITTEN = 5               # SPY dates every nightly price run rewrites
_MAX_GAP = 20                # more SPY dates than this since the stored window: read it all
_WORKERS = 32                # the run: Firestore round trips, not work
_STOP_FAILED_SHARE = 0.02    # more than 2% of companies failing: the job fails
_READ_MASK = ["symbol", *FIELDS]


def _good(close) -> bool:
    return finite(close) and close > 0


def returns(dates, closes: dict) -> dict:
    """{date: ln(close / previous close)} for each SPY date after the first of
    `dates`, None where this stock lacks either close."""
    return {b: (math.log(closes[b] / closes[a]) if _good(closes.get(a)) and _good(closes.get(b)) else None)
            for a, b in zip(dates, dates[1:])}


def compute(window, rets: dict, first_close: str | None, split: str | None = None) -> dict:
    """The figure for `window` (SPY's last 253 dates) from the stock's returns."""
    out = dict.fromkeys(FIELDS)
    out["window_start"], out["window_end"] = window[0], window[-1]
    used = [r for r in (rets.get(d) for d in window[1:]) if r is not None]
    out["days_used"] = len(used)
    if split:
        out["reason"] = "split_since_refresh"
    elif not first_close or first_close > window[0]:
        out["reason"] = "short_history"
    elif len(used) < MIN_DAYS:
        out["reason"] = "not_enough_days"
    else:
        out["volatility"] = statistics.stdev(used) * math.sqrt(DAYS)
    return out


def calendar() -> list[str]:
    """SPY's last 253 settled dates, oldest first."""
    dates = sorted({str(r["date"])[:10] for r in storage.get_prices_history(BENCHMARK, limit=_SPY_ROWS)
                    if r.get("date") and _good(r.get("close"))})
    window = settled(dates)[-(DAYS + 1):]
    if len(window) < DAYS + 1:
        raise RuntimeError(f"volatility: SPY has {len(window)} settled dates; {DAYS + 1} needed")
    return window


# ── the mark every price write leaves (storage.write_prices_batch, write_price) ─

def mark(symbol: str, oldest: str) -> bool:
    """Record that `symbol`'s closes from `oldest` on may have changed, so the
    evening run reads them again. Never raises: a price write never fails
    because of this. A mark that cannot be written is recorded in
    volatility_meta/failed_marks, which makes that evening's run read the
    company again in full and then fail, so the job's alert names it."""
    try:
        from google.cloud import firestore  # noqa: PLC0415
        since = int(str(oldest)[:10].replace("-", ""))
        storage.get_db().collection(DIRTY).document(symbol).set(
            {"symbol": symbol, "since": firestore.Minimum(since), "marked_at": firestore.SERVER_TIMESTAMP},
            merge=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("volatility: %s: mark from %s not written: %s", symbol, oldest, exc)
    try:
        from google.cloud import firestore  # noqa: PLC0415
        storage.get_db().document(FAILED_MARKS).set(
            {"symbols": firestore.ArrayUnion([symbol]), "updated_at": firestore.SERVER_TIMESTAMP}, merge=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("volatility: %s: failed mark not recorded either: %s", symbol, exc)
    return False


# ── reading the table: what the API calls ───────────────────────────────────

def _checked(symbol: str, snap) -> dict:
    """One stored figure, checked: the doc must name the company it is filed
    under. Anything else is "unavailable", never served as a value."""
    if not snap.exists:
        return {**dict.fromkeys(FIELDS), "reason": "not_yet_computed"}
    held = snap.to_dict() or {}
    if snap.id != symbol or held.get("symbol") != symbol:
        logger.warning("volatility: %s/%s holds a figure for %r; not served", STORED, snap.id, held.get("symbol"))
        return dict(UNAVAILABLE)
    return {k: held.get(k) for k in FIELDS}


def for_symbols(symbols, metas: dict) -> dict:
    """{symbol: stored figure} for a set of companies, in one batched read of
    the figures alone (never the 252 returns behind them). The same rules as
    beta.for_symbols: no record or an inactive one reads all None and costs no
    read; a covered company the run has not reached reads "not_yet_computed";
    a failed read makes every row "unavailable" (the volatility_unavailable
    warning). Never raises."""
    out = {s: dict.fromkeys(FIELDS) for s in symbols}
    wanted = [s for s in out if metas.get(s) and not (isinstance(metas[s], dict) and metas[s].get("active") is False)]
    if not wanted:
        return out
    try:
        db = storage.get_db()
        snaps = {snap.id: snap for snap in
                 db.get_all([db.collection(STORED).document(s) for s in wanted], field_paths=_READ_MASK)}
        out.update({s: _checked(s, snaps[s]) for s in wanted})
    except Exception as exc:  # noqa: BLE001
        logger.warning("volatility unavailable for %d companies: %s", len(wanted), exc)
        out.update({s: dict(UNAVAILABLE) for s in wanted})
    return out


# ── the evening run (daily_volatility.py, Cloud Run job daily-volatility) ────

def _closes_from(ref, start: str) -> dict:
    """{date: close} for every stored close dated `start` or later."""
    from google.cloud.firestore_v1.base_query import FieldFilter  # noqa: PLC0415
    out = {}
    for doc in ref.collection("prices").where(filter=FieldFilter("date", ">=", start)).stream():
        row = doc.to_dict() or {}
        if finite(row.get("close")):
            out[str(row.get("date") or doc.id)[:10]] = float(row["close"])
    return out


def _first_close(ref) -> str | None:
    for doc in ref.collection("prices").order_by("date").limit(1).stream():
        return str((doc.to_dict() or {}).get("date") or doc.id)[:10]
    return None


def _since(marker) -> str | None:
    value = (marker or {}).get("since")
    if not isinstance(value, int):
        return None
    text = f"{value:08d}"
    return f"{text[:4]}-{text[4:6]}-{text[6:]}"


def plan(window, held: dict | None, path: str, since: str | None, full: str | None = None):
    """(reason to read the whole window, or None; the first return slot to
    recompute). The returns kept for slots before that one are reused."""
    if full:
        return full, None
    if not held or held.get("method") != METHOD or not isinstance(held.get("returns"), dict):
        return "new", None
    if held.get("price_path") != path:
        return "route", None
    old_end, kept = held.get("window_end"), held["returns"]
    if old_end not in window:
        return ("gap" if (old_end or "") < window[0] else "calendar"), None
    i = window.index(old_end)
    if len(window) - 1 - i > _MAX_GAP:
        return "gap", None
    # The kept returns must sit on exactly this calendar's dates: a SPY date
    # added, removed or moved means every window is read again.
    if {d for d in kept if window[1] <= d <= old_end} != set(window[1:i + 1]):
        return "calendar", None
    start = window[max(i - _REWRITTEN, 1)]
    if since and since < start:
        start = since
    if start <= window[1]:
        return ("since" if since and since <= window[1] else "gap"), None
    return None, start


def update(window, held: dict | None, ref, since: str | None, split: str | None, full: str | None = None):
    """(the new current doc's computed part, how it was read, whether the
    whole window was read)."""
    why, start = plan(window, held, ref.path, since, full)
    if why:
        closes = _closes_from(ref, window[0])
        rets = returns(window, closes)
        first = _first_close(ref)
        rebuilt = True
    else:
        j = next(k for k, d in enumerate(window) if d >= start)
        closes = _closes_from(ref, window[j - 1])
        fresh = returns(window[j - 1:], closes)
        rets = {d: (fresh[d] if d >= window[j] else held["returns"].get(d)) for d in window[1:]}
        first = held.get("first_close") or (min(closes) if closes else None)
        rebuilt = False
    figure = compute(window, rets, first, split)
    return {**figure, "returns": rets, "first_close": first, "price_path": ref.path}, (why or "update"), rebuilt


def _write(db, symbol: str, held: dict | None, new: dict, computed_at: str) -> str:
    """Set the current doc and, when the figure is new, add it to history in
    the same batch. An identical figure for the same window keeps its version
    and the time it began serving ("held")."""
    current = db.collection(STORED).document(symbol)
    figure = {"symbol": symbol, **{k: new[k] for k in FIELDS}, "method": METHOD}
    held = held or {}
    same_window = held.get("window_end") == new["window_end"]
    if same_window and {k: held.get(k) for k in figure} == figure:
        doc = {**held, **new, **figure, "version": held.get("version"), "computed_at": held.get("computed_at")}
        if doc != held:
            current.set(doc)
        return "held"
    version = (held.get("version") or 0) + 1 if same_window else 1
    doc = {**new, **figure, "version": version, "computed_at": computed_at}
    history = current.collection(HISTORY).document(f"{new['window_end']}-{version}")
    batch = db.batch()
    batch.create(history, {**figure, "version": version, "computed_at": computed_at})
    batch.set(current, doc)
    try:
        batch.commit()
    except AlreadyExists:
        kept = history.get().to_dict() or {}
        if {k: kept.get(k) for k in figure} != figure:
            raise RuntimeError(f"history {new['window_end']}-{version} holds a different figure") from None
        current.set({**doc, "computed_at": kept.get("computed_at")})
    return "written"


def _clear(db, snap) -> None:
    """Delete a mark the run has acted on, only if nothing marked it since."""
    try:
        snap.reference.delete(option=db.write_option(last_update_time=snap.update_time))
    except (FailedPrecondition, NotFound):
        pass


def _repaired(db) -> tuple[set, dict]:
    """Symbols whose price repair audit (price_repair.py) is new or changed
    status since the last run, and the statuses to remember."""
    seen_snap = db.document(REPAIRS_SEEN).get()
    seen = ((seen_snap.to_dict() or {}) if seen_snap.exists else {}).get("seen") or {}
    now, symbols = {}, set()
    for doc in db.collection("price_repairs").select(["status", "writes"]).stream():
        audit = doc.to_dict() or {}
        now[doc.id] = audit.get("status")
        if seen.get(doc.id) != now[doc.id]:
            symbols.update((w.get("data") or {}).get("symbol") for w in audit.get("writes") or [])
    return {s for s in symbols if s}, now


def run(symbols=None, full: bool = False) -> dict:
    """Bring every active company's volatility up to SPY's newest settled date.

    `symbols` limits the run to those companies (a trial); `full` reads every
    window again. More than 2% of companies failing fails the job; their docs
    keep the figure they had, with its own window_end. Marks that could not
    be written (volatility_meta/failed_marks) and a SPY calendar change are
    repaired by a full read and then fail the job too, so its alert says so."""
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415
    import share_counts  # noqa: PLC0415

    db = storage.get_db()
    window = calendar()
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    failed_snap = db.document(FAILED_MARKS).get()
    lost = set(((failed_snap.to_dict() or {}) if failed_snap.exists else {}).get("symbols") or [])
    repaired, repairs_now = _repaired(db)
    markers = {snap.id: snap for snap in db.collection(DIRTY).stream()}
    companies = sorted(symbols if symbols is not None else storage.list_active_tickers())
    held = {}
    for at in range(0, len(companies), 500):
        refs = [db.collection(STORED).document(s) for s in companies[at:at + 500]]
        held.update({snap.id: (snap.to_dict() if snap.exists else None) for snap in db.get_all(refs)})

    def one(symbol):
        try:
            marker = markers.get(symbol)
            reason = ("full" if full else "failed_mark" if symbol in lost else "price_repair" if symbol in repaired
                      else None)
            new, how, rebuilt = update(window, held.get(symbol), storage.ticker_ref(symbol, db=db),
                                       _since(marker.to_dict() if marker else None),
                                       share_counts.split_since_refresh(symbol), reason)
            if rebuilt:
                new["full_rebuild_at"] = computed_at
            else:
                new["full_rebuild_at"] = (held.get(symbol) or {}).get("full_rebuild_at")
            outcome = _write(db, symbol, held.get(symbol), new, computed_at)
            if marker is not None:
                _clear(db, marker)
            return symbol, new, how, outcome, None
        except Exception as exc:  # noqa: BLE001
            return symbol, None, None, None, f"{type(exc).__name__}: {str(exc)[:160]}"

    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        results = list(pool.map(one, companies))
    failed = {s: e for s, _, _, _, e in results if e}
    for symbol, error in list(failed.items())[:20]:
        logger.warning("volatility: %s failed: %s", symbol, error)
    reads = {}
    for _, _, how, _, _ in results:
        if how:
            reads[how] = reads.get(how, 0) + 1
    summary = {"window_end": window[-1], "companies": len(companies), "failed": len(failed),
               "written": sum(1 for r in results if r[3] == "written"),
               "held": sum(1 for r in results if r[3] == "held"),
               "with_volatility": sum(1 for r in results if r[1] and r[1]["volatility"] is not None),
               "read_as": reads}
    if symbols is not None:
        logger.info("volatility trial: %s", summary)
        return {**summary, "results": {s: n for s, n, _, _, _ in results if n}, "errors": failed}

    for symbol in markers.keys() - set(companies):      # no longer active: nothing to update
        _clear(db, markers[symbol])
    alerts = []
    if lost:
        alerts.append(f"marks were lost for {sorted(lost)}; read again in full")
        if not failed.keys() & lost:
            _clear(db, failed_snap)
    if repairs_now and not failed.keys() & repaired:
        db.document(REPAIRS_SEEN).set({"seen": repairs_now})
    if reads.get("calendar"):
        alerts.append(f"SPY's calendar changed: {reads['calendar']} windows read again in full")
    db.document(LATEST).set({**summary, "computed_at": computed_at, "failed_symbols": sorted(failed),
                             "alerts": alerts})
    if len(failed) > _STOP_FAILED_SHARE * max(len(companies), 1):
        raise RuntimeError(f"volatility: {len(failed)} of {len(companies)} companies failed")
    if alerts:
        raise RuntimeError("volatility: done, but " + "; ".join(alerts))
    logger.info("volatility: %s", summary)
    return summary
