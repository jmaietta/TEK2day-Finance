#!/usr/bin/env python3
"""Tests for /partner/v1/macro.

    python test_partner_macro.py

No network, no Firestore — app._macro_payload and the Yahoo overlay are stubbed.

WHY THIS ENDPOINT NEEDS ITS OWN TESTS — 21 August 2026.

Two hazards, and neither is visible by reading the response on a good day.

1. THE SNAPSHOT SPEAKS BROWSER. Every cell carries a display string alongside
   its number, and that string is the literal "N/A" when the value is missing.
   Handing "N/A" to a partner breaks FS7 — a consumer cannot tell it from data.
   The value must be a number or null, with the reason stated separately.

2. THE RESPONSE MIXES TWO CLOCKS. Most rows come from a snapshot written twice
   a day. Three rows (^GSPC, ^IXIC, ^VIX) are overlaid from Yahoo at request
   time. A consumer that cannot tell them apart will assume the whole dashboard
   is as fresh as its freshest cell, so every cell declares `live`.

AND THE CACHE IS A YAHOO GUARD, NOT A SPEED TRICK. The overlay is uncached on
the website: one page load, three Yahoo requests. Through a partner API that
would scale with Kilby's user count, and Yahoo throttling TEK2day is
existential. The cache makes that load a function of the clock instead. These
tests pin that a second call inside the TTL does NOT re-fetch, and that
MACRO_PARTNER_TTL_SECONDS=0 still does — a dial that only turns one way is not
a dial.

⚠️ STUBS ARE INSTALLED AND TORN DOWN, ALWAYS. This file puts a fake `app` into
sys.modules, and sys.modules is process-global: leaving it there would poison
every module imported afterwards. test_partner_symbols.py records that exact
bug — ten of its tests were passing on a stub an unrelated file had left lying
around. Hence the context manager and the pytest fixture below.
"""
import contextlib
import sys

from testkit import check, run_all

import envelope
import partner_api


class Req:
    headers: dict = {}


def _cell(value, formatted, *, date="2026-08-20", provider="FRED",
          code="CPIAUCSL", overlay=None, reason=None):
    cell = {
        "formatted": formatted,
        "formatted_value": formatted,
        "numeric_value": value,
        "value": value,
        "source_date": date,
        "source_provider": provider,
        "source_code": code,
        "raw_source_value": None if value is None else str(value),
        "request_params": {"series_id": code} if overlay is None else {"overlay": overlay},
        "method_notes": "test",
    }
    if reason:
        cell["audit_reason"] = reason
    return cell


