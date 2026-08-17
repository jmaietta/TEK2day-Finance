"""The response contract for the partner API.

Every partner response carries the same wrapper, so a consumer never has to
infer anything that TEK2day already knows. This module holds no I/O and no
FastAPI: it takes a payload and facts about it, and returns the shape Kilby
reads.

The contract exists because Kilby answers investment professionals. A figure
they cannot place — which period, which currency, how complete, from where — is
a figure they cannot use. Everything here is in service of that.

Two rules worth stating up front, because they are easy to erode later:

1. Warnings and coverage are computed HERE, from the record, at the moment the
   record is read. They are never stored beside a card, never cached apart from
   the numbers they describe, and never written by a language model. A note that
   outlives its cause is worse than no note.

2. Missing is null and says so. It is never zero, and never quietly omitted.
"""
import math
import re
import uuid
from datetime import datetime, timezone

API_VERSION = "1.0.0"

PLATFORM = "TEK2day Finance"

# Upstream per dataset. TEK2day is not single-source, so a filing must not be
# attributed to Yahoo. Mirrors the `source` strings app.py already returns.
UPSTREAM = {
    "company_summary": "Yahoo Finance",
    "financial_statement": "Yahoo Finance",
    "estimates": "Yahoo Finance",
    "prices": "Yahoo Finance",
    "comparison": "Yahoo Finance",
    "news": "Yahoo Finance",
    "filings": "SEC EDGAR",
    "management": "CEORater, Yahoo Finance",
    "macro_snapshot": "FRED, Yahoo Finance",
    "symbol_resolution": None,
}

# Prices are stored with auto_adjust=True, so every price-derived response must
# say so rather than leaving a consumer to assume as-traded values.
ADJUSTED = "split_and_dividend_adjusted"

STATEMENT_SECTIONS = ("income", "balance_sheet", "cash_flow")
QUARTER_RE = re.compile(r"[0-9]{4}-Q[0-9]")
ANNUAL_RE = re.compile(r"[0-9]{4}-FY")

# Completeness, in the four states Kilby must tell apart.
COMPLETE = "complete"      # all three statements populated
PARTIAL = "partial"        # populated, but a check failed
STUB = "stub"              # present but substantially empty
ABSENT = "absent"          # no record at all


def request_id() -> str:
    return "t2d_" + uuid.uuid4().hex[:24]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def finite(value) -> bool:
    """A usable number. Firestore stores Yahoo's NaN verbatim."""
    return isinstance(value, (int, float)) and not (
        isinstance(value, float) and (math.isnan(value) or math.isinf(value))
    )


def clean(value):
    """JSON-safe, and missing stays missing. NEVER coerces to zero."""
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def normalize_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


# The shape of a ticker. Mirrors app.py:202, which the website's command bar has
# used since launch — a partner asking for a symbol must be held to exactly the
# same standard as a person typing one, or the two disagree about what a ticker
# even is. Dots are real here (BRK.B, BF.B); dashes are not used by any ticker we
# hold, but the website accepts them so this does too.
#
# NOTE this is shape only. It says "NVDA" and "APPL" are both well-formed; it
# does NOT say either one exists. Existence is a Firestore lookup, and the two
# checks must stay separate — conflating them is how a typo becomes a company.
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,12}$")


def valid_symbol(symbol: str) -> bool:
    """Whether a string is shaped like a ticker. Not whether we hold it."""
    return bool(SYMBOL_RE.fullmatch(normalize_symbol(symbol)))


# ── period ───────────────────────────────────────────────────────────────────

def period_block(storage_key: str | None, period_end: str | None) -> dict | None:
    """Describe a period without ever guessing which kind it is.

    Frequency comes from the document ID pattern, never from the stored `freq`
    field (absent on most quarterly records) and never from sorting period_end
    (an annual and a quarterly record can share one — MSFT 2026-FY and 2026-Q2
    both end 2026-06-30, a 3.7x error waiting to happen).

    issuer_fiscal_label stays null: the stored Q# is derived from the calendar
    month of period_end and is NOT the issuer's own quarter numbering. AAPL's
    quarter ending September is its fiscal Q4 but stores as 2025-Q3. Filling
    this in needs a fiscal-calendar source TEK2day does not have.
    """
    if not storage_key:
        return None
    if QUARTER_RE.fullmatch(storage_key):
        frequency = "quarterly"
    elif ANNUAL_RE.fullmatch(storage_key):
        frequency = "annual"
    else:
        frequency = None
    return {
        "storage_key": storage_key,
        "period_end": period_end,
        "frequency": frequency,
        "issuer_fiscal_label": None,
        "resolution_status": "exact_key_match",
    }


# ── completeness ─────────────────────────────────────────────────────────────

