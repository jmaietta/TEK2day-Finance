"""Repair proposals — building them, storing them, reading them.

Deliberately free of FastAPI and of anything web-related, so the background job
that generates proposals does not depend on the web application. The admin page
and the scheduled job both import this; neither imports the other.

A proposal records what a repair scan FOUND. It is not a set of writes. It
becomes writes only when an admin approves specific records, which is a separate
code path that does not exist yet.

    repair_proposals/{proposal_id}
        generated_at, source, source_read_at, target, status
        records[]  — one per stub period: metrics, sections, checks

Nothing in this module writes financial data. The only collection it writes is
repair_proposals, which sits alongside market data rather than inside it.
"""
import math
import re
from datetime import datetime, timezone

import storage

PROPOSALS = "repair_proposals"

# Metrics shown on a record: enough to judge whether the numbers are sane, not
# the whole statement. The full field list is available on demand in the page.
HEADLINE = [
    ("Total Revenue", "income"),
    ("Net Income", "income"),
    ("Diluted EPS", "income"),
    ("Operating Cash Flow", "cash_flow"),
    ("Total Assets", "balance_sheet"),
]

# Firestore section keys mapped to the labels an admin reads.
SECTION_LABEL = {"income": "income", "balance_sheet": "balance", "cash_flow": "cash flow"}

QUARTER_RE = re.compile(r"[0-9]{4}-Q[0-9]")

# A period Yahoo may still fill in, versus one it never will. Yahoo publishes
# statements over days-to-weeks after a release, so a just-reported quarter that
# is still empty is worth retrying; a 2021 annual is not.
RECENT_DAYS = 180


def finite(v) -> bool:
    """True for a usable number. Firestore stores Yahoo's NaN verbatim."""
    return isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _num(v):
    """JSON-safe. Missing stays missing — never rendered as zero."""
    return v if finite(v) else None


def is_stub(doc: dict) -> bool:
    """A period whose contents never arrived.

    Structural rather than a field-count threshold: a real filing populates all
    three statements, so an empty section is the reliable signal and needs no
    tuning per company (JPM's income statement has 38 fields, JNJ's has 49).
    """
    return any(not (doc.get(section) or {}) for section in storage.STATEMENT_SECTIONS)


