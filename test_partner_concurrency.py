#!/usr/bin/env python3
"""Contamination tests for /partner/v1/comparisons — plan step 9.

    python test_partner_concurrency.py

No network, no Firestore.

WHY THIS FILE EXISTS.

`/comparisons` is the FIRST endpoint that uses a thread pool. It was introduced
because six live quotes in series timed a request out, and each worker calls
terminal._market_snapshot, which reads a shared TTL cache. That is the one place
in the partner API where cross-symbol bleed could actually occur, and until now
it was the one place with no test for it.

THE FAILURE THIS GUARDS AGAINST IS INVISIBLE. A wrong single card is obvious —
one company, one set of numbers, and a reader who knows the company spots it. A
wrong COLUMN is not: it sits under the right ticker, beside companies that ARE
right, in a table whose whole purpose is being read across. Nothing looks odd.
The reader's eye is on the differences between the columns, which is exactly
what a bled value corrupts.

The plan's own words for this step: "NVDA straight after MSFT; alternate
repeatedly; concurrent different symbols; assert no cross-symbol bleed and no
cache collision."

Every test here forces REAL thread interleaving with randomised latency rather
than assuming the pool schedules helpfully. Runs are repeated, because a race
that fails one time in twenty passes a single run.
"""
import random
import sys
import threading
import time

import partner_api
import storage
import terminal
from testkit import check, run_all


class Req:
    headers: dict = {}


UNIVERSE = ["NVDA", "AMD", "INTC", "MSFT", "AAPL", "AMZN", "GOOGL", "JPM"]


def snapshot_for(symbol):
    """A snapshot whose every figure is derived from the symbol itself.

    That is the whole trick: if any value ever appears in the wrong column it
    can be traced straight back to the company it belongs to, rather than
    needing a table of expected numbers.
    """
    seed = float(UNIVERSE.index(symbol) + 1)
    return {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "price": seed * 100,
        "market_cap": seed * 1e12,
        "enterprise_value": seed * 1.1e12,
        "revenue": seed * 1e11,
        "ebitda": seed * 1e10,
        "net_income": seed * 1e9,
        "eps_ttm": seed,
        "forward_eps": seed * 2,
        "pe_ttm": seed * 10,
        "forward_pe": seed * 5,
        "ps_ttm": seed * 3,
        "ev_revenue": seed * 4,
        "ev_ebitda": seed * 6,
        "ev_opcf": seed * 7,
        "ev_fcf": seed * 8,
        "ttm_as_of": f"2026-0{int(seed) % 9 + 1}-30",
        "balance_sheet_as_of": f"2026-0{int(seed) % 9 + 1}-30",
    }


def install(latency=True):
    storage.get_ticker_meta = lambda s: ({"symbol": s, "name": f"{s} Inc.", "active": True}
                                         if s in UNIVERSE else None)

    def slow_snapshot(symbol):
        # Randomised, so workers finish out of order and interleave differently
        # on every run. A fixed sleep would schedule the same way every time and
        # prove nothing.
        if latency:
            time.sleep(random.uniform(0, 0.02))
        return snapshot_for(symbol) if symbol in UNIVERSE else None

    terminal._market_snapshot = slow_snapshot
    partner_api.require_kilby = lambda r: "test"


def call(symbols):
    result = partner_api.comparisons(Req(), symbols=symbols)
    if hasattr(result, "body"):
        import json
        return result.status_code, json.loads(result.body)
    return 200, result


def assert_columns_are_their_own(body, expected, label):
    """Every value in every row must belong to the company heading its column."""
    companies = [c["symbol"] for c in body["data"]["companies"]]
    if companies != expected:
        return f"{label}: columns {companies} != {expected}"
    for row in body["data"]["rows"]:
        field = row["field"]
        for idx, symbol in enumerate(companies):
            want = snapshot_for(symbol).get(field)
            got = row["values"][idx]
            if got != want:
                return (f"{label}: row {field} column {idx} ({symbol}) "
                        f"held {got}, which belongs to "
                        f"{next((s for s in UNIVERSE if snapshot_for(s).get(field) == got), '?')}")
    return None


# ── concurrent, different symbols ────────────────────────────────────────────

def test_parallel_requests_for_different_sets_never_bleed():
    """The headline case. Many requests in flight at once, each for a different
    set, all sharing one module and one snapshot function."""
    install()
    sets = [
        ["NVDA", "AMD"],
        ["MSFT", "AAPL", "AMZN"],
        ["INTC", "GOOGL"],
        ["JPM", "NVDA", "MSFT", "AMD"],
        ["AAPL"],
        ["AMZN", "INTC", "JPM", "GOOGL", "NVDA", "AMD"],
    ]
    errors = []
    barrier = threading.Barrier(len(sets))

    def run(symbols):
        barrier.wait()          # every thread enters the endpoint together
        _, body = call(",".join(symbols))
        problem = assert_columns_are_their_own(body, symbols, "parallel")
        if problem:
            errors.append(problem)

    for _ in range(15):
        errors.clear()
        barrier.reset()
        threads = [threading.Thread(target=run, args=(s,)) for s in sets]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            break
    check("no bleed across parallel requests", not errors, errors[0] if errors else "")


