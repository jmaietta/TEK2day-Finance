"""Exchange rates for companies that file in a currency other than the US dollar.

WHY. 699 companies file their statements in another currency while their
shares trade in US dollars. Dividing one by the other without converting made
TSM's P/E read 1.0x instead of about 27x (18 Oct 2026). The figures shown stay
in the company's own currency (owner: "If the number comes in as TWD, and it
is TWD, then label it TWD"); only the valuation ratios are calculated in US
dollars, through these rates.

THE YAHOO RULE. One lookup per currency, once a night, run by task 0 of the
nightly price job (owner, 18 Oct 2026: "use yahoo for fx"). 28 currencies
today. A new currency is added automatically, never more than MAX_LOOKUPS a
night. Nothing here is called while serving a page.

STORAGE. fx_rates/{CUR} holds the latest rate and its date, and
fx_rates/{CUR}/history/{date} keeps each night's rate, so past rates exist from
now on. Rates are units of the currency per one US dollar.
"""
from __future__ import annotations

import logging
import math
from datetime import date, datetime, timezone

logger = logging.getLogger("ydp.fx_rates")

COLLECTION = "fx_rates"
MAX_LOOKUPS = 40          # owner: add new currencies automatically, never more than 40 a night
MAX_AGE_DAYS = 5          # a rate older than this is not used: the ratios go blank instead
MAX_NIGHTLY_MOVE = 0.5    # a rate 50% away from yesterday's is held for review, not stored


def _valid_rate(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def currencies_needed(db) -> list[str]:
    """Every filing currency other than the US dollar among active companies."""
    from config import COLLECTION_ROOT
    import envelope
    found = set()
    docs = db.collection(COLLECTION_ROOT).where("active", "==", True).select(["financial_currency"]).stream()
    for doc in docs:
        code = envelope.filing_currency((doc.to_dict() or {}).get("financial_currency"))
        if code and code != "USD":
            found.add(code)
    return sorted(found)


def run(db=None) -> dict:
    """Fetch and store tonight's rates. Never raises; a failure keeps yesterday's rate."""
    import fetchers
    import storage
    summary = {"needed": 0, "stored": 0, "failed": [], "held": [], "skipped_over_limit": []}
    try:
        db = db or storage.get_db()
        needed = currencies_needed(db)
    except Exception as exc:
        logger.error("Exchange rates: could not list currencies: %s", exc)
        return summary
    summary["needed"] = len(needed)
    if len(needed) > MAX_LOOKUPS:
        summary["skipped_over_limit"] = needed[MAX_LOOKUPS:]
        logger.error("Exchange rates: %d currencies needed, limit is %d; not fetched: %s",
                     len(needed), MAX_LOOKUPS, ",".join(summary["skipped_over_limit"]))
        needed = needed[:MAX_LOOKUPS]
    now = datetime.now(timezone.utc).isoformat()
    for currency in needed:
        quote = fetchers.fetch_fx_rate(currency)
        if not quote or not _valid_rate(quote.get("rate")):
            summary["failed"].append(currency)
            continue
        ref = db.collection(COLLECTION).document(currency)
        try:
            previous = (ref.get().to_dict() or {}).get("rate")
            if _valid_rate(previous) and abs(quote["rate"] / previous - 1) > MAX_NIGHTLY_MOVE:
                summary["held"].append(currency)
                logger.error("Exchange rates: %s moved from %s to %s overnight; held for review, not stored",
                             currency, previous, quote["rate"])
                continue
            record = {**quote, "fetched_at": now}
            ref.set(record)
            ref.collection("history").document(quote["rate_date"]).set(record)
            summary["stored"] += 1
        except Exception as exc:
            summary["failed"].append(currency)
            logger.error("Exchange rates: %s not stored: %s", currency, exc)
    level = logging.ERROR if summary["failed"] or summary["held"] else logging.INFO
    logger.log(level, "Exchange rates: %d of %d stored; failed %s; held %s",
               summary["stored"], summary["needed"], summary["failed"] or "none", summary["held"] or "none")
    return summary


def latest(currency: str, db=None, today: date | None = None) -> dict | None:
    """{"currency", "rate", "rate_date"} for a usable rate, or None.

    None when no rate is held, or the one held is more than MAX_AGE_DAYS old:
    a blank ratio is better than one built on a stale rate.
    """
    if not currency or currency == "USD":
        return None
    try:
        import storage
        db = db or storage.get_db()
        record = db.collection(COLLECTION).document(currency).get().to_dict() or {}
    except Exception as exc:
        logger.warning("Exchange rate for %s unreadable: %s", currency, exc)
        return None
    rate, rate_date = record.get("rate"), record.get("rate_date")
    if not _valid_rate(rate) or not isinstance(rate_date, str):
        return None
    try:
        age = ((today or datetime.now(timezone.utc).date()) - date.fromisoformat(rate_date)).days
    except ValueError:
        return None
    if age > MAX_AGE_DAYS:
        return None
    return {"currency": currency, "rate": float(rate), "rate_date": rate_date}
