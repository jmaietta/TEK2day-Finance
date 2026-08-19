#!/usr/bin/env python3
"""Tests for /partner/v1/equities/{symbol}/estimates.

    python test_partner_estimates.py

No network, no Firestore.

THE WHOLE DIFFICULTY OF THIS ENDPOINT IS THAT THE PERIOD KEYS ROLL.

Estimates are stored against `0q`, `+1q`, `0y`, `+1y` — "current quarter", "next
quarter" and so on. Those are not fixed periods. When a company reports, every
key shifts down one and the numbers change wholesale:

    before the report   0q = the quarter about to be reported
    after the report    0q = the NEXT quarter, a different set of estimates

A consumer comparing today's `0q` against last week's `0q` across a report date
is comparing two different quarters, and will read a ROLLOVER as though analysts
had revised their views. That is the plan's gate for this step.

So the response says three things it would be easy to leave implicit:
  1. the keys are rolling (`rolling: true`)
  2. what each one means, spelled out
  3. the DATE they are relative to (`as_of`)

⚠️ AND `as_of` IS THE RECORD'S OWN DATE, NEVER "TODAY". The helper used to say
the keys were "relative to today". Estimates are pulled WEEKLY, so on most days
of the week that was wrong — NVDA's snapshot was six days old when this was
written — and wrong in the direction that matters, because a consumer would
resolve "current quarter" against the wrong date.
"""
import json
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


RECORD = {
    "date": "2026-08-13",
    "fetched_at": "2026-08-13T11:55:30.334249+00:00",
    "eps_avg": {"0q": 2.083, "+1q": 2.35239, "0y": 8.99574, "+1y": 12.89043},
    "eps_high": {"0q": 2.2, "+1q": 2.63, "0y": 9.85, "+1y": 16.0},
    "eps_low": {"0q": 2.03128, "+1q": 2.13, "0y": 8.2, "+1y": 9.65},
    "eps_numberofanalysts": {"0q": 40.0, "+1q": 40.0, "0y": 50.0, "+1y": 50.0},
    "eps_growth": {"0q": 0.9838, "+1q": 0.8095, "0y": 0.88589996, "+1y": 0.4329},
    "eps_yearagoeps": {"0q": 1.05, "+1q": 1.3, "0y": 4.77, "+1y": 8.99574},
    "rev_avg": {"0q": 5.4e10, "+1q": 6.1e10},
}


def install(record=RECORD, covered=True):
    storage.get_ticker_meta = lambda s: ({"symbol": s, "name": "NVIDIA Corporation",
                                          "active": True} if covered else None)
    terminal._estimate_history = lambda s: ([record] if record else [])
    partner_api.require_kilby = lambda r: "test"


def call(symbol="NVDA"):
    result = partner_api.equity_estimates(Req(), symbol=symbol)
    if hasattr(result, "body"):
        return result.status_code, json.loads(result.body)
    return 200, result


# ── the rolling-key contract ─────────────────────────────────────────────────

def test_the_keys_are_declared_rolling():
    install()
    _, body = call()
    check("rolling flag", body["data"]["rolling"] is True, str(body["data"].get("rolling")))


def test_every_period_code_is_spelled_out():
    """A partner reading "0q" should not have to guess, and "Curr Q" is a column
    heading rather than a definition."""
    install()
    _, body = call()
    periods = body["data"]["periods"]
    for code in ("0q", "+1q", "0y", "+1y"):
        check(f"{code} explained", len(str(periods.get(code, ""))) > 10, str(periods.get(code)))
    check("says what they are relative to",
          all("as_of" in str(v) for v in periods.values()), str(periods))


