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

    if all(finite(x) for x in (eps, shares, ni)) and ni:
        implied = eps * shares
        gap = abs(implied - ni) / abs(ni)
        out.append({
            "name": "Diluted EPS x shares vs net income",
            "detail": f"{implied/1e6:,.0f}m vs {ni/1e6:,.0f}m ({gap*100:.1f}% apart)",
            "pass": gap <= 0.10,
        })

    assets = bs.get("Total Assets")
    liabilities = bs.get("Total Liabilities Net Minority Interest")
    equity = bs.get("Stockholders Equity")
    if all(finite(x) for x in (assets, liabilities, equity)) and assets:
        gap = abs((liabilities + equity) - assets) / assets
        out.append({
            "name": "Assets = Liabilities + Equity",
            "detail": f"{assets/1e6:,.0f}m vs {(liabilities+equity)/1e6:,.0f}m ({gap*100:.2f}% apart)",
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
