"""Admin surface for TEK2day Finance — currently the data-repair review queue.

READ-ONLY. Nothing here writes to the financial data. The apply path, which
does, is deliberately a later change so the gate, the page and the proposal
loading are all proven in production before anything can touch a record.

Access is the four allowlisted addresses in config.ADMIN_EMAILS, signed in, with
an address Google has verified. See auth.require_admin.

Repair proposals live in their own root collection, separate from market data:

    repair_proposals/{proposal_id}
        generated_at, source_read_at, target, status
        records[]  — one per stub period, with before/after values and checks

A proposal is a record of what a repair run FOUND. It becomes a set of writes
only when an admin approves it, which is not yet possible.
"""
import math
import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import auth
import storage

router = APIRouter(prefix="/admin", tags=["admin"])

PROPOSALS = "repair_proposals"
_STATIC = Path(__file__).parent / "static"

# Metrics shown on a record. Deliberately few: enough to judge whether the
# numbers are sane, not the whole statement.
HEADLINE = [
    ("Total Revenue", "income"),
    ("Net Income", "income"),
    ("Diluted EPS", "income"),
    ("Operating Cash Flow", "cash_flow"),
    ("Total Assets", "balance_sheet"),
]
SECTION_LABEL = {"income": "income", "balance_sheet": "balance", "cash_flow": "cash flow"}


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not (isinstance(v, float) and (math.isnan(v) or math.isinf(v)))


def _num(v):
    """Firestore stores NaN; JSON cannot. Missing is null, never zero."""
    return v if _finite(v) else None


def build_record(symbol: str, period: str, stored: dict, incoming: dict,
                 merged: dict, filled: list[str], checks: list[dict]) -> dict:
    """One record of a proposal, in the shape the page renders.

    Values are computed here, server-side, and stored. The page displays them
    and sends nothing back — when the apply path exists it will re-derive every
    write from this document rather than trusting the browser.
    """
    def cell(name, section):
        s = (stored.get(section) or {}).get(name)
        y = (incoming.get(section) or {}).get(name) if incoming else None
        m = (merged.get(section) or {}).get(name)
        if _finite(s) and _finite(y):
            action = "same" if s == y else "keep ours"
        elif _finite(s):
            action = "keep ours"
        elif _finite(y):
            action = "fill"
        else:
            action = "both blank"
        return {"name": name, "firebase": _num(s), "yahoo": _num(y), "after": _num(m), "action": action}

    sections = []
    for key, label in SECTION_LABEL.items():
        before = sum(1 for v in (stored.get(key) or {}).values() if _finite(v))
        after = sum(1 for v in (merged.get(key) or {}).values() if _finite(v))
        total = max(len(merged.get(key) or {}), len(stored.get(key) or {}))
        sections.append({
            "name": label,
            "key": key,
            "before": before,
            "after": after,
            "total": total,
            "fills": sum(1 for f in filled if f.startswith(key + ".")),
        })

    return {
        "id": f"{symbol.replace('.', '')}-{period}",
        "symbol": symbol,
        "period": period,
        "period_end": stored.get("period_end"),
        "fields": len(filled),
        "metrics": [cell(n, s) for n, s in HEADLINE],
        "sections": sections,
        "checks": checks,
        "source": "Yahoo Finance",
    }


def save_proposal(proposal_id: str, records: list[dict], target: str,
                  source_read_at: str, db=None) -> str:
    """Store a proposal. Never writes financial data — only the proposal itself."""
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


@router.get("/repair", response_class=HTMLResponse, include_in_schema=False)
def repair_page(request: Request) -> HTMLResponse:
    """The review queue. Gated; the page itself holds no data.

    Kept out of the OpenAPI schema and out of the sitemap: it is not part of the
    public product, and advertising an admin URL invites probing.
    """
    auth.require_admin(request)
    html = (_STATIC / "admin-repair.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/api/repair/proposals", include_in_schema=False)
def list_proposals(request: Request) -> dict:
    """Proposals, newest first — enough to populate the sidebar."""
    auth.require_admin(request)
    db = storage.get_db()
    docs = (
        db.collection(PROPOSALS)
        .order_by("generated_at", direction="DESCENDING")
        .limit(20)
        .stream()
    )
    out = []
    for d in docs:
        x = d.to_dict() or {}
        out.append(
            {
                "id": d.id,
                "generated_at": x.get("generated_at"),
                "target": x.get("target"),
                "status": x.get("status", "open"),
                "record_count": len(x.get("records") or []),
            }
        )
    return {"proposals": out}


@router.get("/api/repair/proposals/{proposal_id}", include_in_schema=False)
def get_proposal(proposal_id: str, request: Request) -> dict:
    """One proposal in full — what the page renders.

    Returned as stored. The page displays it; it does not send values back. When
    the apply path exists it will re-derive every value from this document, so
    the browser is never the source of anything written.
    """
    admin_email = auth.require_admin(request)

    snap = storage.get_db().collection(PROPOSALS).document(proposal_id).get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="No such proposal")

    data = snap.to_dict() or {}
    records = data.get("records") or []

    return {
        "id": proposal_id,
        "generated_at": data.get("generated_at"),
        "source": data.get("source", "Yahoo Finance"),
        "source_read_at": data.get("source_read_at"),
        "target": data.get("target"),
        "status": data.get("status", "open"),
        # Echoed so the page can show who is signed in without a second call.
        "viewer": admin_email,
        # Until the apply path ships, the page must not offer decisions it
        # cannot honour. The page reads this rather than assuming.
        "decisions_enabled": False,
        "summary": {
            "needs_review": sum(1 for r in records if r.get("status") == "REVIEW"),
            "checks_passed": sum(1 for r in records if r.get("status") == "CLEAN"),
            "no_action": sum(1 for r in records if r.get("status") in ("RETRY_LATER", "NO_SOURCE")),
            "fields_proposed": sum(int(r.get("fields") or 0) for r in records),
        },
        "records": records,
    }