def _snapshot():
    """A fresh copy per test, so a mutating test cannot leak into the next."""
    return {
        "type": "macro",
        "schema_version": 2,
        "contract_version": "2026-08-21",
        "row_count": 3,
        "asOf": "2026-08-20",
        "generated_at": "2026-08-21T13:09:18.983706+00:00",
        "sections": [
            {
                "id": "market",
                "title": "Market",
                "source": "Yahoo Finance / FRED",
                "columns": ["Latest", "Prior Month", "Prior Year"],
                "items": [
                    {
                        "item_id": "sp500",
                        "label": "S&P 500",
                        "source_provider": "Yahoo Finance",
                        "source_code": "^GSPC",
                        "cells": {
                            "latest": _cell(7641.16, "7,641.16", provider="Yahoo Finance",
                                            code="^GSPC", overlay="live_yahoo_latest"),
                            "prior_month": _cell(7443.28, "7,443.28", date="2026-07-20",
                                                 provider="Yahoo Finance", code="^GSPC",
                                                 overlay="live_yahoo_rebased_comparison"),
                            "prior_year": _cell(6395.78, "6,395.78", date="2025-08-20",
                                                provider="Yahoo Finance", code="^GSPC",
                                                overlay="live_yahoo_rebased_comparison"),
                        },
                    },
                ],
            },
            {
                "id": "rates",
                "title": "Rates",
                "source": "FRED / US Treasury",
                "columns": ["Latest", "Prior Month", "Prior Year"],
                "items": [
                    {
                        "item_id": "treasury_10y",
                        "label": "10Y Treasury Yield",
                        "source_provider": "US Treasury",
                        "source_code": "BC_10YEAR",
                        "cells": {
                            "latest": _cell(4.69, "4.69%", provider="US Treasury",
                                            code="BC_10YEAR"),
                            "prior_month": _cell(4.60, "4.60%", date="2026-07-20",
                                                 provider="US Treasury", code="BC_10YEAR"),
                            "prior_year": _cell(4.29, "4.29%", date="2025-08-20",
                                                provider="US Treasury", code="BC_10YEAR"),
                        },
                    },
                    {
                        "item_id": "t_bill_13w",
                        "label": "13 Week Treasury Bill",
                        "source_provider": "US Treasury",
                        "source_code": "ROUND_B1_CLOSE_13WK_2",
                        "cells": {
                            "latest": _cell(3.71, "3.71%", provider="US Treasury",
                                            code="ROUND_B1_CLOSE_13WK_2"),
                            "prior_month": _cell(None, "N/A", date=None,
                                                 provider="US Treasury",
                                                 code="ROUND_B1_CLOSE_13WK_2",
                                                 reason="No source observation available."),
                            "prior_year": _cell(None, "N/A", date=None,
                                                provider="US Treasury",
                                                code="ROUND_B1_CLOSE_13WK_2",
                                                reason="No source observation available."),
                        },
                    },
                ],
            },
        ],
    }


class _FakeApp:
    """Stands in for app.py. Counts reads so the cache can be proven."""

    def __init__(self, snapshot=None):
        self.snapshot = snapshot if snapshot is not None else _snapshot()
        self.payload_calls = 0
        self.overlay_calls = 0

    def _macro_payload(self):
        self.payload_calls += 1
        return self.snapshot

    def _overlay_live_macro_yahoo_latest(self, snapshot):
        # The real one fetches ^GSPC, ^IXIC and ^VIX from Yahoo. Counting calls
        # here is counting Yahoo requests.
        self.overlay_calls += 1
        return snapshot


_REAL_AUTH = partner_api.require_kilby
_REAL_TTL = partner_api.MACRO_PARTNER_TTL_SECONDS


def _reset_cache():
    partner_api._MACRO_PARTNER_CACHE["data"] = None
    partner_api._MACRO_PARTNER_CACHE["expires"] = 0.0


@contextlib.contextmanager
def _fake_app(ttl=120, module=None):
    """Install a fake `app` module and stubs, then put everything back."""
    fake = module if module is not None else _FakeApp()
    saved_app = sys.modules.get("app")
    partner_api.require_kilby = lambda r: "test"
    partner_api.MACRO_PARTNER_TTL_SECONDS = ttl
    _reset_cache()
    sys.modules["app"] = fake
    try:
        yield fake
    finally:
        if saved_app is None:
            sys.modules.pop("app", None)
        else:
            sys.modules["app"] = saved_app
        partner_api.require_kilby = _REAL_AUTH
        partner_api.MACRO_PARTNER_TTL_SECONDS = _REAL_TTL
        _reset_cache()


try:  # pragma: no cover - absent when run as a plain script
    import pytest as _pytest
except ImportError:  # pragma: no cover
    _pytest = None

if _pytest is not None:
    @_pytest.fixture(autouse=True)
    def _restore_globals():
        """Belt and braces: even a test that throws mid-body leaves no fake behind."""
        saved_app = sys.modules.get("app")
        try:
            yield
        finally:
            if saved_app is None:
                sys.modules.pop("app", None)
            else:
                sys.modules["app"] = saved_app
            partner_api.require_kilby = _REAL_AUTH
            partner_api.MACRO_PARTNER_TTL_SECONDS = _REAL_TTL
            _reset_cache()


