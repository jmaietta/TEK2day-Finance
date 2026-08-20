"""A price pull that writes nothing must NOT report success.

⚠️ WHY THIS FILE EXISTS. From 13-19 Aug 2026 the daily price pull failed on
every one of 9,911 tickers, exited 0 every night, and Cloud Run recorded six
successes per run. Firestore's prices sat frozen for a week and nothing said so.

The bug that caused it was one line and took an hour to fix once the traceback
existed. THE SILENCE COST SIX EXTRA DAYS. This guard is worth more than the fix.
"""
import runpy
import sys
import types

import pytest

import pull_daily_prices as pdp


class _Recorder:
    """Stands in for fetchers/storage so no network or Firestore is touched."""

    def __init__(self, succeed_for):
        self.succeed_for = succeed_for
        self.written = []

    def fetch_prices(self, symbol, period="5d"):
        if symbol in self.succeed_for:
            return [{"date": "2026-08-19", "symbol": symbol, "open": 1.0,
                     "high": 2.0, "low": 0.5, "close": 1.5, "volume": 100}]
        return []

    def write_prices_batch(self, symbol, rows):
        self.written.append(symbol)


@pytest.fixture
def harness(monkeypatch):
    """Run main() against a fake universe with no sleeping and no I/O."""
    def build(universe, succeed_for):
        rec = _Recorder(set(succeed_for))
        monkeypatch.setattr(pdp.storage, "list_active_tickers", lambda: list(universe))
        monkeypatch.setattr(pdp.fetchers, "fetch_prices", rec.fetch_prices)
        monkeypatch.setattr(pdp.storage, "write_prices_batch", rec.write_prices_batch)
        monkeypatch.setattr(pdp.time, "sleep", lambda _s: None)
        monkeypatch.setattr(pdp, "DELAY", 0)
        monkeypatch.delenv("CLOUD_RUN_TASK_INDEX", raising=False)
        monkeypatch.delenv("CLOUD_RUN_TASK_COUNT", raising=False)
        return rec
    return build


UNIVERSE = [f"T{i:04d}" for i in range(100)]


def test_total_failure_exits_non_zero(harness):
    """THE REGRESSION TEST. Zero rows written for the whole universe is exactly
    what happened for seven nights, and it exited 0."""
    harness(UNIVERSE, succeed_for=[])
    with pytest.raises(SystemExit) as exc:
        pdp.main()
    assert exc.value.code == 1, "a run that wrote NOTHING must not report success"


def test_a_healthy_night_does_not_trip(harness):
    """~87% is the measured healthy rate; delisted shells, warrants and thin OTC
    lines legitimately return nothing. The guard must never fire on that."""
    rec = harness(UNIVERSE, succeed_for=UNIVERSE[:87])
    pdp.main()  # must not raise
    assert len(rec.written) == 87


def test_a_bad_but_survivable_night_does_not_trip(harness):
    """60% is poor and worth investigating, but data DID land. Failing here
    would train everyone to ignore the alarm."""
    rec = harness(UNIVERSE, succeed_for=UNIVERSE[:60])
    pdp.main()
    assert len(rec.written) == 60


def test_the_boundary_is_where_it_is_documented_to_be(harness):
    """Pins the floor itself. Moving it silently is how a guard rots."""
    assert pdp.MIN_SUCCESS_RATE == 0.5

    harness(UNIVERSE, succeed_for=UNIVERSE[:50])
    pdp.main()  # exactly at the floor passes

    harness(UNIVERSE, succeed_for=UNIVERSE[:49])
    with pytest.raises(SystemExit):
        pdp.main()  # one below it fails


def test_floor_is_overridable_for_a_catch_up_run(harness, monkeypatch):
    """A deliberate partial run (a shard, a retry of stragglers) must be able to
    lower the bar without editing code at 2am."""
    monkeypatch.setattr(pdp, "MIN_SUCCESS_RATE", 0.0)
    harness(UNIVERSE, succeed_for=[])
    pdp.main()  # must not raise


def test_empty_universe_does_not_trip(harness):
    """No tickers is a different fault - a storage problem, not a fetch problem.
    Dividing by zero or crying wolf here would point the next reader at the
    wrong system."""
    harness([], succeed_for=[])
    pdp.main()


def test_the_failure_is_logged_loudly(harness, caplog):
    """Cloud Run's status is the alarm, but a human reads the log. It must say
    what happened and why, not just exit."""
    harness(UNIVERSE, succeed_for=[])
    with caplog.at_level("ERROR"):
        with pytest.raises(SystemExit):
            pdp.main()
    message = " ".join(r.getMessage() for r in caplog.records)
    assert "PRICE PULL FAILED" in message
    assert "0 of 100" in message or "0.0%" in message