def test_as_of_is_the_record_date_not_today():
    """THE FIX THIS ENDPOINT EXISTS TO CARRY. Estimates are pulled weekly, so a
    snapshot several days old is normal and "today" is wrong on most days."""
    install()
    _, body = call()
    check("data.as_of", body["data"]["as_of"] == "2026-08-13", str(body["data"].get("as_of")))
    check("envelope as_of matches", body["as_of"] == "2026-08-13", str(body.get("as_of")))
    check("capture time kept",
          body["data"]["captured_at"].startswith("2026-08-13T"),
          str(body["data"].get("captured_at")))


def test_the_note_does_not_claim_today():
    install()
    _, body = call()
    note = body["data"]["note"].lower()
    check("anchored to as_of", "as_of" in note, note)
    check("does not say relative to today", "relative to today" not in note, note)


def test_a_rollover_is_distinguishable_from_a_revision():
    """Two responses whose as_of straddle a report date describe DIFFERENT
    quarters under the same key. The dates are what make that visible."""
    install()
    _, before = call()
    install({**RECORD, "date": "2026-08-27",
             "eps_avg": {"0q": 2.35239, "+1q": 2.61, "0y": 9.1, "+1y": 13.2}})
    _, after = call()
    check("as_of moved", before["data"]["as_of"] != after["data"]["as_of"])
    check("same key, different value",
          before["data"]["eps"]["avg"]["0q"] != after["data"]["eps"]["avg"]["0q"])


# ── values ───────────────────────────────────────────────────────────────────

def test_values_are_raw_not_rendered():
    """`_estimates_payload` formats for the browser — "12.40%", "91.8B", the
    string "N/A". A partner needs the number."""
    install()
    _, body = call()
    check("eps avg raw", body["data"]["eps"]["avg"]["0q"] == 2.083,
          str(body["data"]["eps"]["avg"]["0q"]))
    check("growth stays a fraction", body["data"]["eps"]["growth"]["0q"] == 0.9838,
          str(body["data"]["eps"]["growth"]["0q"]))


def test_revenue_and_eps_are_separate_sections():
    install()
    _, body = call()
    check("eps section", "eps" in body["data"])
    check("revenue section", "revenue" in body["data"], str(sorted(body["data"])))


def test_every_metric_is_defined():
    """FS5 — the description travels with the value."""
    install()
    _, body = call()
    defs = body["data"]["definitions"]
    for metric in ("avg", "high", "low", "numberofanalysts", "growth", "as_of"):
        check(f"{metric} defined", metric in defs, str(sorted(defs)))


# ── absence and staleness ────────────────────────────────────────────────────

def test_no_estimates_is_coverage_not_failure():
    """A covered company nobody publishes a consensus for. Same shape as a
    company with no quarterly statements: 200, data null, and a note."""
    install(record=None)
    status, body = call()
    check("200", status == 200, str(status))
    check("data is null", body["data"] is None, str(body["data"]))
    check("said plainly",
          [w["code"] for w in body["quality"]["warnings"]] == ["no_estimates"],
          str(body["quality"]["warnings"]))


def test_an_uncovered_symbol_is_404():
    install(covered=False)
    status, _ = call("ZZZZ")
    check("404", status == 404, str(status))


def test_a_stale_snapshot_is_flagged():
    """Estimates are pulled weekly. Past a fortnight the pull has likely
    stalled, and a consumer must be told rather than left to assume the
    consensus is current."""
    install({**RECORD, "date": "2026-01-01"})
    _, body = call()
    codes = [w["code"] for w in body["quality"]["warnings"]]
    check("flagged", "stale_estimates" in codes, str(codes))
    note = next(w["note"] for w in body["quality"]["warnings"]
                if w["code"] == "stale_estimates")
    check("says how old", "days ago" in note, note)


def test_a_fresh_snapshot_is_not_flagged():
    install()
    _, body = call()
    check("no stale note",
          "stale_estimates" not in [w["code"] for w in body["quality"]["warnings"]],
          str(body["quality"]["warnings"]))


def test_an_unparseable_date_does_not_raise():
    install({**RECORD, "date": "not-a-date"})
    status, _body = call()
    check("still answers", status == 200, str(status))


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
