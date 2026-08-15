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
import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import envelope
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


# ── symbol resolution ────────────────────────────────────────────────────────
#
# TEK2day has no company-name lookup and is not gaining one: the slash commands
# on both products are /TICKER, never /CompanyName, so a name never reaches here.
# That also means there is nothing to disambiguate — GOOG and GOOGL are two
# tickers a caller names explicitly, not one query with two answers.
#
# What this endpoint is for is FS1. Every other partner endpoint trusts the
# symbol it is handed; this is where that trust is established, once.

# How long "nobody has this symbol" is remembered.
#
# terminal._live_quote caches quotes for 30s but its should_cache requires a
# price, so a MISS is never cached there — deliberately, since a transient Yahoo
# blip should not stick. That is right for the website and wrong here: Kilby
# serves many users from a few egress IPs, so one mistyped ticker can arrive
# repeatedly and each repeat would be a fresh Yahoo call. Yahoo also feeds the
# daily price pull, so being throttled on this path breaks ingestion elsewhere.
#
# An hour is safe because the fact is stable: a symbol no one has heard of does
# not appear mid-afternoon. A genuinely new listing is late by at most an hour.
_UNKNOWN_SYMBOL_TTL_SECONDS = 3600
_unknown_symbols: dict[str, float] = {}
_unknown_symbols_lock = threading.Lock()


def _yahoo_knows(symbol: str) -> bool:
    """Whether Yahoo has a quote for a symbol TEK2day does not hold.

    The point is coverage lag, not a second opinion: a company that listed this
    week is real and quotable before our universe job has added it. Anything this
    finds is reported as NOT covered by TEK2day and carries a note saying so —
    it is never blended into our own data, which is what FS8 forbids.
    """
    now = time.monotonic()
    with _unknown_symbols_lock:
        seen = _unknown_symbols.get(symbol)
        if seen is not None and now - seen < _UNKNOWN_SYMBOL_TTL_SECONDS:
            return False

    # Imported here rather than at module load: terminal.py pulls in the Rich
    # renderer, and this module must stay importable without it.
    import terminal  # noqa: PLC0415

    try:
        # Reuses the quote path the website already uses, so "Yahoo has it" means
        # the same thing on both. Never takes COMMAND_LOCK.
        known = terminal._live_quote(symbol).get("price") is not None
    except Exception:
        # Yahoo being unreachable is not evidence the symbol is fake, so this is
        # not cached as a miss — we simply cannot say, and report not covered.
        return False

    if not known:
        with _unknown_symbols_lock:
            _unknown_symbols[symbol] = now
    return known


def _resolve(symbol: str):
    """Shared symbol resolution. Returns (normalised, meta, coverage) or a
    JSONResponse to return as-is.

    Every data endpoint goes through this, so FS1 is established in exactly one
    place rather than re-implemented per endpoint with slightly different rules.
    """
    requested = {"symbol": symbol}
    norm = envelope.normalize_symbol(symbol)

    if not envelope.valid_symbol(norm):
        return None, None, _not_found(requested, f"Not a valid ticker: {symbol}")

    try:
        meta = storage.get_ticker_meta(norm)
    except Exception:
        raise HTTPException(status_code=503, detail="Symbol lookup unavailable") from None

    if not meta:
        return norm, None, None

    stored = envelope.normalize_symbol(meta.get("symbol") or norm)
    if stored != norm:
        return None, None, JSONResponse(
            status_code=409,
            content=envelope.integrity_error(
                requested, {"symbol": stored}, f"Record for {norm} carries symbol {stored}"
            ),
        )
    return norm, meta, None


def _not_found(requested: dict, detail: str) -> JSONResponse:
    """No such symbol, anywhere. Shaped like envelope.integrity_error so a
    consumer parses every failure the same way."""
    return JSONResponse(
        status_code=404,
        content={
            "api_version": API_VERSION,
            "request_id": _request_id(),
            "error": "symbol_not_found",
            "detail": detail,
            "requested": requested,
            "retrieved_at": _now_iso(),
        },
    )


