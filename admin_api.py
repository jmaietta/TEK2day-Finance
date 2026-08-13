"""Admin surface for TEK2day Finance — currently the Data Review page.

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
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

import auth
import proposals
import storage

router = APIRouter(prefix="/admin", tags=["admin"])

# Proposal building and storage live in proposals.py, which the scheduled job
# also imports. Keeping it there means the job does not depend on the web app.
PROPOSALS = proposals.PROPOSALS
_STATIC = Path(__file__).parent / "static"

@router.get("/data-review", response_class=HTMLResponse, include_in_schema=False)
def data_review_page(request: Request) -> HTMLResponse:
    """The review queue. Gated; the page itself holds no data.

    Kept out of the OpenAPI schema and out of the sitemap: it is not part of the
    public product, and advertising an admin URL invites probing.
    """
    auth.require_admin(request)
    html = (_STATIC / "admin-data-review.html").read_text(encoding="utf-8")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/api/data-review/proposals", include_in_schema=False)
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


@router.get("/api/data-review/proposals/{proposal_id}", include_in_schema=False)
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
        "summary": proposals.summarise(records),
        "records": records,
    }
