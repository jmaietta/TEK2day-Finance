"""One checked share count per company, with SEC filings as the source of truth.

WHY (issue #16). Market cap is the price times the latest quarter's share
count, and P/E, P/S and every EV ratio follow from it; each quarter's EPS is net
income over that quarter's count. Measured 29 Oct 2026: of 6,734 companies, 622
had a market cap more than 1.5x from Yahoo's own. Causes seen: bad numbers in
the statements (TTD 4.7M shares instead of 470M; FirstEnergy 2,316M instead of
578M), foreign companies switching between New York and local shares (KOF,
HDB), splits, and shares issued since the last report.

THE OWNER'S RULES (29 Oct 2026).
- "The source of truth in my view should be the SEC filing." No review list:
  where nothing settles a count, today's figure stays.
- Companies with several share classes follow the SEC count, one class
  (IBKR: 450,088,032 in its 10-Q, as Bloomberg and Morningstar show), not
  Yahoo's all-units count.

THE ONE SAFEGUARD. SEC wins, except when the company's own statement and
Yahoo's market cap agree with each other and SEC disagrees with both. That is
SEC's own typo (McDonald's filed as 711.1 shares) or a number that does not
mean the same thing as the ticker (a count before a split, Class A
equivalents). Two witnesses against one; nobody has to review it.

THE SEC LIMIT. Kilby uses SEC too, so it must never be throttled. SEC's
"frames" files hold one quarter for every US filer, so a week needs 4 requests
(the two newest quarters, two share-count tags), 16 the first time. Older
quarters are kept and never fetched again. One request a second, at most
MAX_SEC_REQUESTS a run, and the first refusal stops all SEC requests until
next week.

SPLITS. SEC counts are as filed, before any later split; the weekly 5-year
price download carries every split in its "Stock Splits" column (zero extra
Yahoo calls). save_splits keeps them; a count is multiplied by the ratio of
every split after its quarter ended.

UNLESS SEC ALREADY DID. SEC's file holds the LATEST filing's figure for a
quarter, and a 10-Q filed after a split restates the year-ago quarter on the
new basis. Multiplying that again counted the split twice: IBKR's March 2025
quarter, restated after its 4-for-1 split in June 2025, came out at 1,758M
shares beside 450M either side (found 29 Oct 2026, 11 companies). So a count
takes the ratio only when that lands nearer the company's count today than the
figure as filed does (on_todays_basis).

WHERE IT LIVES. share_counts/{TICKER} holds the corrected quarters. The
statements are never changed (financials are write-once). apply() overlays the
corrections when SHARE_COUNTS_READ is on; until then nothing a reader sees
changes (watch-only).
"""
from __future__ import annotations

import logging
import math
import os
import time
from copy import deepcopy
from datetime import date, datetime, timezone

logger = logging.getLogger("ydp.share_counts")

COLLECTION = "share_counts"
SPLITS = "splits"
FRAMES = "sec_share_frames"
TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding",
        "WeightedAverageNumberOfShareOutstandingBasicAndDiluted")
QUARTERS = 8               # SEC quarters kept; the newest REFRESH are re-read weekly
REFRESH = 2                # the newest quarters keep filling for about two months
MAX_SEC_REQUESTS = 20
SEC_PAUSE = 1.0            # seconds between SEC requests (SEC allows 10 a second)
SEC_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/shares/{period}.json"
USER_AGENT = "TEK2day Finance support@tek2day.com"
MATCH_DAYS = 20            # an SEC quarter belongs to a stored quarter ending within this
EARLIER_DAYS = 190         # the latest quarter may lean on SEC's previous one, no older
SEC_AGREES = 0.10          # SEC and the statement within 10% agree
YAHOO_AGREES = 0.25        # Yahoo's count is a week old and rounded: 25%
WHOLE = (2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 40, 50, 100)
UNIT_SLIPS = (1e3, 1e6, 1e-3, 1e-6)
LONG_PERIODS = {"5y", "10y", "max"}   # a download this long holds every recent split


def read_enabled() -> bool:
    """Whether readers use the corrections. Off until the owner switches it on."""
    return os.environ.get("SHARE_COUNTS_READ", "").strip().lower() in {"1", "on", "true", "yes"}


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) and value > 0 else None


