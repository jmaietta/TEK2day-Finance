#!/usr/bin/env python3
"""Tests for the `periods` block on /equities/{symbol}/summary.

    python test_partner_summary.py

No network, no Firestore.

WHY THIS BLOCK EXISTS — 16 August 2026.

Every figure labelled TTM covers a twelve-month period, and that period does not
always end on the latest quarter. Yahoo opens a quarter within hours of a release
and fills it in days or weeks later; until it does, the window sits one quarter
back. Enterprise value has the same exposure through the balance sheet behind
it.

Both were live that day and neither was knowable from the response:

    AMZN   TTM ended 31 March while the card implied "now"
    ORCL   enterprise value stood on a FEBRUARY balance sheet while Yahoo had May

The figures were right. What was missing was the period they described — and a
portfolio manager cannot use a number they cannot place. FS4 says a figure is
declared, never inferred; these two dates are what makes TTM and EV declarable.

⚠️ NOTE: this file covers the periods block ONLY. Before it, no suite called
equity_summary at all — the endpoint's twelve fields were checked by hand
against the website once, in August, and never since.
"""
import sys

import partner_api
import storage
import terminal

_passed = 0
_failed = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
    else:
        _failed.append(f"{name}{(' — ' + detail) if detail else ''}")


class Req:
    headers: dict = {}


SNAP = {
    "symbol": "ORCL",
    "name": "Oracle Corporation",
    "price": 150.52,
    "market_cap": 438_770_000_000.0,
    "enterprise_value": 563_670_000_000.0,
    "revenue": 67_360_000_000.0,
    "eps_ttm": 5.86,
    "ttm_as_of": "2026-05-31",
    "balance_sheet_as_of": "2026-05-31",
}


def install(snap=SNAP):
    storage.get_ticker_meta = lambda s: {"symbol": "ORCL", "name": "Oracle Corporation",
                                         "active": True}
    terminal._market_snapshot = lambda s: snap
    partner_api.require_kilby = lambda r: "test"
    partner_api._estimates = lambda s: None


def call(symbol="ORCL"):
    result = partner_api.equity_summary(Req(), symbol=symbol)
    if hasattr(result, "body"):
        import json
        return result.status_code, json.loads(result.body)
    return 200, result


def test_the_periods_block_is_present():
    install()
    _, body = call()
    periods = (body.get("data") or {}).get("periods")
    check("periods block exists", isinstance(periods, dict), str(periods))


def test_it_carries_both_dates():
    install()
    _, body = call()
    periods = body["data"]["periods"]
    check("ttm date", periods.get("ttm_as_of") == "2026-05-31", str(periods))
    check("balance sheet date", periods.get("balance_sheet_as_of") == "2026-05-31",
          str(periods))


def test_the_two_dates_can_differ():
    """ORACLE, THE REAL CASE. Its stub May quarter pushed the TTM window back to
    February while the balance sheet came from the annual record closing 31 May.
    One response, two different periods, and only saying so makes either
    usable."""
    install({**SNAP, "ttm_as_of": "2026-02-28"})
    _, body = call()
    periods = body["data"]["periods"]
    check("dates differ", periods["ttm_as_of"] != periods["balance_sheet_as_of"],
          str(periods))
    check("both stated", periods["ttm_as_of"] == "2026-02-28"
          and periods["balance_sheet_as_of"] == "2026-05-31", str(periods))


def test_a_missing_period_is_null_not_omitted():
    """Missing is null and says so — the same rule as every figure. A null here
    is why the figure beside it is null too."""
    install({**SNAP, "balance_sheet_as_of": None, "enterprise_value": None})
    _, body = call()
    periods = body["data"]["periods"]
    check("key survives", "balance_sheet_as_of" in periods, str(periods))
    check("value is null", periods["balance_sheet_as_of"] is None, str(periods))


def test_an_older_snapshot_without_the_fields_still_answers():
    """Cached snapshots predate these keys."""
    install({k: v for k, v in SNAP.items() if not k.endswith("_as_of")})
    status, body = call()
    check("still 200", status == 200, str(status))
    check("dates are null", body["data"]["periods"]["ttm_as_of"] is None,
          str(body["data"]["periods"]))


def test_both_dates_are_defined():
    """FS5: the description travels with the value."""
    install()
    _, body = call()
    defs = body["data"]["definitions"]
    check("ttm_as_of defined", "ttm_as_of" in defs, str(list(defs)))
    check("balance_sheet_as_of defined", "balance_sheet_as_of" in defs, str(list(defs)))


def test_the_dates_are_not_rendered_into_display():
    """`display` mirrors FIGURES. A date is already a string and formatting it
    would invent a second representation of the same thing."""
    install()
    _, body = call()
    display = body["data"].get("display") or {}
    check("no periods in display", "periods" not in display, str(list(display)))


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = _passed + len(_failed)
    if _failed:
        print(f"FAILED {len(_failed)}/{total}")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print(f"{_passed}/{total} tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
