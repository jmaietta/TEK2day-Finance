"""Daily SEC sync of the ticker universe: TEK2day's `tickers` list is the one security master.

Owner, 2026-10-02: one security master shared by Kilby, TEK2day and CEORater
("I want to share security data, not build a new fucking master for every
feature"), and "I agree that TEK2day's ticker list should become the one
master." Kilby issue jmaietta/chatllm#309 has the measurements behind this.

Until now the universe only grew when someone ran `cli.py ticker add`, so it
froze in April: on October 2, 375 common tickers listed on Nasdaq/NYSE/Cboe were
missing, and about 870 active tickers had not priced in weeks but were still
requested from Yahoo every night.

What a run does, after task 0 of the daily price pull has finished its prices:

* ADD   common tickers on the SEC exchange list (Nasdaq, NYSE, Cboe) that TEK2day
        has never seen: SEC name and CIK now, sector and industry from the
        weekly metadata refresh (no Yahoo call here; #183: an IPO feed must not
        add Yahoo calls), and five years of price history on the next nightly
        pull, in the same single Yahoo request that night makes anyway.
* DEACTIVATE active tickers with no stored price for 30 days. TEK2day's own
        prices decide, never absence from the SEC list: 311 of the tickers the SEC
        no longer lists were still trading on October 2 (class shares written
        BF.B vs BF-B, OTC issuers that stopped filing, renames the SEC list lags).
        Data is kept; `active` goes false with a reason. Tickers under reviewed
        identity routes are never touched.
* FLAG (report only, never act) a ticker whose SEC CIK differs from the stored
        one (ticker reuse goes through the reviewed identity process, as XOM did),
        and inactive tickers the SEC lists again.

UNIVERSE_SYNC_MODE: off | observe (plan and report, change nothing) | apply
(default; the October 2 read-only dry run against production data served as
the observe pass: 342 adds, 1,124 deactivations, 11 CIK flags, no large cap
wrongly deactivated). Every run that is not off writes `universe_sync_runs/{date}`. A sync
failure is logged loudly and never changes the price job's result.
"""
from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger("ydp.universe_sync")

MODE = os.environ.get("UNIVERSE_SYNC_MODE", "apply").strip().lower()
SEC_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
USER_AGENT = "TEK2day Finance support@tek2day.com"
EXCHANGES = {"Nasdaq", "NYSE", "CBOE"}
STALE_DAYS = 30
BACKFILL_PERIOD = "5y"   # what storage.get_prices_history reads back (1,260 rows)
STATE = ("universe_sync", "state")
# Names with no sector get it from Yahoo right away instead of waiting for the
# weekly refresh (owner, 2026-10-02: Randy's team should not "see a bunch of
# blanks"). Rationed (#183): at most this many Yahoo calls a night, about 1% of
# the nightly price pull. A manual run may raise it with UNIVERSE_FILL_LIMIT.
FILL_LIMIT = int(os.environ.get("UNIVERSE_FILL_LIMIT", "100") or 100)
PRIORITY_MAX = 2000
RUNS = "universe_sync_runs"

# A run that would change more than this is a bad parse or a broken price feed,
# not a market event. The first run needs 342 adds and about 1,124
# deactivations once; ordinary days are a handful. A broken price feed would
# mark all ~9,900 stale and is refused. Refuse rather than act.
MAX_ADDS = 600
MAX_DEACTIVATIONS = 1500
MIN_SEC_ROWS = 5000


def tek2day_symbol(sec_ticker: str, exchange: str) -> str | None:
    """The TEK2day spelling of an SEC ticker, or None when it is not a common ticker.

    The SEC writes class shares with a dash (BRK-B); TEK2day writes a dot (BRK.B).
    Warrants, units, rights and preferreds are separate SEC tickers (AAC-WT,
    AAC-UN, AGM-PI; on Nasdaq a fifth letter W, U or R) and are left out.
    """
    t = (sec_ticker or "").strip().upper()
    if not t or not re.fullmatch(r"[A-Z0-9.\-]+", t):
        return None
    if "-" in t:
        base, _, suffix = t.partition("-")
        if len(suffix) == 1 and suffix not in "UWR" and base.isalpha():
            return f"{base}.{suffix}"
        return None
    if exchange == "Nasdaq" and len(t) == 5 and t[-1] in "WUR":
        return None
    return t


def sec_listings(rows: list[list], fields: list[str]) -> dict[str, dict]:
    """{symbol: {cik, name, sec_exchange}} for common tickers on the main exchanges."""
    idx = {name: fields.index(name) for name in ("cik", "name", "ticker", "exchange")}
    out = {}
    for row in rows:
        exchange = row[idx["exchange"]] or ""
        if exchange not in EXCHANGES:
            continue
        symbol = tek2day_symbol(row[idx["ticker"]], exchange)
        if symbol and symbol not in out:
            out[symbol] = {"cik": int(row[idx["cik"]]), "name": (row[idx["name"]] or "").strip(),
                           "sec_exchange": exchange}
    return out


