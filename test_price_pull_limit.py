"""The price pull must be runnable on a handful of companies, not just 9,911.

⚠️ WHY THIS EXISTS. The job was all-or-nothing: every change — a dependency
bump, a fix, a new field — could only be tried against the full universe, and
therefore against the one upstream the whole platform depends on. During the
13-19 Aug 2026 outage, confirming a single theory would have cost 9,911 Yahoo
requests, which is a large part of why it went unconfirmed for a week.

Being throttled by Yahoo is an existential risk for TEK2day, so "just run it and
see" was never actually available. Now it is, on three companies.
"""
import pytest

import pull_daily_prices as pdp


UNIVERSE = ["AAPL", "AMZN", "GOOGL", "MSFT", "NVDA", "ORCL", "TSLA"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.setattr(pdp, "SYMBOLS", "")
    monkeypatch.setattr(pdp, "LIMIT", 0)


# ── selection ───────────────────────────────────────────────────────────────

def test_no_knob_means_the_whole_universe():
    """The scheduled nightly run must be completely unaffected."""
    picked, smoke = pdp._select(UNIVERSE)
    assert picked == UNIVERSE
    assert smoke is False


def test_limit_takes_the_first_n(monkeypatch):
    monkeypatch.setattr(pdp, "LIMIT", 3)
    picked, smoke = pdp._select(UNIVERSE)
    assert picked == ["AAPL", "AMZN", "GOOGL"]
    assert smoke is True


def test_named_symbols_run_exactly_those_in_order(monkeypatch):
    """Naming companies is the useful case: smoke-test on liquid names you can
    eyeball against Yahoo, not on whatever sorts first."""
    monkeypatch.setattr(pdp, "SYMBOLS", "NVDA,AAPL,MSFT")
    picked, smoke = pdp._select(UNIVERSE)
    assert picked == ["NVDA", "AAPL", "MSFT"]
    assert smoke is True


def test_symbols_are_case_and_space_tolerant(monkeypatch):
    monkeypatch.setattr(pdp, "SYMBOLS", " nvda , aapl ")
    picked, _ = pdp._select(UNIVERSE)
    assert picked == ["NVDA", "AAPL"]


def test_symbols_beat_limit_when_both_are_set(monkeypatch):
    """Naming companies is a more specific instruction than counting them."""
    monkeypatch.setattr(pdp, "SYMBOLS", "TSLA")
    monkeypatch.setattr(pdp, "LIMIT", 3)
    picked, _ = pdp._select(UNIVERSE)
    assert picked == ["TSLA"]


def test_an_unknown_symbol_is_skipped_and_named(monkeypatch, caplog):
    """⚠️ A TYPO MUST NOT READ AS 'that ticker failed'. Those are different
    problems and confusing them sends the next reader at the wrong system."""
    monkeypatch.setattr(pdp, "SYMBOLS", "NVDA,NOTATICKER")
    with caplog.at_level("WARNING"):
        picked, _ = pdp._select(UNIVERSE)
    assert picked == ["NVDA"]
    assert "NOTATICKER" in " ".join(r.getMessage() for r in caplog.records)


def test_all_symbols_unknown_is_an_error(monkeypatch, caplog):
    monkeypatch.setattr(pdp, "SYMBOLS", "NOPE,ALSONOPE")
    with caplog.at_level("ERROR"):
        picked, smoke = pdp._select(UNIVERSE)
    assert picked == []
    assert smoke is True
    assert "matched NO tickers" in " ".join(r.getMessage() for r in caplog.records)


# ── interaction with sharding and with the failure guard ────────────────────

class _Rec:
    def __init__(self, succeed):
        self.succeed, self.written = set(succeed), []

    def fetch_prices(self, symbol, period="5d"):
        if symbol in self.succeed:
            return [{"date": "2026-08-19", "symbol": symbol, "open": 1.0,
                     "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10}]
        return []

    def write_prices_batch(self, symbol, rows):
        self.written.append(symbol)


@pytest.fixture
def run(monkeypatch):
    def go(universe, succeed, **env):
        rec = _Rec(succeed)
        monkeypatch.setattr(pdp.storage, "list_active_tickers", lambda: list(universe))
        monkeypatch.setattr(pdp.fetchers, "fetch_prices", rec.fetch_prices)
        monkeypatch.setattr(pdp.storage, "write_prices_batch", rec.write_prices_batch)
        monkeypatch.setattr(pdp.time, "sleep", lambda _s: None)
        monkeypatch.setattr(pdp, "DELAY", 0)
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        return rec
    return go


def test_limit_is_across_the_whole_job_not_per_shard(run, monkeypatch):
    """⚠️ PRICE_PULL_LIMIT=3 with 6 shards must mean THREE tickers total, not
    eighteen. Selection happens before sharding for exactly this reason."""
    monkeypatch.setattr(pdp, "LIMIT", 3)
    seen = []
    for shard in range(6):
        rec = run(UNIVERSE, UNIVERSE,
                  CLOUD_RUN_TASK_INDEX=shard, CLOUD_RUN_TASK_COUNT=6)
        pdp.main()
        seen.extend(rec.written)
    assert sorted(seen) == ["AAPL", "AMZN", "GOOGL"], seen


def test_a_shard_with_nothing_to_do_exits_quietly(run, monkeypatch):
    """Shards beyond the smoke-test slice get zero tickers. That is not a
    failure and must not trip the guard or email anyone."""
    monkeypatch.setattr(pdp, "LIMIT", 1)
    run(UNIVERSE, UNIVERSE, CLOUD_RUN_TASK_INDEX=5, CLOUD_RUN_TASK_COUNT=6)
    pdp.main()  # must not raise


def test_a_failing_smoke_test_still_fails_loudly(run, monkeypatch):
    """A smoke test that finds the bug must report failure — that is its job."""
    monkeypatch.setattr(pdp, "SYMBOLS", "NVDA,AAPL,MSFT")
    run(UNIVERSE, succeed=[])
    with pytest.raises(SystemExit) as exc:
        pdp.main()
    assert exc.value.code == 1


def test_a_passing_smoke_test_says_it_was_a_smoke_test(run, monkeypatch, caplog):
    """⚠️ THE POINT. A partial run that reads like a full one in the logs is how
    'the pull ran fine last night' becomes false."""
    monkeypatch.setattr(pdp, "SYMBOLS", "NVDA,AAPL,MSFT")
    run(UNIVERSE, succeed=["NVDA", "AAPL", "MSFT"])
    with caplog.at_level("WARNING"):
        pdp.main()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "SMOKE TEST" in text
    assert "NOT" in text and "REFRESHED" in text.upper()


def test_the_nightly_run_is_never_labelled_a_smoke_test(run, caplog):
    run(UNIVERSE, succeed=UNIVERSE)
    with caplog.at_level("WARNING"):
        pdp.main()
    assert "SMOKE TEST" not in " ".join(r.getMessage() for r in caplog.records)
