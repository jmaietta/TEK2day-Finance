#!/usr/bin/env python3
"""Tests for /partner/v1/symbols/resolve.

Runs with plain python, no pytest, no network, no Firestore:

    python test_partner_symbols.py

Firestore and Yahoo are both stubbed, so this is safe to run anywhere and
cannot touch production. The point is to pin the three outcomes apart —
covered, not covered, and no such ticker — because every other partner
endpoint trusts the symbol this one hands it.
"""
import json
import sys

from testkit import check, run_all

import envelope
import partner_api
import storage

# ── harness ──────────────────────────────────────────────────────────────────

class FakeRequest:
    headers: dict = {}


def call(symbol):
    """Invoke the endpoint and normalise both return shapes to (status, body)."""
    result = partner_api.resolve_symbol(FakeRequest(), symbol=symbol)
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


# ── stubs ────────────────────────────────────────────────────────────────────

UNIVERSE = {
    "AAPL": {"symbol": "AAPL", "name": "Apple Inc.", "sector": "Technology", "active": True},
    "MSFT": {"symbol": "MSFT", "name": "Microsoft Corporation", "sector": "Technology", "active": True},
    "NVDA": {"symbol": "NVDA", "name": "NVIDIA Corporation", "sector": "Technology", "active": True},
    "AMZN": {"symbol": "AMZN", "name": "Amazon.com, Inc.", "sector": "Consumer Cyclical", "active": True},
    "GOOGL": {"symbol": "GOOGL", "name": "Alphabet Inc.", "sector": "Communication Services", "active": True},
    "GOOG": {"symbol": "GOOG", "name": "Alphabet Inc.", "sector": "Communication Services", "active": True},
    "JPM": {"symbol": "JPM", "name": "JPMorgan Chase & Co.", "sector": "Financial Services", "active": True},
    "XOM": {"symbol": "XOM", "name": "Exxon Mobil Corporation", "sector": "Energy", "active": True},
    "JNJ": {"symbol": "JNJ", "name": "Johnson & Johnson", "sector": "Healthcare", "active": True},
    "WMT": {"symbol": "WMT", "name": "Walmart Inc.", "sector": "Consumer Defensive", "active": True},
    "BRK.B": {"symbol": "BRK.B", "name": "Berkshire Hathaway Inc. New", "sector": "Financial Services", "active": True},
    "BF.B": {"symbol": "BF.B", "name": "Brown Forman Inc", "sector": "Consumer Defensive", "active": True},
}

GOLDEN_TEN = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "JPM", "XOM", "JNJ", "WMT", "BRK.B"]

# Symbols Yahoo has that TEK2day does not — the new-listing case.
YAHOO_ONLY = {"NEWCO"}

yahoo_calls = []
firestore_raises = False


def fake_get_ticker_meta(symbol):
    if firestore_raises:
        raise RuntimeError("Firestore unavailable")
    return UNIVERSE.get(symbol)


def fake_live_quote(symbol):
    yahoo_calls.append(symbol)
    return {"price": 1.0 if symbol in YAHOO_ONLY else None}


def install_stubs():
    storage.get_ticker_meta = fake_get_ticker_meta
    partner_api.require_kilby = lambda request: "test@example.com"
    # _yahoo_knows imports terminal lazily, so put the stub where it will look.
    import types
    fake_terminal = types.ModuleType("terminal")
    fake_terminal._live_quote = fake_live_quote
    sys.modules["terminal"] = fake_terminal


def reset():
    global firestore_raises
    firestore_raises = False
    yahoo_calls.clear()
    partner_api._unknown_symbols.clear()