@router.get("/symbols/resolve")
def resolve_symbol(request: Request, symbol: str = Query(..., min_length=1, max_length=13)):
    """Confirm a ticker exists and return its canonical symbol and name.

    Three outcomes, and they are deliberately different things:
      - held by TEK2day        -> coverage "covered", answer from our universe
      - not held, Yahoo has it -> coverage "not_covered", plus a note. Real
                                  company, we have no data for it yet.
      - nobody has it          -> 404. A typo is not a company.
    """
    require_kilby(request)
    requested = {"symbol": symbol}
    norm, meta, refusal = _resolve(symbol)
    if refusal is not None:
        return refusal

    if meta:
        return envelope.build(
            "symbol_resolution",
            {
                "symbol": norm,
                "name": meta.get("name"),
                "sector": meta.get("sector") or None,
                "coverage": "covered",
                "active": bool(meta.get("active")),
                "source": envelope.PLATFORM,
            },
            requested,
            {"symbol": norm, "name": meta.get("name")},
        )

    if _yahoo_knows(norm):
        return envelope.build(
            "symbol_resolution",
            {
                "symbol": norm,
                "name": None,
                "sector": None,
                "coverage": "not_covered",
                "active": None,
                "source": "Yahoo Finance",
            },
            requested,
            {"symbol": norm, "name": None},
            # Terse by rule: name the platform, state the fact, stop.
            warnings=[{"code": "not_covered", "note": "Not covered by TEK2day Finance."}],
        )

    return _not_found(requested, f"No such ticker: {norm}")


# ── company summary ──────────────────────────────────────────────────────────
#
# Built from terminal._market_snapshot(), NOT from app._summary_payload().
#
# They carry the same figures from the same reads, but _summary_payload is the
# WEBSITE's payload: every value has been through terminal._price / _dollar /
# _count / _ratio, which return display strings ("$182.45", "4.32T", "28.4x")
# and the literal string "N/A" when a value is missing. Handing those to a
# partner would break three failsafes at once -- "N/A" is not null (FS7),
# "4.32T" bakes in a scale the envelope separately declares as `units` (FS4),
# and two decimals in trillions loses about $5bn of market cap.
#
# _market_snapshot returns the same numbers raw. Same data logic, same reads,
# no extra Yahoo traffic -- it just skips the browser formatting.

# Which figures are computed at request time from the live quote, and which come
# out of storage. A consumer that cannot tell them apart will assume the whole
# response is as fresh as its freshest field.
_LIVE_FIELDS = (
    "price", "change", "change_pct", "volume", "market_cap", "enterprise_value",
    "pe_ttm", "forward_pe", "ps_ttm", "ev_revenue", "ev_ebitda", "ev_opcf", "ev_fcf",
)


def _estimates(symbol: str) -> dict | None:
    """Consensus EPS and revenue estimates, raw.

    Built from `terminal._estimate_history` rather than `app._estimates_payload`
    for the same reason the rest of this endpoint is: that builder formats for
    the browser, turning growth into "12.40%" and revenue into "91.8B" and
    missing values into the string "N/A".

    ⚠️ THE PERIOD KEYS ARE ROLLING, NOT FIXED. `0q` means "the current quarter"
    at the moment of reading, not a specific quarter. When a company reports,
    every key shifts down one and the numbers change wholesale — that is a
    ROLLOVER, not analysts revising their views. A consumer comparing today's
    `0q` against last week's `0q` across a report date is comparing two
    different quarters and will call it a revision. Hence `rolling: true` and
    the labels travelling with the values.
    """
    try:
        import terminal  # noqa: PLC0415
        history = terminal._estimate_history(symbol)
    except Exception:
        return None
    if not history:
        return None

    record = history[0]
    out: dict = {}
    for prefix, name in (("eps", "eps"), ("rev", "revenue")):
        metrics = {
            key[len(prefix) + 1:]: value
            for key, value in record.items()
            if key.startswith(f"{prefix}_")
        }
        if not metrics:
            continue
        section: dict = {}
        for metric, by_period in metrics.items():
            if not isinstance(by_period, dict):
                continue
            section[metric] = {
                period: envelope.clean(value) for period, value in by_period.items()
            }
        if section:
            out[name] = section

    if not out:
        return None

    out["periods"] = {
        code: terminal.PERIOD_LABELS.get(code, code) for code in terminal.PERIOD_ORDER
    }
    out["rolling"] = True
    out["note"] = "Period keys are relative to today and shift when a company reports."
    return out