def _row(data, item_id):
    for section in data["sections"]:
        for row in section["items"]:
            if row["item_id"] == item_id:
                return row
    raise AssertionError(f"row {item_id} not in response")


# ── raw values, never display strings ────────────────────────────────────────

def test_values_are_numbers_not_strings():
    with _fake_app():
        cell = _row(partner_api.macro(Req())["data"], "treasury_10y")["cells"]["latest"]
    check("latest is a float", isinstance(cell["value"], float), repr(cell["value"]))
    check("latest is the raw number", cell["value"] == 4.69, repr(cell["value"]))


def test_missing_value_is_null_not_the_string_na():
    """⚠️ FS7. The snapshot says "N/A"; a partner must receive null."""
    with _fake_app():
        cell = _row(partner_api.macro(Req())["data"], "t_bill_13w")["cells"]["prior_month"]
    check("value is None", cell["value"] is None, repr(cell["value"]))
    check("value is not the string N/A", cell["value"] != "N/A", repr(cell["value"]))


def test_missing_value_says_why():
    with _fake_app():
        cell = _row(partner_api.macro(Req())["data"], "t_bill_13w")["cells"]["prior_year"]
    check("reason is carried", bool(cell["unavailable_reason"]), repr(cell))


def test_present_value_has_no_unavailable_reason():
    with _fake_app():
        cell = _row(partner_api.macro(Req())["data"], "treasury_10y")["cells"]["latest"]
    check("no reason on a real value", cell["unavailable_reason"] is None, repr(cell))


def test_display_block_matches_the_website():
    with _fake_app():
        row = _row(partner_api.macro(Req())["data"], "treasury_10y")
    check("display is the site's string", row["display"]["latest"] == "4.69%",
          repr(row["display"]))


def test_display_drops_na_to_null():
    """How to draw an absent value is Kilby's call, not a string we invent."""
    with _fake_app():
        row = _row(partner_api.macro(Req())["data"], "t_bill_13w")
    check("N/A becomes null", row["display"]["prior_month"] is None, repr(row["display"]))


# ── the two clocks ───────────────────────────────────────────────────────────

def test_overlaid_cells_are_marked_live():
    with _fake_app():
        cell = _row(partner_api.macro(Req())["data"], "sp500")["cells"]["latest"]
    check("overlaid cell is live", cell["live"] is True, repr(cell))


def test_snapshot_cells_are_not_marked_live():
    """⚠️ Treasury rows are NOT overlaid — the overlay skips any row whose
    latest provider is not Yahoo. Marking them live would tell Kilby a
    twice-daily figure is a real-time one."""
    with _fake_app():
        cell = _row(partner_api.macro(Req())["data"], "treasury_10y")["cells"]["latest"]
    check("snapshot cell is not live", cell["live"] is False, repr(cell))


def test_every_cell_declares_its_own_provider():
    with _fake_app():
        data = partner_api.macro(Req())["data"]
    for section in data["sections"]:
        for row in section["items"]:
            for key, cell in row["cells"].items():
                check(f"{row['item_id']}.{key} names a provider",
                      bool(cell["source_provider"]), repr(cell))


def test_section_source_label_is_passed_through():
    """Kilby draws the panel footer from this; it now reads FRED / US Treasury."""
    with _fake_app():
        data = partner_api.macro(Req())["data"]
    rates = [s for s in data["sections"] if s["title"] == "Rates"][0]
    check("rates source label", rates["source"] == "FRED / US Treasury", repr(rates["source"]))


# ── the cache, which is a Yahoo guard ────────────────────────────────────────

def test_second_call_inside_ttl_does_not_refetch():
    """⚠️ THE POINT OF THE CACHE. Each overlay call is three Yahoo requests."""
    with _fake_app(ttl=120) as fake:
        partner_api.macro(Req())
        partner_api.macro(Req())
        partner_api.macro(Req())
        payload_calls, overlay_calls = fake.payload_calls, fake.overlay_calls
    check("Firestore read once", payload_calls == 1, f"{payload_calls} reads")
    check("Yahoo overlay ran once", overlay_calls == 1, f"{overlay_calls} overlays")