def is_recent(doc: dict, days: int = RECENT_DAYS) -> bool:
    try:
        end = datetime.strptime(str(doc.get("period_end")), "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return (datetime.now(timezone.utc) - end).days <= days


def run_checks(merged: dict, stored_all: dict, period: str) -> list[dict]:
    """Arithmetic sanity checks, so a human's attention goes where it is needed.

    These do not decide anything. They mark a record for closer reading; the
    admin still approves or rejects every record either way.
    """
    out = []
    inc = merged.get("income") or {}
    bs = merged.get("balance_sheet") or {}
    rev, ni, eps = inc.get("Total Revenue"), inc.get("Net Income"), inc.get("Diluted EPS")
    shares = inc.get("Diluted Average Shares")

    # EPS is:
    #
    #     Net Income to Common Stockholders / Diluted Average Shares
    #
    # Net income to COMMON, after preferred holders are paid — not total net
    # income. Checking EPS against total net income fails every company with
    # preferred stock, by exactly its preferred dividends, in every period.
    #
    # AGNC FY2024, a mortgage REIT and therefore heavy in preferred:
    #     $731,000,000 / 786,000,000 shares = $0.93   <- the reported EPS
    #     Net Income                          $863,000,000
    #     Preferred Stock Dividends           $132,000,000  <- the whole gap
    #
    # Measured across a 250-ticker sample: 19% of tickers carried at least one
    # EPS warning. Most are this.
    ni_common = inc.get("Net Income Common Stockholders")
    eps_income = ni_common if finite(ni_common) else ni

    quarters = sorted(p for p in stored_all if QUARTER_RE.fullmatch(p))
    if period in quarters and quarters.index(period) > 0:
        prior = (stored_all[quarters[quarters.index(period) - 1]].get("income") or {}).get("Total Revenue")
        if finite(prior) and finite(rev) and prior:
            ratio = rev / prior
            out.append({
                "name": "Revenue vs prior quarter",
                "detail": f"{rev/1e6:,.0f}m vs {prior/1e6:,.0f}m ({ratio:.2f}x)",
                "pass": 0.5 <= ratio <= 2.0,
            })

    if all(finite(x) for x in (eps, shares, eps_income)) and eps_income and shares:
        # Reported EPS is rounded to two decimals — that is standard public
        # company reporting, not a defect — so it stands for a BAND, not a point.
        # A reported 0.00 means "somewhere in +/-0.005 per share".
        #
        # Multiplied out, that band is worth 0.005 * shares of net income. For a
        # small cap that dwarfs the whole profit: ABTC 2025-Q3 reports 0.00 on a
        # real 3,475,000 / 899,489,426 = 0.0039, and the band is +/-4.5m. The old
        # point comparison called that "100.0% apart" and wrote a warning onto a
        # record that was entirely correct — and that warning then travelled to
        # the site, the exports and Kilby.
        #
        # So measure the distance to the BAND, not to its midpoint. A genuinely
        # wrong EPS is still caught: the band is only ever half a cent wide.
        implied = eps * shares
        rounding_band = 0.005 * abs(shares)
        excess = max(0.0, abs(implied - eps_income) - rounding_band)
        gap = excess / abs(eps_income)
        out.append({
            "name": "Diluted EPS x shares vs net income",
            "detail": f"{implied/1e6:,.0f}m vs {eps_income/1e6:,.0f}m ({gap*100:.1f}% apart)",
            "pass": gap <= 0.10,
        })

    assets = bs.get("Total Assets")
    liabilities = bs.get("Total Liabilities Net Minority Interest")
    equity = bs.get("Stockholders Equity")
    if all(finite(x) for x in (assets, liabilities, equity)) and assets:
        # `Stockholders Equity` is the PARENT's share only. A company that
        # consolidates a subsidiary it does not wholly own carries the rest in
        # Minority Interest, and the identity is not complete without it:
        #
        #   Assets = Liabilities + Stockholders Equity + Minority Interest
        #
        # Leaving it out flagged nine of Walmart's eleven periods as broken
        # balance sheets. They are not. WMT 2023-FY:
        #
        #   159,206 + 76,693           = 235,899  vs assets 243,197  -> "3% off"
        #   159,206 + 76,693 + 7,298   = 243,197  vs assets 243,197  -> exact
        #
        # Minority Interest is 7,298m — the gap to the dollar. Same story for
        # XOM at 7,736m. Walmex and Flipkart for one, consolidated affiliates
        # for the other.
        #
        # This is the second check found flagging correct records rather than
        # bad data (the first was Diluted EPS x shares). A check that fires on
        # sound records is worse than no check: its warnings ride ON the record.
        minority = bs.get("Minority Interest")
        total_equity_side = liabilities + equity + (minority if finite(minority) else 0.0)
        gap = abs(total_equity_side - assets) / assets
        out.append({
            "name": "Assets = Liabilities + Equity",
            "detail": f"{assets/1e6:,.0f}m vs {total_equity_side/1e6:,.0f}m ({gap*100:.2f}% apart)",
            "pass": gap <= 0.02,
        })
    return out


def build_record(symbol: str, period: str, stored: dict, incoming: dict | None,
                 merged: dict, filled: list[str], checks: list[dict]) -> dict:
    """One record of a proposal, in the shape the review page renders.

    Values are computed here and stored. The page displays them and sends
    nothing back: when the apply path exists it re-derives every write from this
    document, so the browser is never the source of anything written.
    """
    def cell(name, section):
        stored_v = (stored.get(section) or {}).get(name)
        yahoo_v = (incoming.get(section) or {}).get(name) if incoming else None
        merged_v = (merged.get(section) or {}).get(name)
        if finite(stored_v) and finite(yahoo_v):
            action = "same" if stored_v == yahoo_v else "keep ours"
        elif finite(stored_v):
            action = "keep ours"
        elif finite(yahoo_v):
            action = "fill"
        else:
            action = "both blank"
        return {
            "name": name,
            "firebase": _num(stored_v),
            "yahoo": _num(yahoo_v),
            "after": _num(merged_v),
            "action": action,
        }

    sections = []
    for key, label in SECTION_LABEL.items():
        sections.append({
            "name": label,
            "key": key,
            "before": sum(1 for v in (stored.get(key) or {}).values() if finite(v)),
            "after": sum(1 for v in (merged.get(key) or {}).values() if finite(v)),
            "total": max(len(merged.get(key) or {}), len(stored.get(key) or {})),
            "fills": sum(1 for f in filled if f.startswith(key + ".")),
        })

    record = {
        "id": f"{symbol.replace('.', '')}-{period}",
        "symbol": symbol,
        "period": period,
        "period_end": stored.get("period_end"),
        "fields": len(filled),
        "metrics": [cell(name, section) for name, section in HEADLINE],
        "sections": sections,
        "checks": checks,
        "source": "Yahoo Finance",
    }

    # Status drives what the reviewer sees first. Anything that could not be
    # checked is treated as needing review, never as clean.
    if not filled:
        recent = is_recent(stored)
        record["status"] = "RETRY_LATER" if recent else "NO_SOURCE"
        record["state_label"] = "Yahoo has not published yet" if recent else "nothing at source"
        record["note"] = (
            "Reported, but Yahoo still returns the same fields we already hold. "
            "Nothing to approve; the next scan picks it up."
            if recent else
            "Yahoo holds no more data for this period and is not expected to. It stays "
            "incomplete and is disclosed as a coverage gap."
        )
    elif not checks or any(not c["pass"] for c in checks):
        failed = [c for c in checks if not c["pass"]]
        record["status"] = "REVIEW"
        record["state_label"] = (
            f"{len(failed)} check{'s' if len(failed) != 1 else ''} failed" if failed
            else "too little data to check"
        )
    else:
        record["status"] = "CLEAN"
        record["state_label"] = f"{len(checks)} checks passed"

    return record


def review_and_populate(symbol: str, period: str, incoming: dict, db=None) -> dict | None:
    """Populate one stored record from data the pull already fetched.

    Called from the quarterly pull AFTER its normal write, using the Yahoo data
    already in hand — so this adds no Yahoo traffic at all.

    Returns a record describing what happened, or None if there was nothing to
    do. Populates only empty and NaN fields; a populated value is never
    overwritten (storage.merge_financial_doc, 18 tests).

    Records that fail a sanity check are still populated, with the warning
    written onto the record so it travels with the data — to the site, to
    exports, and to Kilby. A warning that only reaches an admin queue reaches
    nobody at the moment the number is used.

    RAISES NOTHING the caller must handle: the pull's job is ingestion, and a
    problem here must never stop it. See safe_review_and_populate.
    """
    db = db or storage.get_db()
    ref = (
        db.collection(storage.COLLECTION_ROOT)
        .document(symbol)
        .collection("financials")
        .document(period)
    )
    snap = ref.get()
    if not snap.exists:
        return None  # a new period; write_financials already handled it

    stored = snap.to_dict() or {}
    if not is_stub(stored):
        return None  # complete record, nothing to review

    merged, filled = storage.merge_financial_doc(stored, incoming)
    if not filled:
        # Yahoo has nothing to add. Still worth recording so the gap is visible.
        record = build_record(symbol, period, stored, incoming, merged, [], [])
        record["populated"] = False
        return record

    stored_all = {
        d.id: (d.to_dict() or {})
        for d in db.collection(storage.COLLECTION_ROOT).document(symbol).collection("financials").stream()
    }
    checks = run_checks(merged, stored_all, period)
    warnings = [c for c in checks if not c["pass"]]

    merged["backfilled_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    merged["backfilled_fields"] = sorted(filled)
    if warnings:
        # The warning lives ON the record, not in a queue.
        merged["data_warnings"] = [
            {"code": c["name"], "detail": c["detail"]} for c in warnings
        ]
    ref.set(merged)

    record = build_record(symbol, period, stored, incoming, merged, filled, checks)
    record["populated"] = True
    record["populated_at"] = merged["backfilled_at"]
    return record


def safe_review_and_populate(symbol: str, period: str, incoming: dict, db=None,
                             logger=None) -> dict | None:
    """review_and_populate, but incapable of interrupting the caller.

    The pull exists to ingest. If the review fails for one ticker — odd data, an
    unexpected shape, a transient Firestore error — it must log and let the pull
    continue, not take the run down with it.
    """
    try:
        return review_and_populate(symbol, period, incoming, db=db)
    except Exception as exc:  # noqa: BLE001 — deliberately catching everything
        if logger:
            logger.warning("data review skipped for %s %s: %s", symbol, period, exc)
        return None


def save(proposal_id: str, records: list[dict], target: str, source_read_at: str, db=None) -> str:
    """Store a proposal. Writes only to repair_proposals — never to financials."""
    db = db or storage.get_db()
    db.collection(PROPOSALS).document(proposal_id).set({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance",
        "source_read_at": source_read_at,
        "target": target,
        "status": "open",
        "records": records,
    })
    return proposal_id


def summarise(records: list[dict]) -> dict:
    return {
        "needs_review": sum(1 for r in records if r.get("status") == "REVIEW"),
        "checks_passed": sum(1 for r in records if r.get("status") == "CLEAN"),
        "no_action": sum(1 for r in records if r.get("status") in ("RETRY_LATER", "NO_SOURCE")),
        "fields_proposed": sum(int(r.get("fields") or 0) for r in records),
    }
