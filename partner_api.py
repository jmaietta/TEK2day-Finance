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
import logging
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import envelope
import storage

router = APIRouter(prefix="/partner/v1", tags=["partner"])

# A wrong-company answer is the one failure an institutional user cannot
# forgive, so when FS1 discards one it must leave a trace for HIM — never for
# the caller, who is told nothing beyond the figure being unavailable.
logger = logging.getLogger("tek2day.partner")

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


def _display(data: dict) -> dict:
    """The same figures, formatted once, by us.

    A partner that formats our numbers itself has to reimplement our rules —
    when a figure becomes T rather than B, how many decimals a ratio gets, the
    "x" suffix. The day those drift, two products show the same company
    differently, which is precisely the problem this endpoint exists to fix.
    Market cap disagreeing by $40bn was a definition problem; market cap
    disagreeing by a rounding rule would be a pointless one.

    So: raw values for arithmetic, these for rendering. A consumer keeps every
    decision about colour, type and layout, and gives up only how many decimals
    a trillion gets.

    Uses the WEBSITE's own formatters, so a number rendered here is
    character-for-character what finance.tek2dayholdings.com shows.

    Missing stays null rather than becoming the string "N/A" — how to draw an
    absent value is the consumer's call (Kilby draws an em dash), and a string
    there would be indistinguishable from a real one.
    """
    import terminal  # noqa: PLC0415

    def fmt(value, formatter):
        if not envelope.finite(value):
            return None
        try:
            return formatter(value)
        except Exception:
            return None

    def fmt_estimates(estimates):
        """Estimates render too, or the drift just moves to the estimates table.

        Metric-dependent: revenue figures are dollars in the billions, EPS is a
        per-share price, growth is a FRACTION and does multiply by 100 (NVDA's
        0.4257 is the "roughly 43%" figure), and analyst counts are integers.
        """
        if not estimates:
            return None
        out = {}
        for section in ("eps", "revenue"):
            block = estimates.get(section)
            if not isinstance(block, dict):
                continue
            rendered = {}
            for metric, by_period in block.items():
                if not isinstance(by_period, dict):
                    continue
                if metric in ("avg", "high", "low", "yearagorevenue"):
                    f = terminal._dollar if section == "revenue" else terminal._eps
                elif metric == "yearagoeps":
                    f = terminal._eps
                elif metric == "growth":
                    f = terminal._pct
                elif metric == "numberofanalysts":
                    f = lambda v: f"{int(v):,}"
                else:
                    continue  # currency and anything unrecognised stay raw only
                rendered[metric] = {p: fmt(v, f) for p, v in by_period.items()}
            if rendered:
                out[section] = rendered
        return out or None

    quote = data.get("quote") or {}
    valuation = data.get("valuation") or {}
    fundamentals = data.get("fundamentals") or {}

    return {
        "estimates": fmt_estimates(data.get("estimates")),
        "quote": {
            "price": fmt(quote.get("price"), terminal._price),
            "change": fmt(quote.get("change"), terminal._price),
            "change_pct": fmt(quote.get("change_pct"), lambda v: f"{v:+.2f}%"),
            "volume": fmt(quote.get("volume"), terminal._count),
            "day_high": fmt(quote.get("day_high"), terminal._price),
            "day_low": fmt(quote.get("day_low"), terminal._price),
            "fifty_two_week_high": fmt(quote.get("fifty_two_week_high"), terminal._price),
            "fifty_two_week_low": fmt(quote.get("fifty_two_week_low"), terminal._price),
        },
        "valuation": {
            "market_cap": fmt(valuation.get("market_cap"), terminal._dollar),
            "enterprise_value": fmt(valuation.get("enterprise_value"), terminal._dollar),
            "pe_ttm": fmt(valuation.get("pe_ttm"), terminal._ratio),
            "forward_pe": fmt(valuation.get("forward_pe"), terminal._ratio),
            "ps_ttm": fmt(valuation.get("ps_ttm"), terminal._ratio),
            "ev_revenue": fmt(valuation.get("ev_revenue"), terminal._ratio),
            "ev_ebitda": fmt(valuation.get("ev_ebitda"), terminal._ratio),
            "ev_opcf": fmt(valuation.get("ev_opcf"), terminal._ratio),
            "ev_fcf": fmt(valuation.get("ev_fcf"), terminal._ratio),
        },
        "fundamentals": {
            "revenue": fmt(fundamentals.get("revenue"), terminal._dollar),
            "ebitda": fmt(fundamentals.get("ebitda"), terminal._dollar),
            "net_income": fmt(fundamentals.get("net_income"), terminal._dollar),
            # Same accounting convention as every other per-share figure.
            "eps_ttm": fmt(fundamentals.get("eps_ttm"), terminal._eps),
            "forward_eps": fmt(fundamentals.get("forward_eps"), terminal._eps),
            "diluted_shares": fmt(fundamentals.get("diluted_shares"), terminal._count),
            "beta": fmt(fundamentals.get("beta"), terminal._num),
            # NOT terminal._pct — that multiplies by 100, and this value is
            # already a percentage. AAPL stores 0.35 meaning 0.35%, which _pct
            # would render as "35.00%", overstating Apple's dividend yield by
            # a hundredfold. Caught by reading the real record rather than
            # trusting the formatter's name.
            "dividend_yield": fmt(fundamentals.get("dividend_yield"), lambda v: f"{v:.2f}%"),
        },
    }


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
        # WHICH TWELVE MONTHS, AND WHICH BALANCE SHEET.
        #
        # Every figure above labelled TTM covers a twelve-month period, and that
        # period does not always end on the latest quarter: Yahoo opens a quarter
        # within hours of a release and fills it in days or weeks later, and
        # until it does the window sits one quarter back. Enterprise value has
        # the same exposure through the balance sheet behind it.
        #
        # Measured 16 Aug 2026: AMZN's TTM ended 31 March while the card implied
        # "now", and ORCL's enterprise value stood on a February balance sheet
        # while Yahoo had May's. Neither was knowable from the response.
        #
        # An institutional user asked "as of when?" deserves an answer, and FS4
        # says a figure is declared rather than inferred. Null means we hold no
        # such period at all — which is why the figure beside it is null too.
        "periods": {
            "ttm_as_of": snap.get("ttm_as_of"),
            "balance_sheet_as_of": snap.get("balance_sheet_as_of"),
        },
        # FS5: the description travels WITH the value. "Share count" is five
        # different valid numbers for MSFT, spread ~27m shares (~$14bn of market
        # cap), so the one we mean has to be stated rather than assumed.
        "definitions": {
            "diluted_shares": "Diluted Average Shares, most recent quarter, raw count",
            "eps_ttm": "Net income / diluted average shares, trailing twelve months",
            "market_cap": "Live price x diluted average shares, computed at request time",
            "enterprise_value": "Market cap + total debt - cash",
            "ttm_as_of": "Last day of the twelve months every TTM figure covers",
            "balance_sheet_as_of": "Period end of the balance sheet behind enterprise value",
        },
        "basis": {
            "live": list(_LIVE_FIELDS),
            "note": "Live fields are computed at request time; all others are stored.",
        },
    }
    # Mirrors the structure above key for key, so a consumer reads
    # data.valuation.market_cap to calculate and display.valuation.market_cap
    # to render, and the two can never describe different numbers.
    data["display"] = _display(data)

    return envelope.build(
        "company_summary", data, requested,
        {"symbol": norm, "name": data["name"]},
        live=True,
    )