def test_ttl_zero_fetches_every_time():
    """The dial must turn both ways, or it is not a dial."""
    with _fake_app(ttl=0) as fake:
        partner_api.macro(Req())
        partner_api.macro(Req())
        overlay_calls = fake.overlay_calls
    check("overlay ran each time", overlay_calls == 2, f"{overlay_calls} overlays")


# ── envelope contract ────────────────────────────────────────────────────────

def test_response_is_wrapped_in_the_partner_envelope():
    with _fake_app():
        body = partner_api.macro(Req())
    check("dataset is macro", body.get("dataset") == "macro", repr(body.get("dataset")))
    check("as_of is the snapshot date", body.get("as_of") == "2026-08-20",
          repr(body.get("as_of")))


def test_contract_version_is_passed_through():
    with _fake_app():
        data = partner_api.macro(Req())["data"]
    check("contract version", data["contract_version"] == "2026-08-21",
          repr(data["contract_version"]))


def test_row_count_mismatch_raises_a_warning():
    """The macro repo's own audit enforces the row count before writing, so a
    mismatch means the document changed shape underneath us."""
    snapshot = _snapshot()
    snapshot["row_count"] = 24  # three rows are actually present
    with _fake_app(module=_FakeApp(snapshot)):
        body = partner_api.macro(Req())
    quality = body.get("quality") or {}
    codes = [w.get("code") for w in (quality.get("warnings") or [])]
    check("mismatch warned", "row_count_mismatch" in codes, repr(codes))
    # A consumer branches on status, not on reading the notes.
    check("status flips to warning", quality.get("status") == "warning",
          repr(quality.get("status")))


def test_matching_row_count_is_clean():
    """The warning must not cry wolf on a healthy snapshot."""
    with _fake_app():
        body = partner_api.macro(Req())
    quality = body.get("quality") or {}
    codes = [w.get("code") for w in (quality.get("warnings") or [])]
    check("no mismatch warning", "row_count_mismatch" not in codes, repr(codes))
    check("status is ok", quality.get("status") == "ok", repr(quality.get("status")))


def test_unavailable_snapshot_is_a_503_not_an_empty_dashboard():
    """An empty dashboard looks like calm markets. It must look like an outage."""

    class Empty:
        payload_calls = 0
        overlay_calls = 0

        def _macro_payload(self):
            return None

        def _overlay_live_macro_yahoo_latest(self, snapshot):
            raise AssertionError("must not overlay a missing snapshot")

    raised = None
    with _fake_app(module=Empty()):
        try:
            partner_api.macro(Req())
        except Exception as exc:  # HTTPException
            raised = exc
    check("503 raised", getattr(raised, "status_code", None) == 503, repr(raised))


def test_no_nan_survives_into_the_response():
    """Firestore stores Yahoo's NaN verbatim; envelope.finite is the gate."""
    check("NaN is not finite", not envelope.finite(float("nan")), "envelope.finite")
    cell = partner_api._macro_cell(_cell(float("nan"), "N/A"))
    check("NaN becomes null", cell["value"] is None, repr(cell["value"]))


def test_auth_is_required():
    """⚠️ The guard must be real. Five other files stub require_kilby at import
    and that patch leaks; this asserts the endpoint calls it at all."""
    calls = []
    saved = partner_api.require_kilby
    partner_api.require_kilby = lambda r: calls.append(r) or "test"
    try:
        with _fake_app():
            partner_api.require_kilby = lambda r: calls.append(r) or "test"
            partner_api.macro(Req())
    finally:
        partner_api.require_kilby = saved
    check("require_kilby was called", len(calls) == 1, f"{len(calls)} calls")


def main():
    return run_all(globals())


if __name__ == "__main__":
    sys.exit(main())