def plan(sec: dict[str, dict], existing: dict[str, dict], last_price: dict[str, str | None],
         identity_managed: set[str], today: date, is_spac=None) -> dict:
    """What a run would change. Pure: no I/O, so every rule is testable."""
    cutoff = (today - timedelta(days=STALE_DAYS)).isoformat()
    # Renamed or resolved records belong to the reviewed identity process: known,
    # so never re-added, and never deactivated or flagged here.
    retired = {s for s, m in existing.items() if m.get("renamed_to") or m.get("security_resolution") is not None}
    adds, skipped_spacs, cik_changed, relisted = [], [], [], []
    for symbol, info in sorted(sec.items()):
        meta = existing.get(symbol)
        if symbol in retired:
            continue
        if meta is None:
            if is_spac and is_spac(symbol, info["name"]):
                skipped_spacs.append(symbol)   # the universe's standing rule (universe.is_likely_spac)
                continue
            adds.append(symbol)
            continue
        stored = meta.get("cik")
        if stored not in (None, "") and int(stored) != info["cik"]:
            cik_changed.append({"symbol": symbol, "stored_cik": int(stored), "sec_cik": info["cik"],
                                "sec_name": info["name"]})
        if meta.get("active") is False:
            relisted.append(symbol)
    deactivate = []
    for symbol, meta in sorted(existing.items()):
        if meta.get("active") is not True or symbol in identity_managed or symbol in retired:
            continue
        last = last_price.get(symbol)
        if last:
            if last < cutoff:
                deactivate.append({"symbol": symbol, "last_price_date": last})
        else:
            added = str(meta.get("added_at") or meta.get("onboarded_at") or "")[:10]
            if not added or added < cutoff:   # never priced, and old enough to have
                deactivate.append({"symbol": symbol, "last_price_date": None})
    refused = []
    if len(sec) < MIN_SEC_ROWS:
        refused.append(f"SEC list has only {len(sec)} common tickers (expected over {MIN_SEC_ROWS})")
    if len(adds) > MAX_ADDS:
        refused.append(f"{len(adds)} additions exceeds the {MAX_ADDS} limit")
    if len(deactivate) > MAX_DEACTIVATIONS:
        refused.append(f"{len(deactivate)} deactivations exceeds the {MAX_DEACTIVATIONS} limit")
    return {"adds": adds, "deactivate": deactivate, "cik_changed": cik_changed, "relisted": relisted,
            "skipped_spacs": skipped_spacs, "refused": refused}


def fill_order(existing: dict[str, dict], identity_managed: set[str], priority: list[str],
               just_added: list[str], limit: int) -> list[str]:
    """Which names get their sector from Yahoo tonight, in order. Pure.

    First: names a Kilby customer holds or follows (priority), including ones
    TEK2day does not cover yet. Then names the sync added tonight. Then any other
    active name still without a sector, newest first.
    """
    def blank(symbol):
        meta = existing.get(symbol)
        return meta is None or (meta.get("active") is not False and not meta.get("sector")
                                and not meta.get("sector_override"))

    retired = {s for s, m in existing.items() if m.get("renamed_to") or m.get("security_resolution") is not None}
    skip = identity_managed | retired
    order, seen = [], set()
    rest = sorted((s for s, m in existing.items()
                   if m.get("active") is True and not m.get("sector") and not m.get("sector_override")),
                  key=lambda s: str(existing[s].get("added_at") or ""), reverse=True)
    for symbol in [*priority, *just_added, *rest]:
        if symbol in seen or symbol in skip or not blank(symbol):
            continue
        seen.add(symbol)
        order.append(symbol)
        if len(order) >= limit:
            break
    return order


# ── I/O ──────────────────────────────────────────────────────────────────────

def fetch_sec() -> dict[str, dict]:
    import requests
    response = requests.get(SEC_EXCHANGE_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                            timeout=60)
    response.raise_for_status()
    body = response.json()
    return sec_listings(body["data"], body["fields"])


def read_existing(db) -> dict[str, dict]:
    from config import COLLECTION_ROOT
    out = {}
    fields = ["active", "cik", "added_at", "onboarded_at", "renamed_to", "security_resolution", "sector",
              "sector_override", "name", "long_name"]
    for doc in db.collection(COLLECTION_ROOT).select(fields).stream():
        out[doc.id] = doc.to_dict() or {}
    return out