# ── financial statements ─────────────────────────────────────────────────────
#
# The largest thing TEK2day gives Kilby. Kilby holds three fundamentals numbers
# and no statements at all — no income statement, no balance sheet, no cash
# flow, no history.

_STATEMENTS = {
    "income": ("income", "Income Statement"),
    "balance_sheet": ("balance_sheet", "Balance Sheet"),
    "cash_flow": ("cash_flow", "Cash Flow"),
}

_STATEMENT_FIELDS = {
    "income": "INCOME_FIELDS",
    "balance_sheet": "BALANCE_FIELDS",
    "cash_flow": "CASHFLOW_FIELDS",
}

# How many periods to return. Quarterly history runs 5-8 periods and annual
# reaches about five years; asking for more than exists is not an error, it is
# simply the coverage we have, and `coverage` in the envelope says so.
_MAX_PERIODS = 8


def _field_pairs(fields):
    """The field lists mix bare names with (key, label) pairs."""
    for entry in fields:
        if isinstance(entry, (list, tuple)):
            yield entry[0], entry[1]
        else:
            yield entry, entry


def _period_kind(key: str) -> str | None:
    """Annual or quarterly, from the DOCUMENT ID PATTERN only.

    FS2. Never from `freq` and never by sorting `period_end`: MSFT's 2026-FY
    and 2026-Q2 BOTH end 2026-06-30, so a date sort can hand back a full year's
    revenue for a quarterly question — a 3.7x error delivered confidently.

    `freq` currently happens to be reliable for annual records (measured: 474 of
    474 carry "FY"), but it is None on ~9% of quarterly ones, and a single
    annual record without it becomes a quarter. The key pattern cannot drift.
    """
    if envelope.QUARTER_RE.fullmatch(key or ""):
        return "quarterly"
    if envelope.ANNUAL_RE.fullmatch(key or ""):
        return "annual"
    return None