@router.get("/equities/{symbol}/summary")
def equity_summary(request: Request, symbol: str):
    """Company overview: quote, valuation and trailing fundamentals.

    Anything price-derived is computed NOW from a live quote -- price, market
    cap, enterprise value, P/E and the EV multiples. Stored prices are
    yesterday's close and exist for history and charts only.
    """
    require_kilby(request)
    requested = {"symbol": symbol}
    norm, meta, refusal = _resolve(symbol)
    if refusal is not None:
        return refusal

    if meta is None:
        # Same three-way distinction /symbols/resolve makes. A ticker we do not
        # cover is not an error and is not a company we can describe.
        if _yahoo_knows(norm):
            return envelope.build(
                "company_summary", None, requested, {"symbol": norm, "name": None},
                warnings=[{"code": "not_covered",
                           "note": "Not covered by TEK2day Finance."}],
            )
        return _not_found(requested, f"No such ticker: {norm}")

    import terminal  # noqa: PLC0415

    try:
        snap = terminal._market_snapshot(norm)
    except Exception:
        raise HTTPException(status_code=503, detail="Summary unavailable") from None

    if not snap:
        return _not_found(requested, f"No data held for {norm}")

    # FS1 again, on the way OUT. _market_snapshot echoes the symbol it was given,
    # so this catches a mix-up between the lookup and the build rather than
    # trusting that nothing moved in between.
    if envelope.normalize_symbol(snap.get("symbol") or "") != norm:
        return JSONResponse(
            status_code=409,
            content=envelope.integrity_error(
                requested, {"symbol": snap.get("symbol")},
                "Snapshot returned a different symbol",
            ),
        )

    data = {
        "symbol": norm,
        "name": meta.get("name") or snap.get("name") or norm,
        "sector": snap.get("sector") or None,
        "industry": snap.get("industry") or None,
        "description": snap.get("summary") or None,
        "quote": {
            "price": snap.get("price"),
            "change": snap.get("change"),
            "change_pct": snap.get("change_pct"),
            "volume": snap.get("volume"),
            "day_high": snap.get("day_high"),
            "day_low": snap.get("day_low"),
            "fifty_two_week_high": snap.get("fifty_two_week_high"),
            "fifty_two_week_low": snap.get("fifty_two_week_low"),
        },
        "estimates": _estimates(norm),
        "valuation": {
            "market_cap": snap.get("market_cap"),
            "enterprise_value": snap.get("enterprise_value"),
            "pe_ttm": snap.get("pe_ttm"),
            "forward_pe": snap.get("forward_pe"),
            "ps_ttm": snap.get("ps_ttm"),
            "ev_revenue": snap.get("ev_revenue"),
            "ev_ebitda": snap.get("ev_ebitda"),
            "ev_opcf": snap.get("ev_opcf"),
            "ev_fcf": snap.get("ev_fcf"),
        },
        "fundamentals": {
            "revenue": snap.get("revenue"),
            "ebitda": snap.get("ebitda"),
            "net_income": snap.get("net_income"),
            "eps_ttm": snap.get("eps_ttm"),
            "forward_eps": snap.get("forward_eps"),
            "diluted_shares": snap.get("shares"),
            "beta": snap.get("beta"),
            "dividend_yield": snap.get("dividend_yield"),
        },
        # FS5: the description travels WITH the value. "Share count" is five
        # different valid numbers for MSFT, spread ~27m shares (~$14bn of market
        # cap), so the one we mean has to be stated rather than assumed.
        "definitions": {
            "diluted_shares": "Diluted Average Shares, most recent quarter, raw count",
            "eps_ttm": "Net income / diluted average shares, trailing twelve months",
            "market_cap": "Live price x diluted average shares, computed at request time",
            "enterprise_value": "Market cap + total debt - cash",
        },
        "basis": {
            "live": list(_LIVE_FIELDS),
            "note": "Live fields are computed at request time; all others are stored.",
        },
    }

    return envelope.build(
        "company_summary", data, requested,
        {"symbol": norm, "name": data["name"]},
        live=True,
    )


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