def read_last_prices(db, symbols: list[str]) -> dict[str, str | None]:
    from google.cloud import firestore
    from config import COLLECTION_ROOT

    def last(symbol):
        try:
            docs = list(db.collection(COLLECTION_ROOT).document(symbol).collection("prices")
                        .order_by("date", direction=firestore.Query.DESCENDING).limit(1).stream())
            return symbol, ((docs[0].to_dict() or {}).get("date") if docs else None)
        except Exception as exc:
            # Unknown is not stale: a read failure must never deactivate a ticker.
            logger.warning("%s: last price unreadable, left alone: %s", symbol, exc)
            return symbol, "unreadable"

    with ThreadPoolExecutor(16) as pool:
        result = dict(pool.map(last, symbols))
    return {s: (None if d is None else d) for s, d in result.items() if d != "unreadable"}


def backfill_pending(db=None) -> set[str]:
    """Names added by the sync that still need their price history. Never raises."""
    try:
        if db is None:
            import storage
            db = storage.get_db()
        snap = db.collection(STATE[0]).document(STATE[1]).get()
        return set((snap.to_dict() or {}).get("backfill_pending") or []) if snap.exists else set()
    except Exception as exc:
        logger.warning("backfill list unreadable; tonight uses the normal window: %s", exc)
        return set()


def read_priority(db) -> list[str]:
    try:
        snap = db.collection(STATE[0]).document(STATE[1]).get()
        return list((snap.to_dict() or {}).get("priority") or []) if snap.exists else []
    except Exception as exc:
        logger.warning("priority list unreadable: %s", exc)
        return []


def add_priority(db, symbols: list[str]) -> int:
    """Kilby's names that should get a sector first (from /partner/v1/symbols/priority)."""
    from google.cloud import firestore
    clean = sorted({str(s or "").strip().upper().replace("-", ".") for s in symbols if str(s or "").strip()})[:PRIORITY_MAX]
    if clean:
        db.collection(STATE[0]).document(STATE[1]).set({"priority": firestore.ArrayUnion(clean)}, merge=True)
    return len(clean)


def fill(db, storage, order: list[str], existing: dict[str, dict], delay: float | None = None) -> dict:
    """Ask Yahoo for each name's details and save them. Never raises."""
    import time
    import fetchers
    from google.cloud import firestore
    if delay is None:
        from config import FETCH_DELAY as delay
    filled, no_sector, unknown, failed = [], [], [], []
    now = datetime.now(timezone.utc).isoformat()
    for symbol in order:
        try:
            meta = fetchers.fetch_ticker_info(symbol.replace(".", "-"))
            stored = existing.get(symbol) or {}
            if not meta and not stored:
                unknown.append(symbol)          # nobody knows it: left for review
                continue
            meta = meta or {}
            if not meta.get("sector"):
                # Yahoo knows the security but cannot classify it (often a foreign
                # name). Next: the SEC industry code, then the same company's
                # listing elsewhere.
                found = classify_elsewhere(symbol, meta, stored)
                if found:
                    meta.update(found)
            if not meta:
                unknown.append(symbol)
                continue
            meta["symbol"] = symbol
            if symbol not in existing:
                # A name a customer holds that TEK2day did not cover: covered from now on.
                meta.update(added_at=now, added_by="kilby_priority")
            storage.write_ticker_meta(symbol, meta)
            (filled if meta.get("sector") else no_sector).append(symbol)
        except Exception as exc:
            failed.append(symbol)
            logger.warning("%s: sector fill failed: %s", symbol, exc)
        time.sleep(delay)
    done = [s for s in order if s not in failed]
    if done:
        try:
            db.collection(STATE[0]).document(STATE[1]).set({"priority": firestore.ArrayRemove(done)}, merge=True)
        except Exception as exc:
            logger.warning("priority list not trimmed: %s", exc)
    return {"filled": filled, "no_sector": no_sector, "unknown": unknown, "failed": failed}


def classify_elsewhere(symbol: str, meta: dict, stored: dict) -> dict | None:
    """Sector fields from the SEC industry code, else from the home listing. Never raises."""
    import fetchers
    import sec_sectors
    try:
        found = sec_sectors.sector_from_sec(stored.get("cik") or meta.get("cik"))
        if found:
            return {"sector": found[0], "industry": found[1], "sector_source": "sec_sic"}
        name = meta.get("long_name") or meta.get("name") or stored.get("long_name") or stored.get("name") or ""
        hit = fetchers.search_sector(name, exclude=symbol.replace(".", "-"))
        if hit:
            return {"sector": hit[0], "industry": hit[1], "sector_source": f"home_listing:{hit[2]}"}
    except Exception as exc:
        logger.warning("%s: classification elsewhere failed: %s", symbol, exc)
    return None


