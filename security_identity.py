"""Reviewed security continuity. CIK is a registrant attribute, never a join key.

Registry additions require primary event review and explicit record bindings.
This module performs no discovery, HTTP requests or automatic corporate actions.
The production registry is loaded from Python so all existing images include it.
"""
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
import re


class IdentityError(ValueError):
    pass


class RetiredSymbol(IdentityError):
    pass


def portable(value):
    """Lossless JSON encoding of supported Firestore values, including NaN."""
    if isinstance(value, datetime):
        return {"__firestore_timestamp__": value.isoformat()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"__nonfinite__": str(value)}
    if isinstance(value, dict):
        return {k: portable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [portable(v) for v in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise IdentityError("unsupported Firestore value; do not discard it")


def digest(value):
    return hashlib.sha256(json.dumps(portable(value), sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def require(condition, reason):
    if not condition:
        raise IdentityError(reason)


# Internal IDs are assigned here, not derived from a ticker or CIK. These are
# TEK2day IDs, not externally registered identifiers or Kilby's fictional IDs.
BNY_EVENT = {
    "event_id": "bny-common-2026-05-21",
    "event_type": "ticker_rename",
    "issuer_id": "issuer-85cc7016-376a-42d5-9f90-8bf7711f64bb",
    "security_id": "security-d8ce554c-6d7b-4bba-9846-559a7e3c7b93",
    "registrant_name": "The Bank of New York Mellon Corporation",
    "from": {"symbol": "BK", "cik": "0001390777", "share_class": "common_stock_0.01_par",
             "cusip": "064058100", "exchange_mic": "XNYS", "currency": "USD",
             "instrument_type": "common_stock", "adr_ratio": None, "share_basis": "unchanged"},
    "to": {"symbol": "BNY", "cik": "0001390777", "share_class": "common_stock_0.01_par",
           "cusip": "064058100", "exchange_mic": "XNYS", "currency": "USD",
           "instrument_type": "common_stock", "adr_ratio": None, "share_basis": "unchanged"},
    "announced_on": "2026-05-11", "effective_on": "2026-05-21",
    "announced_at": None, "effective_at": None, "date_precision": "day",
    "sources": [{
        "publisher": "BNY", "document": "BNY Announces Planned Change of Stock Ticker Symbol to BNY",
        "url": "https://www.bny.com/corporate/global/en/about-us/newsroom/press-release/bny-announces-planned-change-of-stock-ticker-symbol-to-bny-130465.html",
        "published_on": "2026-05-11", "published_at": None,
        "passage": "The change in ticker symbols will not affect the company's legal name, capital structure, CUSIPs or the rights of securityholders.",
        "content_sha256": "9b722f4bc8fafe4f136c9d737e39215235e3a0bb3a0563bf97123eb635110fc6",
    }, {
        "publisher": "OCC", "document": "Information Memo 58945", "published_on": "2026-05-12",
        "url": "https://infomemo.theocc.com/infomemos?number=58945",
        "passage": "100 The Bank of New York Mellon Corporation (BNY) Common Shares; CUSIP: 064058100",
        "capture": "Browser PDF text reviewed; direct download returned HTTP 403; no raw-file digest asserted",
    }, {
        "publisher": "SEC", "document": "8-K cover, accession 0001193125-26-314242",
        "url": "https://www.sec.gov/Archives/edgar/data/1390777/000119312526314242/R1.htm",
        "report_on": "2026-07-22", "published_on": None,
        "passage": "Common Stock, $0.01 par value; BNY; NYSE; 0001390777",
        "passage_format": "cover-table transcription",
    }, {
        "publisher": "BNY", "document": "Effective-day company announcement",
        "url": "https://www.linkedin.com/posts/bnyglobal_announcement-bny-common-stock-is-trading-activity-7463211196343586816-nNu_",
        "published_on": "2026-05-21", "published_at": None,
        "passage": "BNY common stock is trading under the new ticker symbol, $BNY, effective today, May 21, 2026.",
    }],
    "currency_evidence": "USD in captured BK metadata and estimates, corroborated by Yahoo BNY chart metadata (NYSE/NYQ, EQUITY), regularMarketTime 1788984002. Currency is provider-observed, not stated in the company rename announcement.",
}

REVIEWED_EVENTS = (BNY_EVENT,)

# A release can invalidate earnings inputs before the periodic SEC filing is
# available. This dated primary observation is independent of migration time.
REVIEWED_EARNINGS_EVENTS = ({
    "event_id": "bny-results-2026-q2", "issuer_id": BNY_EVENT["issuer_id"],
    "period_end": "2026-06-30", "released_on": "2026-07-15", "released_at": None,
    "source_url": "https://www.bny.com/content/dam/bnymellon/documents/pdf/investor-relations/earnings/earnings-press-release-2q-2026.pdf",
},)


def earnings_requirement(symbol, as_of=None):
    event = event_for(symbol)
    if event is None:
        return None
    day = as_of or date.today().isoformat()
    releases = [r for r in REVIEWED_EARNINGS_EVENTS if r["issuer_id"] == event["issuer_id"] and r["released_on"] <= day]
    return max(releases, key=lambda r: r["period_end"]) if releases else None


def freshness_warnings(symbol, *, earnings_period=None, estimate=None, check_estimates=False):
    release = earnings_requirement(symbol)
    if release is None:
        return []
    out = []
    if not earnings_period or earnings_period < release["period_end"]:
        out.append({"code": "earnings_precede_released_results",
                    "note": f"Released results cover {release['period_end']}; stored earnings inputs cover {earnings_period or 'no verified period'}."})
    if check_estimates:
        record = estimate or {}
        horizons = record.get("horizons") or {}
        if not horizons:
            out.append({"code": "estimate_horizons_unverified", "note": "Exact estimate target periods were not preserved in this snapshot."})
        if not record.get("date") or record["date"] <= release["released_on"] or any(
                horizons.get(label, "") <= release["period_end"] for label in ("0q", "+1q")):
            out.append({"code": "estimates_not_verified_after_results", "note": "Capture date and explicit quarter horizons do not establish a post-results estimate snapshot."})
        # Retrieval after a release does not establish that the provider revised
        # its estimates in response to it. Preserve this uncertainty separately
        # from whether the maintenance fetch and horizon capture succeeded.
        if record.get("earnings_event_id") != release["event_id"]:
            out.append({"code": "estimate_post_results_provenance_unverified",
                        "note": "Provider incorporation of the released results is unverified; retrieval time and target horizons alone do not prove it."})
    return out


def validate_event(event):
    require(event.get("event_type") == "ticker_rename", "not a simple rename")
    old, new = event["from"], event["to"]
    require(old["symbol"] != new["symbol"], "self alias")
    fields = {"cik", "share_class", "cusip", "exchange_mic", "currency", "instrument_type", "adr_ratio", "share_basis"}
    require(set(old) == fields | {"symbol"} and set(new) == set(old), "incomplete identity")
    require(all(old[k] == new[k] for k in fields), "security or registrant changed")
    require(old["instrument_type"] == "common_stock" and old["adr_ratio"] is None
            and old["share_basis"] == "unchanged", "conversion or ADR unsupported")
    require(bool(re.fullmatch(r"\d{10}", old["cik"])), "invalid CIK")
    require(event.get("issuer_id") and event.get("security_id") and event.get("sources"), "unreviewed identity")
    require(date.fromisoformat(event["announced_on"]) <= date.fromisoformat(event["effective_on"]), "event dates")
    for side in (old, new):
        require(bool(re.fullmatch(r"[A-Z][A-Z0-9.-]{0,12}", side["symbol"])), "invalid symbol")
    return event


def event_for(symbol, events=None):
    events = REVIEWED_EVENTS if events is None else events
    matches = []
    for event in events:
        validate_event(event)
        if symbol in (event["from"]["symbol"], event["to"]["symbol"]):
            matches.append(event)
    require(len(matches) <= 1, "ambiguous, reused or chained ticker")
    if matches:
        symbols = {matches[0]["from"]["symbol"], matches[0]["to"]["symbol"]}
        require(sum(bool(symbols & {e["from"]["symbol"], e["to"]["symbol"]}) for e in events) == 1,
                "cycle, chain or reused ticker")
    return deepcopy(matches[0]) if matches else None


def route_path(event):
    return "security_routes/" + event["event_id"]


def version_path(event, generation):
    require(isinstance(generation, str) and bool(re.fullmatch(r"[0-9a-f]{64}", generation)), "invalid generation")
    return f"security_data/{event['security_id']}/versions/{generation}"


def resolve_state(symbol, event, state, *, public=False, write=False):
    """One reviewed hop. No tombstone following and no trust in caller paths."""
    require(symbol in {event["from"]["symbol"], event["to"]["symbol"]}, "request unrelated to reviewed event")
    if not state or state.get("status") == "rolled_back":
        return symbol, f"tickers/{symbol}"
    require(state.get("event_sha256") == digest(event), "route event mismatch")
    require(state.get("status") in {"staging", "active", "paused"}, "invalid route status")
    if state["status"] in {"active", "paused"}:
        require(date.today().isoformat() > event["effective_on"], "date-only event not unambiguously effective yet")
    if write:
        require(state["status"] == "active", "maintenance paused for migration")
    if state["status"] == "staging":
        return symbol, f"tickers/{symbol}"
    canonical = event["to"]["symbol"]
    if symbol != canonical and not public:
        raise RetiredSymbol(f"{symbol} retired; explicitly request {canonical}")
    path = version_path(event, state.get("generation", ""))
    require(state.get("version_path") == path, "missing or invalid target")
    return canonical, path


def dataset_status(*, quote, earnings, estimates, history, required_period,
                   earnings_event_id, released_on, quote_not_before, as_of,
                   estimate_target_period, history_required_through):
    """Conservative independent freshness; never infer horizons from 0q/0y."""
    def observed(value, floor):
        try:
            a = datetime.fromisoformat(value.replace("Z", "+00:00"))
            b = datetime.fromisoformat(floor.replace("Z", "+00:00"))
            end = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
            return a.tzinfo is not None and b.tzinfo is not None and end.tzinfo is not None and b <= a <= end
        except (AttributeError, TypeError, ValueError):
            return False
    return {
        "quotes": bool(isinstance(quote.get("price"), (int, float)) and not isinstance(quote.get("price"), bool)
                       and math.isfinite(quote["price"]) and quote["price"] > 0
                       and observed(quote.get("observed_at"), quote_not_before)),
        "earnings": bool(earnings.get("period_end") == required_period and required_period <= as_of[:10]
                         and released_on <= as_of[:10] and earnings.get("complete") is True),
        "estimates": bool(estimates.get("target_period_end") == estimate_target_period
                          and estimates.get("earnings_event_id") == earnings_event_id
                          and released_on < str(estimates.get("observed_on") or "") <= as_of[:10]),
        "history": bool(history.get("through") == history_required_through and history_required_through <= as_of[:10]
                        and history.get("complete") is True),
    }