def _near(a, b, tolerance):
    return bool(a and b) and abs(a / b - 1) <= tolerance


def _days(a, b):
    return abs((date.fromisoformat(str(a)[:10]) - date.fromisoformat(str(b)[:10])).days)


# ── Pure rules ──────────────────────────────────────────────────────────────


def calendar_quarters(today: date, count: int | None = None) -> list[str]:
    """SEC frame names of the last `count` finished calendar quarters, newest first."""
    count = count or QUARTERS
    year, quarter = today.year, (today.month - 1) // 3   # the quarter before today's
    out = []
    while len(out) < count:
        if quarter == 0:
            year, quarter = year - 1, 4
        out.append(f"CY{year}Q{quarter}")
        quarter -= 1
    return out


def whole_multiple(ratio):
    """2, 8, 1/2 ... when `ratio` is within 8% of a whole multiple, else None."""
    if not ratio:
        return None
    for k in WHOLE:
        if abs(ratio / k - 1) <= 0.08:
            return float(k)
        if abs(ratio * k - 1) <= 0.08:
            return 1 / k
    return None


def split_factor(splits: dict | None, period_end: str) -> float:
    """Product of every split ratio dated after the quarter ended (4.0 for 4-for-1)."""
    factor = 1.0
    for when, ratio in (splits or {}).items():
        ratio = _num(ratio)
        if ratio and str(when)[:10] > str(period_end)[:10]:
            factor *= ratio
    return factor


def on_todays_basis(filed, factor, anchor):
    """(count, ratio applied): `filed` times the split `factor`, unless SEC restated it already.

    A split moves a count by its whole ratio, so of the two readings the one
    nearer `anchor` (the company's count today) is right. With no anchor the
    ratio is applied: a count filed before a split is the usual case.
    """
    if factor == 1.0 or not anchor or not filed:
        return filed * factor, factor
    if abs(math.log(filed / anchor)) < abs(math.log(filed * factor / anchor)):
        return filed, 1.0
    return filed * factor, factor