def set_override(db, symbol: str, sector: str, industry: str, by: str, note: str = "") -> dict:
    """#4: a person's sector for a name nothing else can classify. No job ever writes over it."""
    from config import COLLECTION_ROOT
    entry = {"sector_override": sector.strip(), "industry_override": industry.strip(),
             "override_by": by, "override_note": note.strip(),
             "override_at": datetime.now(timezone.utc).isoformat()}
    db.collection(COLLECTION_ROOT).document(symbol.upper()).set(entry, merge=True)
    db.collection("sector_overrides").document().set(dict(entry, symbol=symbol.upper()))   # every change kept
    return entry


def backfill_done(db, symbol: str) -> None:
    from google.cloud import firestore
    if db is None:
        import storage
        db = storage.get_db()
    db.collection(STATE[0]).document(STATE[1]).set(
        {"backfill_pending": firestore.ArrayRemove([symbol])}, merge=True)


def run(db=None, today: date | None = None) -> dict | None:
    """One sync. Never raises: the price job's result is not the sync's to decide."""
    if MODE == "off":
        logger.info("Universe sync off")
        return None
    try:
        import storage
        from universe import is_likely_spac
        db = db or storage.get_db()
        today = today or datetime.now(timezone.utc).date()
        sec = fetch_sec()
        existing = read_existing(db)
        retired = {s for s, m in existing.items() if m.get("renamed_to") or m.get("security_resolution") is not None}
        active = [s for s, m in existing.items() if m.get("active") is True and s not in retired]
        identity_managed = {s for s in active if storage.event_for(s)}
        last_price = read_last_prices(db, [s for s in active if s not in identity_managed])
        # A ticker whose last price could not be read is absent here; treat it as recent.
        unread = [s for s in active if s not in identity_managed and s not in last_price]
        for s in unread:
            last_price[s] = today.isoformat()
        result = plan(sec, existing, last_price, identity_managed, today, is_spac=is_likely_spac)
        applied = MODE == "apply" and not result["refused"]
        if applied:
            _apply(db, storage, sec, result)
        order = fill_order(existing, identity_managed, read_priority(db), result["adds"] if applied else [], FILL_LIMIT)
        filled = fill(db, storage, order, existing) if MODE == "apply" else {"would_fill": order}
        result["fill"] = filled
        _report(db, today, result, applied, unread)
        return result
    except Exception as exc:
        logger.error("UNIVERSE SYNC FAILED: %s", exc, exc_info=True)
        return None


def _apply(db, storage, sec, result):
    from google.cloud import firestore
    now = datetime.now(timezone.utc).isoformat()
    for symbol in result["adds"]:
        info = sec[symbol]
        storage.write_ticker_meta(symbol, {
            "symbol": symbol, "name": info["name"], "cik": info["cik"], "sec_exchange": info["sec_exchange"],
            "active": True, "added_at": now, "added_by": "universe_sync", "sector": "", "industry": "",
        })
    if result["adds"]:
        db.collection(STATE[0]).document(STATE[1]).set(
            {"backfill_pending": firestore.ArrayUnion(result["adds"])}, merge=True)
    for row in result["deactivate"]:
        storage.deactivate_ticker(row["symbol"], reason="no_price_30d", last_price_date=row["last_price_date"])


def _report(db, today, result, applied, unread):
    fill_result = result.pop("fill", {}) or {}
    summary = {k: len(v) for k, v in result.items()}
    fill_counts = {k: len(v) for k, v in fill_result.items()}
    logger.info("UNIVERSE FILL %s", " ".join(f"{k}={v}" for k, v in fill_counts.items()) or "nothing to fill")
    line = ("UNIVERSE SYNC mode=%s applied=%s adds=%d deactivate=%d cik_changed=%d relisted=%d "
            "skipped_spacs=%d unreadable=%d refused=%s")
    args = (MODE, applied, summary["adds"], summary["deactivate"], summary["cik_changed"], summary["relisted"],
            summary["skipped_spacs"], len(unread), "; ".join(result["refused"]) or "no")
    (logger.error if result["refused"] else logger.info)(line, *args)
    db.collection(RUNS).document(today.isoformat()).set({
        "mode": MODE, "applied": applied, "at": datetime.now(timezone.utc).isoformat(),
        "counts": summary, "refused": result["refused"], "unreadable": len(unread),
        "adds": result["adds"][:1000], "deactivate": result["deactivate"][:1500],
        "cik_changed": result["cik_changed"][:200], "relisted": result["relisted"][:500],
        "skipped_spacs": result["skipped_spacs"][:500],
        "fill": {k: v[:500] for k, v in fill_result.items()}, "fill_counts": fill_counts,
    })
    result["fill"] = fill_result