@router.get("/equities/{symbol}/financials")
def equity_financials(
    request: Request,
    symbol: str,
    statement: str = Query("income", pattern="^(income|balance_sheet|cash_flow)$"),
    frequency: str = Query("quarterly", pattern="^(quarterly|annual)$"),
):
    """One financial statement, at one frequency, most recent periods first."""
    require_kilby(request)
    requested = {"symbol": symbol, "statement": statement, "frequency": frequency}
    norm, meta, refusal = _resolve(symbol)
    if refusal is not None:
        return refusal
    if meta is None:
        return _not_found(requested, f"No financial statements held for {norm}")

    import terminal  # noqa: PLC0415

    try:
        records = terminal._all_financials(norm) or []
    except Exception:
        raise HTTPException(status_code=503, detail="Financials unavailable") from None

    wanted = [r for r in records if _period_kind(r.get("period") or "") == frequency]
    if not wanted:
        return envelope.build(
            "financial_statement", None, requested, {"symbol": norm, "name": meta.get("name")},
            coverage=envelope.coverage_block([]),
        )

    # Sort by the KEY, not by period_end — for the same reason the split above
    # uses the key. Keys sort correctly as strings: 2025-Q4 < 2026-Q1.
    wanted.sort(key=lambda r: r.get("period") or "")
    selected = wanted[-_MAX_PERIODS:]
    selected.reverse()  # most recent first, the order a reader wants

    section, title = _STATEMENTS[statement]
    fields = getattr(terminal, _STATEMENT_FIELDS[statement])

    rows = []
    for key, label in _field_pairs(fields):
        # The SAME helper the website and the terminal use, so all three
        # surfaces cannot render one company differently.
        values = [terminal.statement_cell(r, section, key) for r in selected]
        # A row nothing reports is noise, not information.
        if not any(envelope.finite(v) for v in values):
            continue
        rows.append({
            "field": key,
            "label": label,
            "values": [envelope.clean(v) for v in values],
            "display": [_fin_display(statement, key, v) for v in values],
        })

    periods = [
        envelope.period_block(r.get("period"), str(r.get("period_end") or "") or None)
        for r in selected
    ]

    data = {
        "symbol": norm,
        "statement": statement,
        "title": title,
        "frequency": frequency,
        "periods": periods,
        "rows": rows,
    }

    # Completeness describes the MOST RECENT period — the one a reader is
    # looking at — rather than the set, which would average away a stub.
    newest = selected[0] if selected else None
    coverage = envelope.coverage_block([r.get("period") for r in wanted])

    return envelope.build(
        "financial_statement", data, requested,
        {"symbol": norm, "name": meta.get("name")},
        record=newest,
        period=periods[0] if periods else None,
        coverage=coverage,
    )