def fix_sec_units(value, other_sec, statement):
    """SEC's own unit slip (MCD 711.1 for 711.1 million), corrected; else unchanged.

    Only exact powers of a thousand, and only when the corrected figure lands on
    the company's other SEC quarters or, with none, on the statement.
    """
    others = sorted(v for v in other_sec if v)
    anchor = others[len(others) // 2] if others else statement
    if not anchor or _near(value, anchor, 0.5):
        return value, False
    for slip in UNIT_SLIPS:
        if _near(value * slip, anchor, 0.5 if others else 0.02):
            return value * slip, True
    return value, False


def correct_quarters(quarters, sec, splits, foreign, yahoo_shares):
    """The corrected share count for each quarter that needs one. Pure.

    quarters: [{"period", "period_end", "diluted"}], quarterly only.
    sec: {end_date: shares} for this company's CIK (as filed).
    splits: {date: ratio}, or None when no split record exists yet.
    yahoo_shares: Yahoo's market cap / that day's close, or None.

    Returns {period: {...}} with "shares" set where the count should change.
    """
    out = {}
    matched = {}
    for q in quarters:
        stated = _num(q.get("diluted"))
        hits = sorted((_days(e, q["period_end"]), e, v) for e, v in sec.items()
                      if _days(e, q["period_end"]) <= MATCH_DAYS)
        if hits:
            _, sec_end, filed = hits[0]
            matched[q["period"]] = (sec_end, *fix_sec_units(filed, [v for e, v in sec.items() if e != sec_end],
                                                            stated))
    anchor = None
    if matched and splits is not None:
        # The company's count today: its latest SEC quarter, put on today's
        # basis by Yahoo's own count when a split came after it.
        latest = max((q for q in quarters if q["period"] in matched), key=lambda q: str(q["period_end"]))
        anchor, _ = on_todays_basis(matched[latest["period"]][1], split_factor(splits, latest["period_end"]),
                                    yahoo_shares)
    for q in quarters:
        period, end, stated = q["period"], q["period_end"], _num(q.get("diluted"))
        if period in matched:
            sec_end, filed, unit_fix = matched[period]
            if splits is None:
                out[period] = {"source": "sec", "status": "waiting_for_split_record",
                               "sec_shares": filed, "sec_end": sec_end}
                continue
            shares, factor = on_todays_basis(filed, split_factor(splits, end), anchor)
            entry = {"source": "sec", "sec_shares": filed, "sec_end": sec_end,
                     "split_factor": factor, "sec_units_corrected": unit_fix}
            if (stated and yahoo_shares and _near(stated, yahoo_shares, YAHOO_AGREES)
                    and not _near(shares, stated, SEC_AGREES) and not _near(shares, yahoo_shares, YAHOO_AGREES)):
                # The safeguard: the statement and Yahoo agree, SEC disagrees with both.
                entry.update(status="sec_outvoted", shares=None)
            else:
                entry.update(status="sec", shares=shares)
            out[period] = entry
        elif foreign and stated and yahoo_shares:
            # No SEC quarter (a foreign filer). New York vs local shares: a steady
            # whole multiple of the price's own share count.
            k = whole_multiple(stated / yahoo_shares)
            if k and k != 1:
                out[period] = {"source": "statement_adr", "status": "adr_basis", "multiple": k,
                               "shares": stated / k}
    _latest_from_earlier_sec(quarters, sec, splits, yahoo_shares, out)
    return out


def _latest_from_earlier_sec(quarters, sec, splits, yahoo_shares, out):
    """The latest quarter, when SEC has not published it yet (FirstEnergy, Oct 2026).

    Market cap reads only the latest quarter, so a bad count there cannot wait
    for SEC's file. SEC's previous quarter (within EARLIER_DAYS, put on today's
    basis) is used only when Yahoo confirms it and the statement is far from
    both. Otherwise the latest quarter keeps today's count.
    """
    if not quarters or splits is None or not yahoo_shares:
        return
    latest = max(quarters, key=lambda q: str(q["period_end"]))
    stated = _num(latest.get("diluted"))
    if latest["period"] in out or not stated:
        return
    earlier = sorted((e, v) for e, v in sec.items()
                     if e < str(latest["period_end"])[:10] and _days(e, latest["period_end"]) <= EARLIER_DAYS)
    if not earlier:
        return
    sec_end, filed = earlier[-1]
    filed, unit_fix = fix_sec_units(filed, [v for e, v in sec.items() if e != sec_end], stated)
    shares, factor = on_todays_basis(filed, split_factor(splits, sec_end), yahoo_shares)
    if _near(shares, yahoo_shares, YAHOO_AGREES) and not _near(stated, shares, YAHOO_AGREES):
        out[latest["period"]] = {"source": "sec", "status": "sec_earlier_quarter", "shares": shares,
                                 "sec_shares": filed, "sec_end": sec_end,
                                 "split_factor": factor, "sec_units_corrected": unit_fix}


def apply(financials, record):
    """`financials` with corrected share counts from `record`. Pure; copies.

    Diluted Average Shares takes the corrected count. Basic Average Shares is
    scaled by the same ratio when the correction is a change of basis (more
    than 10%), so basic EPS follows; smaller differences leave it alone.
    """
    quarters = (record or {}).get("quarters") or {}
    if not quarters:
        return financials
    out = []
    for financial in financials or []:
        entry = quarters.get(str(financial.get("period") or ""))
        shares = _num((entry or {}).get("shares"))
        if not shares or financial.get("freq") == "FY":
            out.append(financial)
            continue
        changed = deepcopy(financial)
        income = changed.setdefault("income", {})
        stated = _num(income.get("Diluted Average Shares"))
        income["Diluted Average Shares"] = shares
        basic = _num(income.get("Basic Average Shares"))
        if basic and stated and not _near(shares, stated, SEC_AGREES):
            income["Basic Average Shares"] = basic * shares / stated
        changed["share_count_source"] = entry.get("source")
        out.append(changed)
    return out


# ── Firestore and SEC ───────────────────────────────────────────────────────


def record_for(symbol, db=None):
    """share_counts/{symbol}, or None. Never raises: unreadable reads as today."""
    try:
        if db is None:
            import storage
            db = storage.get_db()
        snap = db.collection(COLLECTION).document(symbol).get()
        return snap.to_dict() if snap.exists else None
    except Exception as exc:
        logger.warning("%s: share count record unreadable, using the statements: %s", symbol, exc)
        return None


def overlay(symbol, financials):
    """What readers call: the corrections when switched on, else `financials` untouched."""
    if not read_enabled() or not financials:
        return financials
    return apply(financials, record_for(symbol))


def save_splits(db, symbol, splits: dict, period: str, today: date | None = None) -> None:
    """Keep the splits from a price download. Never raises.

    A long download (the weekly 5 years, a new ticker's backfill) replaces the
    record, which then also says "no splits in that window". A nightly 5 days
    only adds what it found.
    """
    try:
        today = today or datetime.now(timezone.utc).date()
        events = {str(k)[:10]: float(v) for k, v in (splits or {}).items() if _num(v)}
        if period not in LONG_PERIODS and not events:
            return                      # an ordinary night: nothing to keep
        if db is None:
            import storage
            db = storage.get_db()
        ref = db.collection(SPLITS).document(symbol)
        if period in LONG_PERIODS:
            ref.set({"symbol": symbol, "events": events, "period": period, "checked_at": today.isoformat()})
        elif events:
            ref.set({"symbol": symbol, "events": events}, merge=True)
    except Exception as exc:
        logger.warning("%s: splits not saved: %s", symbol, exc)


def split_since_refresh(symbol: str) -> str | None:
    """Date of a split recorded after the last 5-year refresh, else None.

    The nightly run rewrites only five days, so after a mid-week split the older
    closes stay on the old basis until Thursday: a false 50% move. The price
    feed flags such a name and beta leaves it blank until then.
    """
    import storage
    snap = storage.get_db().collection(SPLITS).document(symbol).get()
    record = (snap.to_dict() or {}) if snap.exists else {}
    refreshed = str(record.get("checked_at") or "")
    later = sorted(d for d in (record.get("events") or {}) if str(d)[:10] > refreshed)
    return later[-1][:10] if later and refreshed else None


def fetch_frames(db, today: date, get=None, sleep=time.sleep) -> dict:
    """{(tag, period): {cik: [end, shares]}} from storage, refreshing what is due.

    Due: a quarter not held yet, or one of the REFRESH newest. Stops at the first
    SEC refusal or at MAX_SEC_REQUESTS; whatever is held is still used.
    """
    if get is None:
        import requests
        get = requests.get
    periods = calendar_quarters(today)
    frames, made, stopped = {}, 0, False
    for tag in TAGS:
        for i, period in enumerate(periods):
            ref = db.collection(FRAMES).document(f"{tag}_{period}")
            snap = ref.get()
            held = snap.to_dict() if snap.exists else None
            if held and i >= REFRESH:
                frames[(tag, period)] = held.get("values") or {}
                continue
            if stopped or made >= MAX_SEC_REQUESTS:
                if held:
                    frames[(tag, period)] = held.get("values") or {}
                continue
            if made:
                sleep(SEC_PAUSE)
            made += 1
            try:
                response = get(SEC_URL.format(tag=tag, period=period),
                               headers={"User-Agent": USER_AGENT}, timeout=60)
            except Exception as exc:
                logger.warning("SEC %s %s: %s; no more SEC requests this run", tag, period, exc)
                stopped = True
                response = None
            if response is not None and response.status_code == 404:
                # Nobody has filed this quarter under this tag yet. Not a refusal.
                frames[(tag, period)] = (held or {}).get("values") or {}
                continue
            if response is None or response.status_code != 200:
                if response is not None:
                    logger.warning("SEC answered %s for %s %s; no more SEC requests this run",
                                   response.status_code, tag, period)
                stopped = True
                if held:
                    frames[(tag, period)] = held.get("values") or {}
                continue
            values = {}
            for row in (response.json() or {}).get("data") or []:
                shares = _num(row.get("val"))
                if shares and row.get("cik") is not None and row.get("end"):
                    values[str(row["cik"]).zfill(10)] = [str(row["end"])[:10], shares]
            ref.set({"tag": tag, "period": period, "values": values,
                     "fetched_at": datetime.now(timezone.utc).isoformat()})
            frames[(tag, period)] = values
    logger.info("Share counts: %d SEC request(s)%s", made, ", stopped early" if stopped else "")
    return frames


def sec_by_company(frames: dict) -> dict:
    """{cik: {end: shares}}, the diluted tag preferred over basic-and-diluted."""
    out = {}
    for tag in reversed(TAGS):          # the preferred tag last, so it overwrites
        for (t, _), values in frames.items():
            if t != tag:
                continue
            for cik, (end, shares) in values.items():
                out.setdefault(cik, {})[end] = shares
    return out


def run(db=None, today: date | None = None, get=None, sleep=time.sleep) -> dict:
    """Rebuild every active company's record. Never raises; never changes a job's result."""
    summary = {"companies": 0, "corrected": 0, "outvoted": 0, "adr": 0, "waiting_for_splits": 0,
               "earlier_quarter": 0}
    try:
        from config import COLLECTION_ROOT
        if db is None:
            import storage
            db = storage.get_db()
        today = today or datetime.now(timezone.utc).date()
        sec = sec_by_company(fetch_frames(db, today, get=get, sleep=sleep))
        metas = {d.id: d.to_dict() or {} for d in db.collection(COLLECTION_ROOT).where("active", "==", True)
                 .select(["cik", "financial_currency", "market_cap", "updated_at", "quote_type"]).stream()}
        def one(item):
            symbol, meta = item
            try:
                return _company(db, symbol, meta, sec)
            except Exception as exc:
                logger.warning("%s: share count not checked: %s", symbol, exc)
                return None

        from concurrent.futures import ThreadPoolExecutor
        equities = [(s, m) for s, m in metas.items() if (m.get("quote_type") or "EQUITY") == "EQUITY"]
        with ThreadPoolExecutor(16) as pool:
            records = [r for r in pool.map(one, equities) if r]
        for record in records:
            summary["companies"] += 1
            for entry in record["quarters"].values():
                status = entry.get("status")
                summary["corrected"] += bool(entry.get("changes_count"))
                summary["outvoted"] += status == "sec_outvoted"
                summary["adr"] += status == "adr_basis"
                summary["waiting_for_splits"] += status == "waiting_for_split_record"
                summary["earlier_quarter"] += status == "sec_earlier_quarter"
    except Exception as exc:
        logger.error("SHARE COUNTS FAILED: %s", exc, exc_info=True)
    logger.info("Share counts: %d companies; %d quarter counts would change; %d SEC figures outvoted; "
                "%d New York/local fixes; %d waiting for split records; readers %s",
                summary["companies"], summary["corrected"], summary["outvoted"], summary["adr"],
                summary["waiting_for_splits"], "ON" if read_enabled() else "off (watch-only)")
    return summary


def _company(db, symbol, meta, sec):
    import envelope
    import storage
    quarters = [{"period": f.get("period"), "period_end": f.get("period_end"),
                 "diluted": (f.get("income") or {}).get("Diluted Average Shares")}
                for f in storage.get_all_financials(symbol)
                if f.get("freq") != "FY" and f.get("period") and f.get("period_end")]
    if not quarters:
        return None
    snap = db.collection(SPLITS).document(symbol).get()
    splits = (snap.to_dict() or {}).get("events") if snap.exists else None
    foreign = envelope.filing_currency(meta.get("financial_currency")) not in (None, "USD")
    record = {"symbol": symbol, "checked_at": datetime.now(timezone.utc).isoformat(),
              "splits_known": splits is not None,
              "quarters": correct_quarters(quarters, sec.get(str(meta.get("cik") or "").zfill(10), {}),
                                           splits, foreign, _yahoo_shares(db, symbol, meta))}
    stated = {q["period"]: _num(q["diluted"]) for q in quarters}
    for period, entry in record["quarters"].items():
        entry["statement_shares"] = stated.get(period)
        entry["changes_count"] = bool(entry.get("shares")) and not _near(entry["shares"], stated.get(period), 0.005)
    db.collection(COLLECTION).document(symbol).set(record)
    return record


def _yahoo_shares(db, symbol, meta):
    """Yahoo's market cap divided by the close on the day it was saved, or None."""
    cap, when = _num(meta.get("market_cap")), str(meta.get("updated_at") or "")[:10]
    if not cap or not when:
        return None
    import storage
    from google.cloud import firestore
    docs = list(storage.ticker_ref(symbol, db=db).collection("prices").where("date", "<=", when)
                .order_by("date", direction=firestore.Query.DESCENDING).limit(1).stream())
    close = _num((docs[0].to_dict() or {}).get("close")) if docs else None
    return cap / close if close else None