# The line that makes each statement a statement. A balance sheet without Total
# Assets is not a balance sheet, however many other lines it carries.
#
# Income allows either headline because not every issuer reports "Total Revenue"
# under that name — banks in particular — and net income is universal.
#
# ⚠️ THIS IS THE SAME RULE THE REPAIR JOB USES (proposals.is_stub), imported from
# here so the two cannot drift. One concept: "did the statement arrive?"
# Each entry lists every NAME the same line legitimately arrives under. A
# missing anchor must mean "the statement did not arrive", never "this issuer
# presents it differently".
ANCHOR_FIELDS = {
    # Not every issuer reports "Total Revenue" under that name — banks in
    # particular — and net income is universal.
    "income": ("Total Revenue", "Net Income"),
    "balance_sheet": ("Total Assets",),
    # Companies reporting under the DIRECT method label operating cash flow
    # differently. Measured 17 Aug 2026: CILJF, CYATY and PTXKY are 70-100%
    # populated, complete statements, and were flagged purely because Yahoo
    # names the line "Cash Flowsfromusedin Operating Activities Direct". Foreign
    # issuers using IFRS presentation; the statement is fine, the label differs.
    "cash_flow": (
        "Operating Cash Flow",
        "Cash Flowsfromusedin Operating Activities Direct",
    ),
}


def _section_usable(section: str, block) -> bool:
    """Whether a statement section actually arrived.

    Two tests, both of which a section must pass:

    1. It holds at least one real number. Firestore stores Yahoo's blanks as
       `nan`, not as absent keys, so a section can be full of field NAMES and
       empty of data — which is why counting keys was never enough.
    2. It carries its headline line. This is what catches the case test 1
       cannot: ORCL's quarter ending 2024-11-30 holds two real figures out of
       sixty-six — `Non Current Deferred Taxes Liabilities` and `Non Current
       Deferred Liabilities` — while Total Assets, Total Debt and Cash are all
       blank. Two obscure liabilities are not a balance sheet.

    ⚠️ WHY NOT A PROPORTION OF THE PREVIOUS PERIOD, which was designed, built and
    then abandoned on the evidence (16 Aug 2026). A "kept less than 60% of last
    quarter's fields" rule was measured across 166 companies and changed the
    verdict on two — CMBT and KBON — and BOTH were usable:

        CMBT 2026-Q1 cash flow, 8 fields: Operating, Investing and Financing
        Cash Flow, Free Cash Flow, Beginning and End Cash Position, Changes In
        Cash, FX effect.

    That is a summary cash flow statement, not a skeleton: every headline total
    is present and a reader can use it. HIS RULING: *"we can't afford mistakes
    where summary statements are flagged and withheld."* Marking that `stub`
    makes Kilby say "we hold no usable data for this quarter" about data we
    hold and could show.

    The anchor test catches ORCL outright, passes both summary statements, needs
    no previous period, and has no threshold to tune. Simpler and safer.

    Cannot raise: this runs inside every financials response.
    """
    block = block or {}
    if not isinstance(block, dict):
        return False
    if not any(finite(v) for v in block.values()):
        return False
    return any(finite(block.get(field)) for field in ANCHOR_FIELDS.get(section, ()))


def completeness_block(record: dict | None, coverage: dict | None = None) -> dict:
    """How complete a financial record is, and where its values came from.

    Kilby is TOLD, never left to infer. This matters more now that records
    change: one Kilby saw as empty last week may be populated this week, with a
    warning attached.

    `status` is honest about its own limits — "complete" means as complete as
    our source has. Matching the actual filing needs SEC.gov or a data partner.
    """
    if record is None:
        return {"status": ABSENT, "sections": {}, "source": None, "coverage": coverage}

    sections = {}
    unusable = 0
    for name in STATEMENT_SECTIONS:
        block = record.get(name) or {}
        populated = sum(1 for v in block.values() if finite(v))
        # The counts stay a plain census of what is stored — they were always
        # honest, and they are what a human reads to check this verdict.
        sections[name] = f"{populated}/{len(block)}" if block else "0/0"
        if not _section_usable(name, block):
            unusable += 1

    if unusable:
        # ⚠️ A RECORD THAT EXISTS IS NEVER `absent`, however empty it is.
        # `absent` is defined as "no record at all" and makes Kilby answer
        # "coverage begins March 2025" — false for a company we hold years of.
        # An existing but unusable record is a `stub`: "we hold no usable data
        # for this quarter", which is true and is what the contract's own table
        # says `stub` is for.
        #
        # This is not hypothetical. AMZN's June 2026 quarter is empty here AND
        # empty at Yahoo, so no pull can ever fill it — and Kilby has to answer
        # questions about it today. (His ruling, 16 Aug.)
        status = STUB
    elif record.get("data_warnings"):
        status = PARTIAL
    else:
        status = COMPLETE

    return {
        "status": status,
        "sections": sections,
        # Distinguishes a value from the original pull from one the Data Review
        # populated later. An institutional user asking where a figure came from
        # deserves a real answer.
        "source": "yahoo_backfill" if record.get("backfilled_at") else "original_ingestion",
        "populated_at": record.get("backfilled_at"),
        "coverage": coverage,
    }