def test_one_request_keeps_its_columns_under_random_latency():
    """Within a single request the workers finish out of order. Order comes from
    pool.map, not from completion — this proves it."""
    install()
    problems = []
    for _ in range(40):
        symbols = random.sample(UNIVERSE, random.randint(2, 6))
        _, body = call(",".join(symbols))
        problem = assert_columns_are_their_own(body, symbols, "single")
        if problem:
            problems.append(problem)
            break
    check("columns hold under latency", not problems, problems[0] if problems else "")


# ── the plan's exact words: NVDA straight after MSFT, alternating ────────────

def test_nvda_straight_after_msft_repeatedly():
    """Sequential calls must not leave anything behind for the next one."""
    install()
    problems = []
    for _ in range(30):
        _, body = call("MSFT")
        p = assert_columns_are_their_own(body, ["MSFT"], "msft")
        if p:
            problems.append(p)
            break
        _, body = call("NVDA")
        p = assert_columns_are_their_own(body, ["NVDA"], "nvda-after-msft")
        if p:
            problems.append(p)
            break
    check("alternating leaves no residue", not problems, problems[0] if problems else "")


def test_alternating_sets_of_different_widths():
    """A four-column answer followed by a two-column one must not keep two
    columns of the previous table."""
    install()
    problems = []
    for _ in range(20):
        _, wide = call("NVDA,AMD,INTC,MSFT")
        _, narrow = call("AAPL,AMZN")
        for body, expected, label in ((wide, ["NVDA", "AMD", "INTC", "MSFT"], "wide"),
                                      (narrow, ["AAPL", "AMZN"], "narrow")):
            p = assert_columns_are_their_own(body, expected, label)
            if p:
                problems.append(p)
        if problems:
            break
    check("width changes cleanly", not problems, problems[0] if problems else "")


# ── cache collision ──────────────────────────────────────────────────────────

def test_the_endpoint_never_mutates_a_snapshot_it_was_given():
    """_market_snapshot reads a shared TTL cache and may hand back the CACHED
    OBJECT itself. If the endpoint mutated it, the damage would outlive the
    request and land in somebody else's table."""
    install(latency=False)
    cache = {s: snapshot_for(s) for s in UNIVERSE}
    before = {s: dict(v) for s, v in cache.items()}
    terminal._market_snapshot = lambda s: cache.get(s)
    call("NVDA,AMD,INTC,MSFT")
    call("AAPL,AMZN")
    changed = [s for s in UNIVERSE if cache[s] != before[s]]
    check("cached snapshots untouched", not changed, str(changed))


def test_a_shared_cache_object_does_not_alias_between_columns():
    """The nastier shape: a cache that hands the SAME object to two symbols.
    Values must still be read per column rather than the last writer winning."""
    install(latency=False)
    shared = snapshot_for("NVDA")
    terminal._market_snapshot = lambda s: shared if s in ("NVDA", "AMD") else snapshot_for(s)
    _, body = call("NVDA,AMD")
    # AMD's snapshot says symbol=NVDA, so FS1 must discard it rather than let
    # NVIDIA's numbers render under AMD's heading.
    companies = body["data"]["companies"]
    check("AMD column kept", [c["symbol"] for c in companies] == ["NVDA", "AMD"], str(companies))
    check("AMD marked uncovered", companies[1]["covered"] is False, str(companies[1]))
    price = next(r for r in body["data"]["rows"] if r["field"] == "price")
    check("no NVDA price under AMD", price["values"][1] is None, str(price["values"]))


# ── the global lock the partner API must never take ─────────────────────────

def test_the_endpoint_does_not_wait_on_the_website_lock():
    """app.COMMAND_LOCK serialises the terminal renderer. If the partner API
    took it, partner traffic would queue behind website traffic. Held here in
    the main thread: the call must complete anyway."""
    install(latency=False)
    import app  # noqa: PLC0415
    done = []

    def run():
        call("NVDA,AMD")
        done.append(True)

    with app.COMMAND_LOCK:
        worker = threading.Thread(target=run)
        worker.start()
        worker.join(timeout=10)
    check("completed while the lock was held", done == [True],
          "blocked on COMMAND_LOCK" if not done else "")


# ── the crash found while writing these ──────────────────────────────────────

def test_symbols_that_normalise_away_do_not_crash():
    """A tab survives the split — only SPACES become separators — then
    normalises to "". That left the worker list empty and
    ThreadPoolExecutor(max_workers=0) raises, turning a malformed request into a
    500 rather than a 404."""
    install(latency=False)
    for probe in ("\t", "\n", "\t,\n"):
        try:
            status, _ = call(probe)
        except Exception as exc:  # noqa: BLE001
            check(f"{probe!r} does not raise", False, f"{type(exc).__name__}: {exc}")
            continue
        check(f"{probe!r} answers 404", status == 404, str(status))


def main():
    return run_all(globals(), setup=random.seed)


if __name__ == "__main__":
    sys.exit(main())
