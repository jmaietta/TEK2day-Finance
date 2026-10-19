#!/usr/bin/env python3
"""Each ticker is refreshed once a week, on a weekday fixed by its symbol.

    pytest test_weekly_slot.py

No network, no Firestore.

WHY THIS FILE EXISTS — 19 October 2026.

The weekly estimates job and the quarterly financials job each took
`index % 6` over the sorted active list. The universe sync adds tickers most
nights, and one addition near the top moves every later ticker into the next
day's slot. Rebuilding each run's list reproduced all 18 logged tranche sizes
from 28 Sep exactly. In the week of 12-17 Oct, 3,731 tickers were never
refreshed and 3,061 were refreshed twice. TSM was last due 12 Oct and missed
its 15 Oct results.
"""
from testkit import check

import storage

UNIVERSE = [f"T{i:05d}" for i in range(6000)] + ["AAPL", "BRK.B", "NVDA", "TSM", "BNY"]


def week(symbols, count=6):
    return [storage.todays_slice(symbols, count, day) for day in range(count)]


def test_every_ticker_is_due_exactly_once_a_week():
    seen = [s for day in week(UNIVERSE) for s in day]
    check("each once", sorted(seen) == sorted(UNIVERSE), str(len(seen)))


def test_adding_tickers_moves_no_existing_ticker():
    """The fault: a ticker added near the top of the alphabet shifted everyone."""
    before = {s: storage.weekly_slot(s) for s in UNIVERSE}
    grown = ["A", "AA", "AAA", "0ABC"] + UNIVERSE
    after = {s: storage.weekly_slot(s) for s in grown}
    check("nobody moved", all(after[s] == before[s] for s in UNIVERSE))
    check("slices agree", all(set(a) - {"A", "AA", "AAA", "0ABC"} == set(b)
                              for a, b in zip(week(grown), week(UNIVERSE))))


def test_removing_tickers_moves_no_existing_ticker():
    shrunk = UNIVERSE[100:]
    check("slices agree", all(set(a) == set(b) - set(UNIVERSE[:100])
                              for a, b in zip(week(shrunk), week(UNIVERSE))))


def test_the_slot_is_the_same_on_every_machine():
    """crc32, not Python's hash(), which is salted per process."""
    check("NVDA", storage.weekly_slot("NVDA") == 1, str(storage.weekly_slot("NVDA")))
    check("AAPL", storage.weekly_slot("AAPL") == 0, str(storage.weekly_slot("AAPL")))
    check("BRK.B", storage.weekly_slot("BRK.B") == 0, str(storage.weekly_slot("BRK.B")))


def test_the_days_are_even():
    sizes = [len(day) for day in week(UNIVERSE)]
    check("within 5% of an even split", max(sizes) <= len(UNIVERSE) / 6 * 1.05, str(sizes))


def test_a_count_of_one_takes_everyone():
    """Manual runs and the existing job tests use TRANCHE_COUNT=1."""
    check("all", storage.todays_slice(UNIVERSE, 1, 0) == sorted(UNIVERSE))


def _run_job(monkeypatch, job, fetch_name, index):
    seen = []
    monkeypatch.setattr(job.storage, "list_active_tickers", lambda: list(UNIVERSE))
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    monkeypatch.setenv("TRANCHE_COUNT", "6")
    monkeypatch.setenv("TRANCHE_INDEX", str(index))

    def stop(symbol, *a, **k):
        seen.append(symbol)
        raise KeyboardInterrupt  # one ticker is enough to see the slice's order
    monkeypatch.setattr(job.fetchers, fetch_name, stop)
    try:
        job.main()
    except KeyboardInterrupt:
        pass
    return seen


def test_both_jobs_use_the_fixed_slot(monkeypatch, caplog):
    import logging
    caplog.set_level(logging.INFO)
    import pull_quarterly_financials
    import pull_weekly_estimates
    for job, fetch_name in ((pull_weekly_estimates, "fetch_estimates"),
                            (pull_quarterly_financials, "fetch_financials")):
        for index in (0, 3):
            expected = storage.todays_slice(UNIVERSE, 6, index)
            caplog.clear()
            seen = _run_job(monkeypatch, job, fetch_name, index)
            check(f"{job.__name__} slot {index} starts where expected",
                  seen and seen[0].replace("-", ".") == expected[0], str((seen, expected[:2])))
            check(f"{job.__name__} slot {index} size logged",
                  f"Tranche {index} of 6: {len(expected)} tickers" in caplog.text, caplog.text[:300])