def _fin_display(statement: str, field: str, value):
    """Rendered string for a statement figure, by the same rules the site uses."""
    if not envelope.finite(value):
        return None
    import terminal  # noqa: PLC0415
    try:
        if "EPS" in field:
            # Accounting convention, matching the website: ($x.xx) for a loss.
            return terminal._eps(value)
        return terminal._fin(value) if hasattr(terminal, "_fin") else terminal._dollar(value)
    except Exception:
        return None


# ── comparisons ──────────────────────────────────────────────────────────────
#
# Kilby has no compare tool at all — grepped, there is nothing — so this is one
# of only two things in Phase 3 it genuinely cannot do without TEK2day.

_MAX_COMPARE = 6

# The 15 metrics the website compares on, in its order. `kind` drives both the
# rendered string and how a consumer should right-align it.
_COMPARE_METRICS = [
    ("price",            "Price",             "price"),
    ("market_cap",       "Market Cap",        "money"),
    ("enterprise_value", "EV",                "money"),
    ("revenue",          "Revenue (TTM)",     "money"),
    ("ebitda",           "EBITDA (TTM)",      "money"),
    ("net_income",       "Net Income (TTM)",  "money"),
    ("eps_ttm",          "EPS (TTM)",         "price"),
    ("forward_eps",      "EPS (Fwd)",         "price"),
    ("pe_ttm",           "P/E TTM (GAAP)",    "ratio"),
    ("forward_pe",       "Fwd P/E (Est)",     "ratio"),
    ("ps_ttm",           "P/S (TTM)",         "ratio"),
    ("ev_revenue",       "EV/Rev (TTM)",      "ratio"),
    ("ev_ebitda",        "EV/EBITDA (TTM)",   "ratio"),
    ("ev_opcf",          "EV/OpCF (TTM)",     "ratio"),
    ("ev_fcf",           "EV/FCF (TTM)",      "ratio"),
]


def _compare_display(kind: str, value, field: str = ""):
    if not envelope.finite(value):
        return None
    import terminal  # noqa: PLC0415
    try:
        # A per-share figure follows the accounting convention everywhere it
        # appears — ($5.62) for a loss — so the comp card, the summary card and
        # the income statement cannot render the same company differently.
        if "eps" in field:
            return terminal._eps(value)
        if kind == "price":
            return terminal._price(value)
        if kind == "ratio":
            return terminal._ratio(value)
        return terminal._dollar(value)
    except Exception:
        return None