# ⚠️ PYTEST NEVER CALLS main(), SO IT NEVER CALLED install_stubs().
#
# This file is a standalone script; `python test_partner_symbols.py` passes
# 126/126. Under pytest it ran with NO stubs — no fake Firestore, no fake auth —
# and failed 13 of 14 in isolation. In the full suite only 3 failed, because
# five OTHER test modules assign `partner_api.require_kilby` at import and that
# patch leaks across the session. So ten of these tests were passing on a stub
# some unrelated file happened to leave lying around.
#
# ⚠️ AND THE STUBS MUST BE TORN DOWN. install_stubs() puts a FAKE `terminal`
# into sys.modules; leaving it there would poison every module imported after
# it — the same cross-file contamination this fixture exists to end. Install,
# yield, restore.
try:  # pragma: no cover - absent when run as a plain script
    import pytest as _pytest
except ImportError:  # pragma: no cover
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(autouse=True)
    def _stubs_under_pytest():
        saved_meta = storage.get_ticker_meta
        saved_auth = partner_api.require_kilby
        saved_terminal = sys.modules.get("terminal")
        install_stubs()
        try:
            yield
        finally:
            storage.get_ticker_meta = saved_meta
            partner_api.require_kilby = saved_auth
            if saved_terminal is None:
                sys.modules.pop("terminal", None)
            else:
                sys.modules["terminal"] = saved_terminal


# ── tests ────────────────────────────────────────────────────────────────────

def test_golden_ten_resolve():
    """The gate for this step: every golden-ten symbol resolves."""
    reset()
    for sym in GOLDEN_TEN:
        status, body = call(sym)
        check(f"golden ten {sym} resolves", status == 200, f"got {status}")
        check(f"golden ten {sym} coverage", body.get("data", {}).get("coverage") == "covered")
        check(f"golden ten {sym} symbol", body.get("data", {}).get("symbol") == sym)
        check(f"golden ten {sym} name", bool(body.get("data", {}).get("name")))
        check(f"golden ten {sym} no Yahoo call", sym not in yahoo_calls,
              "a covered symbol must never reach Yahoo")


def test_envelope_shape():
    reset()
    _, body = call("NVDA")
    check("dataset", body.get("dataset") == "symbol_resolution")
    check("request_id prefix", str(body.get("request_id", "")).startswith("t2d_"))
    check("api_version", body.get("api_version") == envelope.API_VERSION)
    check("platform", body.get("provenance", {}).get("platform") == "TEK2day Finance")
    check("quality ok", body.get("quality", {}).get("status") == "ok")
    check("no warnings on a clean resolve", body.get("quality", {}).get("warnings") == [])
    check("requested echoed", body.get("requested") == {"symbol": "NVDA"})
    check("resolved carries name", body.get("resolved", {}).get("name") == "NVIDIA Corporation")


def test_normalisation():
    reset()
    for given in ("nvda", " nvda ", "NvDa"):
        status, body = call(given)
        check(f"{given!r} normalises to NVDA", status == 200 and body["data"]["symbol"] == "NVDA")
        check(f"{given!r} echoes what was asked", body["requested"]["symbol"] == given)


def test_dotted_tickers():
    """The only two tickers in the universe that contain a dot."""
    reset()
    for sym in ("BRK.B", "BF.B"):
        status, body = call(sym)
        check(f"{sym} resolves", status == 200 and body["data"]["symbol"] == sym)


def test_google_pair_are_distinct_tickers():
    """GOOG and GOOGL each resolve to themselves. There is no name lookup, so
    there is nothing to disambiguate — the caller names the ticker it wants."""
    reset()
    _, goog = call("GOOG")
    _, googl = call("GOOGL")
    check("GOOG resolves to GOOG", goog["data"]["symbol"] == "GOOG")
    check("GOOGL resolves to GOOGL", googl["data"]["symbol"] == "GOOGL")
    check("neither is substituted for the other",
          goog["data"]["symbol"] != googl["data"]["symbol"])


def test_company_name_is_not_a_ticker():
    """No company-name command exists, so a name is just an unknown symbol."""
    reset()
    status, body = call("GOOGLE")
    check("GOOGLE is 404", status == 404, f"got {status}")
    check("GOOGLE error code", body.get("error") == "symbol_not_found")
    check("GOOGLE returns no data key", "data" not in body,
          "an error must carry nothing renderable")


