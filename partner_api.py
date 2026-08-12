"""TEK2day Finance Partner API — READ-ONLY data access for approved partner
systems (currently Kilby only).

Why this exists as its own router rather than more /api/ routes:

1. Rate limiting. app.py applies the public per-IP limiter to every path starting
   with "/api/" (30 burst, 2/sec). Kilby's backend serves many users from a handful
   of Cloud Run egress IPs, so it would trip that limiter and throttle our own
   product. "/partner/" sits outside it and gets its own bucket.
2. Versioning. Partner responses are a stable contract (/partner/v1/...). The
   website's /api/ routes are free to change shape whenever the UI needs it.

Phase 1 ships /health only. Authentication (Google OIDC, no shared keys) lands in
Phase 2; data endpoints in Phase 3. The API surface itself is published as
OpenAPI at /openapi.json (browsable at /docs), which FastAPI generates.

This module must never acquire app.COMMAND_LOCK — that lock serialises the Rich
terminal renderer, and partner traffic must not queue behind website traffic.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter

import storage

router = APIRouter(prefix="/partner/v1", tags=["partner"])

# Bumped only for breaking changes to a response contract. Kilby pins on this.
API_VERSION = "1.0.0"

# The ticker used to probe data freshness. Liquid, always-covered, and cheap:
# one Firestore read of a single price document.
_FRESHNESS_PROBE_SYMBOL = "AAPL"


def _request_id() -> str:
    return "t2d_" + uuid.uuid4().hex[:24]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_price_date(symbol: str) -> str | None:
    """Most recent stored price date, or None if unavailable.

    Never raises: /health must report a degraded backend rather than 500, so a
    monitor can tell "TEK2day is up but Firestore is unhappy" from "TEK2day is down".
    """
    try:
        rows = storage.get_prices_history(symbol, limit=1)
    except Exception:
        return None
    if not rows:
        return None
    return rows[0].get("date")


@router.get("/health")
def partner_health() -> dict:
    """Liveness plus data freshness.

    Freshness matters more than liveness here: the service can be perfectly up
    while the ingestion jobs have stalled, and a partner needs to know that
    before it presents our numbers to an institutional user.
    """
    latest = _latest_price_date(_FRESHNESS_PROBE_SYMBOL)
    return {
        "status": "ok" if latest else "degraded",
        "api_version": API_VERSION,
        "request_id": _request_id(),
        "checked_at": _now_iso(),
        "data_freshness": {
            "probe_symbol": _FRESHNESS_PROBE_SYMBOL,
            "latest_price_date": latest,
        },
    }


# No /capabilities endpoint: FastAPI already publishes the API surface as
# OpenAPI at /openapi.json, with browsable docs at /docs. That is the standard
# every client generator and gateway reads, and it is generated from the code so
# it cannot drift. A hand-maintained capabilities list would be a second,
# non-standard source of truth that could only go stale.
#
# /health is different in kind and stays: OpenAPI describes which endpoints
# exist, which is static. /health reports whether the DATA is current right now,
# which no specification can express — and Kilby needs that before it trusts a
# number in front of an investor.