@router.get("/comparisons")
def comparisons(request: Request, symbols: str = Query(..., min_length=1)):
    """Compare up to six companies on the metrics the website compares on.

    FS1 ON A SET, which is a stronger promise than FS1 on a single symbol.

    Every requested symbol is accounted for in the response, in the order it
    was asked for, whether or not we hold it. A comparison that quietly drops
    one is the dangerous shape: a reader sees a complete-looking table, counts
    columns without thinking, and never notices the company they cared about is
    not in it. So an uncovered symbol comes back as a column marked
    `covered: false` with null values, and `not_covered` lists it plainly.
    """
    require_kilby(request)
    requested_raw = [s for s in str(symbols or "").replace(" ", ",").split(",") if s]
    requested = {"symbols": requested_raw}

    if not requested_raw:
        return _not_found(requested, "No symbols requested")
    if len(requested_raw) > _MAX_COMPARE:
        return JSONResponse(
            status_code=400,
            content={
                "api_version": API_VERSION,
                "request_id": _request_id(),
                "error": "too_many_symbols",
                "detail": f"Maximum {_MAX_COMPARE} symbols at a time; {len(requested_raw)} requested",
                "requested": requested,
                "retrieved_at": _now_iso(),
            },
        )

    # De-duplicate but KEEP ORDER — asking for NVDA twice should not produce two
    # identical columns, and the order asked for is the order to display.
    seen, ordered = set(), []
    for raw in requested_raw:
        norm = envelope.normalize_symbol(raw)
        if norm and norm not in seen:
            seen.add(norm)
            ordered.append(norm)

    def load(norm):
        """Resolve and snapshot one symbol. Never raises — a symbol that cannot
        be loaded is a column marked uncovered, not a failed comparison."""
        if not envelope.valid_symbol(norm):
            return norm, None, None
        try:
            meta = storage.get_ticker_meta(norm)
        except Exception:
            return norm, None, None
        if not meta:
            return norm, None, None
        try:
            import terminal  # noqa: PLC0415
            snap = terminal._market_snapshot(norm)
        except Exception:
            return norm, meta, None

        # FS1 on the way OUT, per company. The summary endpoint does this and a
        # comparison needs it more, not less: a wrong-company column sits inside
        # a table under the right heading, next to companies that ARE right,
        # which is far harder to notice than a single wrong card. Discard it and
        # let the column render as uncovered rather than show another company's
        # numbers under this ticker.
        if snap and envelope.normalize_symbol(snap.get("symbol") or "") != norm:
            logger.warning(
                "comparison symbol mismatch",
                extra={"detail": f"asked={norm} got={snap.get('symbol')}"},
            )
            return norm, meta, None
        return norm, meta, snap

    # Every symbol can normalise away — "\t" survives the split above because
    # only spaces are turned into separators, then normalises to "". That left
    # `ordered` empty and ThreadPoolExecutor(max_workers=0) raises, turning a
    # malformed request into a 500. It is the same answer as no symbols at all.
    if not ordered:
        return _not_found(requested, "No usable symbols requested")

    # Concurrently: each snapshot is a live quote, and six of them in series is
    # slow enough to time a request out.
    with ThreadPoolExecutor(max_workers=len(ordered)) as pool:
        loaded = list(pool.map(load, ordered))

    companies, missing = [], []
    for norm, meta, snap in loaded:
        covered = bool(meta and snap)
        if not covered:
            missing.append(norm)
        companies.append({
            "symbol": norm,
            "name": (meta or {}).get("name") if meta else None,
            "covered": covered,
            # PER COMPANY, not per table — and that is the point. A comparison is
            # read ACROSS, so two columns whose TTM windows end on different
            # dates are not strictly comparable, and nothing else in the response
            # would reveal it. Measured 16 Aug 2026: AMZN's window ended 31 March
            # while MSFT's ended 30 June, because Yahoo had not filled Amazon's
            # June quarter. Both figures were correct; putting them side by side
            # without saying so was not.
            "ttm_as_of": (snap or {}).get("ttm_as_of"),
            "balance_sheet_as_of": (snap or {}).get("balance_sheet_as_of"),
        })

    rows = []
    for key, label, kind in _COMPARE_METRICS:
        values, display = [], []
        for _, _, snap in loaded:
            value = (snap or {}).get(key)
            values.append(envelope.clean(value) if envelope.finite(value) else None)
            display.append(_compare_display(kind, value, key))
        # Unlike a single-company statement, a row is KEPT even when every value
        # is missing: the metric list is the comparison's frame, and a row that
        # vanishes for one set of companies and appears for another makes two
        # comparisons impossible to read against each other.
        rows.append({"field": key, "label": label, "kind": kind,
                     "values": values, "display": display})

    data = {
        "companies": companies,
        "rows": rows,
        "not_covered": missing,
        "definitions": {
            "market_cap": "Live price x diluted average shares, computed at request time",
            "enterprise_value": "Market cap + total debt - cash",
            "ttm_as_of": "Last day of the twelve months this company's TTM figures cover",
            "balance_sheet_as_of": "Period end of the balance sheet behind enterprise value",
        },
    }

    warnings = []
    if missing:
        warnings.append({
            "code": "not_covered",
            "note": "Not covered by TEK2day Finance: " + ", ".join(missing) + ".",
        })

    return envelope.build(
        "comparison", data, requested,
        {"symbols": [c["symbol"] for c in companies]},
        live=True,
        warnings=warnings,
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
