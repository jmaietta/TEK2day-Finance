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
import os
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

import storage

router = APIRouter(prefix="/partner/v1", tags=["partner"])

# Bumped only for breaking changes to a response contract. Kilby pins on this.
API_VERSION = "1.0.0"

# ── who is allowed to call the protected endpoints ───────────────────────────
# Kilby's Cloud Run services (chatllm-git = production, chatllm-test, and
# chatllm-skuttle) all run as this one service account, so a single entry covers
# production and Kilby's test environment.
#
# Overridable by env var so staging/preview revisions can point at a different
# caller without a code change.
KILBY_SERVICE_ACCOUNT = os.getenv(
    "PARTNER_ALLOWED_CALLER",
    "cloud-run-chat@chatapp-488502.iam.gserviceaccount.com",
).strip()

# Tolerance for clock drift between Google's signing time and this server. Cloud
# Run clocks are accurate, so 60s is ample in production; the override exists for
# development machines whose clocks have drifted (a laptop 2.5 minutes slow will
# otherwise reject every valid token as "used too early").
try:
    _CLOCK_SKEW = max(0, int(os.getenv("PARTNER_CLOCK_SKEW_SECONDS", "60")))
except ValueError:
    _CLOCK_SKEW = 60

# Shown to anyone who reaches a protected endpoint without credentials. The
# partner API is not open to third parties yet, but it is not a secret either —
# partner_api.py is in a public repository. Saying so plainly, and pointing at
# what IS open, is better than a blank refusal.
_CLOSED_DOOR_MESSAGE = (
    "This endpoint serves approved partner systems and is not open to third "
    "parties. TEK2day Finance is free and open source - see /docs for the "
    "public API."
)

# The ticker used to probe data freshness. Liquid, always-covered, and cheap:
# one Firestore read of a single price document.
_FRESHNESS_PROBE_SYMBOL = "AAPL"


def _request_id() -> str:
    return "t2d_" + uuid.uuid4().hex[:24]


def require_kilby(request: Request) -> str:
    """Verify the caller is Kilby. Returns the caller's identity, or refuses.

    Kilby's backend attaches a Google-signed identity token; Google's library
    checks the signature, expiry and issuer, and we then check the identity is
    the one account we trust. Nothing is trusted that Google has not signed —
    a header claiming to be Kilby proves nothing on its own.

    Same mechanism auth.py already uses to verify Firebase sign-ins, pointed at
    a service account instead of a person.

    Note this is NOT about protecting the data — TEK2day Finance is free and open
    source. It identifies Kilby so its traffic gets its own rate-limit bucket
    rather than tripping the public per-IP limiter from a few Cloud Run egress
    IPs, and so its usage is attributable in logs.
    """
    from google.auth.transport import requests as google_requests  # noqa: PLC0415
    from google.oauth2 import id_token  # noqa: PLC0415

    token = str(request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    if not token:
        # A locked door with directions on it. Anyone who finds this endpoint is
        # told what it is and where the open data lives, rather than hitting a
        # bare refusal — TEK2day Finance is free and open source, so there is
        # nothing to be coy about.
        raise HTTPException(status_code=401, detail=_CLOSED_DOOR_MESSAGE)

    try:
        claims = id_token.verify_oauth2_token(
            token, google_requests.Request(), clock_skew_in_seconds=_CLOCK_SKEW
        )
    except Exception as exc:  # bad signature, expired, malformed
        raise HTTPException(status_code=401, detail="Invalid credential") from exc

    # Identity tokens omit the email claim unless it is explicitly requested —
    # gcloud needs --include-email, and the Cloud Run metadata server needs
    # format=full. Without this branch a caller doing it wrong gets an opaque
    # "not authorised" and no idea why, so say exactly what is missing.
    if "email" not in claims:
        raise HTTPException(
            status_code=403,
            detail="Token carries no email claim; request a full-format identity token",
        )

    caller = str(claims.get("email") or "")
    # Google states whether it verified the address itself. Without this check a
    # token carrying an unverified address would pass on the name alone.
    if caller != KILBY_SERVICE_ACCOUNT or not claims.get("email_verified"):
        raise HTTPException(status_code=403, detail="Caller not authorised")

    return caller


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


@router.get("/health", include_in_schema=False)
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


@router.get("/whoami", include_in_schema=False)
def partner_whoami(request: Request) -> dict:
    """Confirm the caller's identity. Exists to prove authentication works.

    Returns who Google says you are and nothing else — no market data — so it is
    safe to call from anywhere while testing the gate.
    """
    caller = require_kilby(request)
    return {
        "api_version": API_VERSION,
        "request_id": _request_id(),
        "checked_at": _now_iso(),
        "authenticated": True,
        "caller": caller,
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