def test_typo_is_not_a_company():
    reset()
    status, body = call("APPL")
    check("APPL is 404", status == 404, f"got {status}")
    check("APPL never becomes AAPL", "AAPL" not in json.dumps(body))


def test_malformed_symbols():
    reset()
    for bad in ("", " ", "1NVDA", "NV DA", "NVDA!", "TOOLONGSYMBOL123", "../etc/passwd"):
        status, _ = call(bad)
        check(f"malformed {bad!r} refused", status == 404, f"got {status}")
    check("malformed input never reached Yahoo", yahoo_calls == [], str(yahoo_calls))


def test_new_listing_falls_back_to_yahoo():
    reset()
    status, body = call("NEWCO")
    check("new listing answers 200", status == 200, f"got {status}")
    check("marked not covered", body["data"]["coverage"] == "not_covered")
    check("no name invented", body["data"]["name"] is None)
    check("source names Yahoo", body["data"]["source"] == "Yahoo Finance")
    check("carries a warning", body["quality"]["status"] == "warning")
    notes = [w["note"] for w in body["quality"]["warnings"]]
    check("note names the platform", notes and "TEK2day Finance" in notes[0], str(notes))
    check("note is terse", notes and len(notes[0]) < 60, str(notes))


def test_unknown_symbol_is_negatively_cached():
    """A mistyped ticker must cost ONE Yahoo call, not one per request.
    Yahoo also feeds the daily price pull, so being throttled here breaks
    ingestion elsewhere."""
    reset()
    for _ in range(25):
        call("ZZZZ")
    check("unknown symbol hit Yahoo once", yahoo_calls.count("ZZZZ") == 1,
          f"called {yahoo_calls.count('ZZZZ')} times")


def test_known_yahoo_symbol_is_not_negatively_cached():
    reset()
    call("NEWCO")
    call("NEWCO")
    check("a real listing is not cached as missing", yahoo_calls.count("NEWCO") == 2,
          f"called {yahoo_calls.count('NEWCO')} times")


def test_firestore_failure_does_not_fall_back(monkey=None):
    """FS8: if our own backend is down, say so. Never answer from Yahoo and
    present it as ours."""
    global firestore_raises
    reset()
    firestore_raises = True
    try:
        call("NVDA")
        check("Firestore failure raises", False, "no exception")
    except Exception as exc:
        check("Firestore failure is 503", getattr(exc, "status_code", None) == 503,
              f"got {exc!r}")
    check("Firestore failure never called Yahoo", yahoo_calls == [], str(yahoo_calls))
    firestore_raises = False


def test_symbol_integrity_mismatch():
    """FS1: a record whose symbol disagrees with its key is a refusal, not a
    payload. Answering about the wrong company is unforgivable."""
    reset()
    UNIVERSE["TRAP"] = {"symbol": "OTHER", "name": "Some Other Co", "active": True}
    try:
        status, body = call("TRAP")
        check("mismatch is 409", status == 409, f"got {status}")
        check("mismatch is an integrity error", body.get("error") == "symbol_integrity")
        check("mismatch carries no data", "data" not in body)
    finally:
        del UNIVERSE["TRAP"]


def test_no_cross_symbol_bleed():
    """Alternating symbols must not contaminate each other."""
    reset()
    for _ in range(10):
        _, a = call("NVDA")
        _, b = call("MSFT")
        check("NVDA stays NVDA", a["data"]["symbol"] == "NVDA")
        check("MSFT stays MSFT", b["data"]["symbol"] == "MSFT")
        check("names do not bleed", a["data"]["name"] != b["data"]["name"])


def main():
    return run_all(globals(), setup=install_stubs)


if __name__ == "__main__":
    sys.exit(main())