def coverage_block(period_keys: list[str], gaps: list[str] | None = None) -> dict:
    """What we hold, so a short series is never presented as a complete one.

    History is deliberately not comprehensive — TEK2day is not Bloomberg,
    FactSet or LSEG. Quarterly runs 5–8 periods; annual reaches about 5 years.
    Stating the floor is the honest alternative to silent truncation.
    """
    keys = sorted(k for k in (period_keys or []) if k)
    return {
        "earliest": keys[0] if keys else None,
        "latest": keys[-1] if keys else None,
        "periods_held": len(keys),
        "gaps": sorted(gaps or []),
    }


# ── warnings ─────────────────────────────────────────────────────────────────

def warnings_from(record: dict | None) -> list[dict]:
    """Notes a partner may show a reader. Deliberately NOT the sanity checks.

    HIS RULE, 2026-08-14, and it is absolute: **a Kilby user must never be told
    there is an error in the system.** Only the owner sees errors. Telling a
    portfolio manager "we do not hold this value" is fine; telling one that a
    check failed, or handing over a diagnostic, is not — it invites them to
    distrust every other number on the page, which no caveat is worth.

    So `data_warnings` STOPS HERE. Those are internal check results written by
    proposals.py (`{"code": "Diluted EPS x shares vs net income", "detail":
    "0m vs 3m (100.0% apart)"}`) — an engineer's diagnostic, in an engineer's
    words, about a check an outsider has no way to interpret. They belong on the
    Data Review page, in the logs and in his email alerts. They are not a
    partner's business and they never leave this function.

    Absence is different and still travels: what we do not hold is reported
    through `completeness`, in plain language, without implying a fault.
    """
    return []


def coverage_note(coverage: dict | None, requested_period: str | None = None) -> str | None:
    """One line, when a request reaches past what we hold. Otherwise nothing."""
    if not coverage or not coverage.get("earliest"):
        return None
    if requested_period and requested_period < coverage["earliest"]:
        return f"Not held. Coverage begins {coverage['earliest']}."
    return None


# ── the envelope ─────────────────────────────────────────────────────────────

def build(
    dataset: str,
    data,
    requested: dict,
    resolved: dict,
    *,
    record: dict | None = None,
    period: dict | None = None,
    coverage: dict | None = None,
    currency: str = "USD",
    scale: str = "units",
    warnings: list[dict] | None = None,
    as_of: str | None = None,
    live: bool = False,
) -> dict:
    """Wrap a payload in the partner contract.

    `live=True` marks a response whose price-derived values are computed at
    request time rather than read from storage — price, market cap, enterprise
    value, P/E and the EV multiples. Anything with price in it must be live;
    stored prices are yesterday's close and are for history and charts only.
    """
    upstream = UPSTREAM.get(dataset)
    notes = list(warnings or [])
    notes.extend(warnings_from(record))

    envelope = {
        "api_version": API_VERSION,
        "request_id": request_id(),
        "dataset": dataset,
        "requested": requested,
        "resolved": resolved,
        "units": {"currency": currency, "scale": scale},
        "as_of": as_of or now_iso()[:10],
        "retrieved_at": now_iso(),
        "provenance": {
            "platform": PLATFORM,
            "upstream": upstream,
            # Named so a consumer can say which figures are live rather than
            # assuming everything is as fresh as the freshest field.
            "valuation_basis": "live_quote" if live else "stored",
        },
        "quality": {
            "status": "warning" if notes else "ok",
            "warnings": notes,
        },
        "data": clean(data),
    }

    if period is not None:
        envelope["period"] = period
    if record is not None or coverage is not None:
        envelope["completeness"] = completeness_block(record, coverage)
    if dataset in ("prices", "company_summary", "comparison"):
        envelope["adjustment_basis"] = ADJUSTED

    return envelope


def integrity_error(requested: dict, resolved: dict, detail: str) -> dict:
    """The response when a symbol check fails.

    Wrong-company data is the one failure an institutional user cannot forgive,
    so it is an error rather than a plausible-looking payload. Never substitute
    another company, and never fall back to another source.
    """
    return {
        "api_version": API_VERSION,
        "request_id": request_id(),
        "error": "symbol_integrity",
        "detail": detail,
        "requested": requested,
        "resolved": resolved,
        "retrieved_at": now_iso(),
    }
