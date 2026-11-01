"""Thursday's 5-year re-adjust rewrites a stock's whole history only when it
can have moved (owner, 1 Nov 2026). Every other stock gets its newest rows."""
from datetime import date

import pandas as pd
import pytest

from testkit import check

import fetchers
import pull_daily_prices as pdp

THURSDAY = date(2026, 11, 12)          # not in a month's first week
FIRST_THURSDAY = date(2026, 12, 3)


def _rows(n=10, close=10.0):
    return [{"date": f"2026-10-{d:02d}", "symbol": "AAPL", "open": close, "high": close,
             "low": close, "close": close, "volume": 100} for d in range(1, n + 1)]


@pytest.fixture
def stored(monkeypatch):
    """The stock's last 5-year date and its stored rows, with every read counted."""
    state = {"last": "2026-11-05", "rows": {r["date"]: dict(r) for r in _rows()}, "reads": 0, "cap": 4e8}

    def get_ticker_meta(symbol):
        state["reads"] += 1
        return {"symbol": symbol, "market_cap": state["cap"]}

    def last_refresh(symbol):
        state["reads"] += 1
        return state["last"]

    def get_prices_on(symbol, days):
        state["reads"] += len(days)
        return {d: dict(state["rows"][d]) for d in days if d in state["rows"]}

    monkeypatch.setattr(pdp, "REFRESH_MODE", "events")
    monkeypatch.setattr(pdp.share_counts, "last_refresh", last_refresh)
    monkeypatch.setattr(pdp.storage, "get_prices_on", get_prices_on)
    monkeypatch.setattr(pdp.storage, "get_ticker_meta", get_ticker_meta)
    return state


def test_an_unchanged_stock_gets_only_its_newest_rows(stored):
    rows, why = pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)
    check("new rows", why == "new_rows" and [r["date"] for r in rows] == [f"2026-10-{d:02d}" for d in range(6, 11)],
          str((why, rows)))
    check("seven reads: the date, 5 checked days, the market cap", stored["reads"] == 7)


@pytest.mark.parametrize("splits, adjustments, why", [
    ({"2026-11-09": 0.1}, set(), "event"),          # a split since the last refresh
    ({}, {"2026-11-06"}, "event"),                  # a dividend since it
    ({}, {"2026-11-05"}, "event"),                  # on the refresh day itself
    ({"2025-06-10": 4.0}, {"2026-08-14"}, "new_rows"),   # older events are already in the stored rows
])
def test_a_split_or_dividend_since_the_last_refresh_rewrites_everything(stored, splits, adjustments, why):
    rows, got = pdp.refresh_rows("AAPL", _rows(), splits, adjustments, THURSDAY)
    check(why, got == why and len(rows) == (10 if why == "event" else 5), str((got, len(rows))))


@pytest.mark.parametrize("day", ["2026-10-01", "2026-10-05", "2026-10-09"])   # checked days
def test_a_moved_checked_day_rewrites_everything(stored, day):
    """A dividend Yahoo added late, or a revised history, moves older rows
    without an event since the last refresh."""
    stored["rows"][day]["close"] = 9.9731
    rows, why = pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)
    check("tripwire", why == "tripwire" and len(rows) == 10, why)


def test_any_change_at_all_rewrites_everything(stored):
    """The check's threshold is for reporting; rewriting needs no threshold."""
    stored["rows"]["2026-10-01"]["volume"] = 101
    check("tripwire", pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)[1] == "tripwire")


def test_a_missing_checked_day_rewrites_everything(stored):
    del stored["rows"]["2026-10-01"]
    check("tripwire", pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)[1] == "tripwire")


def test_a_day_between_the_checked_ones_waits_for_the_month(stored):
    """The known gap: only the month's first Thursday compares every day."""
    stored["rows"]["2026-10-02"]["close"] = 9.0
    check("weekly", pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)[1] == "new_rows")
    checks = []
    pdp.refresh_rows("AAPL", _rows(), {}, set(), FIRST_THURSDAY, checks)
    check("monthly", checks[0]["differing"] == 1 and checks[0]["largest"][0] == "2026-10-02", str(checks))


def test_no_refresh_date_rewrites_everything(stored):
    stored["last"] = None
    rows, why = pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)
    check("no date", why == "no_refresh_date" and len(rows) == 10, why)


def test_an_unreadable_record_rewrites_everything(stored, monkeypatch):
    def fail(*_a):
        raise RuntimeError("Firestore unavailable")
    monkeypatch.setattr(pdp.storage, "get_prices_on", fail)
    check("stored days", pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)[1] == "unreadable")
    monkeypatch.setattr(pdp.share_counts, "last_refresh", fail)
    check("last date", pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)[1] == "unreadable")


@pytest.mark.parametrize("cap, why", [(1e9, "large"), (2.9e12, "large"), (999_999_999, "new_rows"),
                                      (None, "new_rows"), (float("nan"), "new_rows")])
def test_companies_worth_a_billion_or_more_are_rewritten_every_week(stored, cap, why):
    """Owner, 1 Nov 2026: "do the $1 billion plan". No market cap on file
    counts as smaller."""
    stored["cap"] = cap
    rows, got = pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)
    check(why, got == why and len(rows) == (10 if why == "large" else 5), str((cap, got)))


def test_an_unreadable_company_record_rewrites_everything(stored, monkeypatch):
    def fail(*_a):
        raise RuntimeError("Firestore unavailable")
    monkeypatch.setattr(pdp.storage, "get_ticker_meta", fail)
    check("unreadable", pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)[1] == "unreadable")


def test_a_months_first_thursday_rewrites_every_stock_after_checking_every_day(stored):
    rows, why = pdp.refresh_rows("AAPL", _rows(), {}, set(), FIRST_THURSDAY)
    check("monthly", why == "monthly" and len(rows) == 10 and stored["reads"] == 11, str((why, stored["reads"])))


def test_switched_off_is_todays_full_rewrite_without_reading(stored, monkeypatch):
    monkeypatch.setattr(pdp, "REFRESH_MODE", "full")
    rows, why = pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)
    check("full", why == "full" and len(rows) == 10 and stored["reads"] == 0, why)


@pytest.mark.parametrize("symbol", ["AMZN", "BNY", "XOM"])   # enrolled, renamed, successor
def test_a_reviewed_identity_keeps_todays_full_rewrite(stored, symbol):
    """Their writes go through the identity fences, and only they can hold
    price_repair rows: nothing about their Thursday changes. They are checked."""
    checks = []
    rows, why = pdp.refresh_rows(symbol, _rows(), {}, set(), THURSDAY, checks)
    check(symbol, why == "guarded" and len(rows) == 10 and len(checks) == 1, why)
    check("others are not", not pdp.storage.reviewed_identity("AAPL"))


# ── the price check ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("close, differing", [(10.0001, 0), (10.0009, 0), (10.002, 1), (11.0, 1)])
def test_a_difference_is_more_than_one_rounding_step_and_a_hundredth_of_a_percent(stored, close, differing):
    stored["rows"]["2026-10-05"]["close"] = close
    checks = []
    pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY, checks)
    check(str(close), checks[0]["differing"] == differing and checks[0]["compared"] == 5, str(checks))


def test_pennies_need_more_than_one_rounding_step(stored):
    for row in stored["rows"].values():
        row["close"] = 0.0401                      # 0.25% from 0.04, but one step
    checks = []
    pdp.refresh_rows("AAPL", _rows(close=0.04), {}, set(), THURSDAY, checks)
    check("one step", checks[0]["differing"] == 0, str(checks))


def test_an_unexplained_move_over_one_percent_alerts(stored, caplog):
    stored["rows"]["2026-10-05"]["close"] = 10.5    # Yahoo now says 10.0: -4.76%
    checks = []
    pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY, checks)
    check("unexplained", checks[0]["differing"] == 1 and not checks[0]["expected"], str(checks))
    alert = [r for r in caplog.records if "PRICE CHECK ALERT" in r.getMessage()]
    check("alert", len(alert) == 1 and alert[0].levelname == "ERROR"
          and "AAPL: 1 of 5 stored days differ" in alert[0].getMessage(), caplog.text)


def test_a_small_unexplained_move_is_logged_without_alerting(stored, caplog):
    stored["rows"]["2026-10-05"]["close"] = 10.05   # -0.5%
    pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY)
    check("warning", "price check AAPL" in caplog.text and "ALERT" not in caplog.text, caplog.text)


def test_a_dividend_since_the_last_full_rewrite_explains_the_move(stored, caplog):
    for row in stored["rows"].values():
        row["close"] = 10.2                        # before the dividend's adjustment
    checks = []
    rows, why = pdp.refresh_rows("AAPL", _rows(), {}, {"2026-11-09"}, THURSDAY, checks)
    check("expected", why == "event" and checks[0]["expected"] and checks[0]["differing"] == 5, str(checks))
    check("no alert", "ALERT" not in caplog.text and "(expected)" in caplog.text, caplog.text)


def test_repaired_days_are_not_yahoos_and_are_left_out(stored):
    stored["rows"]["2026-10-05"].update(close=12.0, price_repair={"source": "reviewed"})
    checks = []
    pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY, checks)
    check("left out", checks[0]["compared"] == 4 and checks[0]["differing"] == 0, str(checks))


def test_switched_off_checks_nothing(stored, monkeypatch):
    monkeypatch.setattr(pdp, "REFRESH_MODE", "full")
    checks = []
    pdp.refresh_rows("AAPL", _rows(), {}, set(), THURSDAY, checks)
    check("nothing", checks == [] and stored["reads"] == 0)


def test_the_checked_days_span_the_whole_download():
    days = pdp._spread([{"date": f"d{i:04d}"} for i in range(1260)])
    check("spread", [d["date"] for d in days] == ["d0000", "d0252", "d0504", "d0756", "d1008"], str(days))
    check("short", len(pdp._spread([{"date": "a"}, {"date": "b"}])) == 2)


# ── the run ──────────────────────────────────────────────────────────────────

def _run(monkeypatch, period, mode, backfill=(), kept=None):
    written = {}
    kept = {} if kept is None else kept
    monkeypatch.setattr(pdp, "PERIOD", period)
    monkeypatch.setattr(pdp, "REFRESH_MODE", mode)
    monkeypatch.setattr(pdp.storage, "list_active_tickers", lambda: ["AAPL", "NEW"])
    monkeypatch.setattr(pdp.universe_sync, "backfill_pending", lambda: set(backfill))
    monkeypatch.setattr(pdp.universe_sync, "backfill_done", lambda db, s: None)
    monkeypatch.setattr(pdp.universe_sync, "run", lambda *a, **k: {})
    monkeypatch.setattr(pdp.universe_sync, "skip_list", lambda *a, **k: set())
    monkeypatch.setattr(pdp.fetchers, "fetch_prices", lambda s, period, splits=None, adjustments=None: _rows())
    monkeypatch.setattr(pdp.storage, "write_prices_batch", lambda s, rows: written.__setitem__(s, len(rows)))
    monkeypatch.setattr(pdp.share_counts, "save_splits", lambda db, s, splits, period: kept.__setitem__(s, period))
    monkeypatch.setattr(pdp.share_counts, "run", lambda *a, **k: None)
    monkeypatch.setattr(pdp.share_counts, "last_refresh", lambda s: "2026-11-05")
    monkeypatch.setattr(pdp.storage, "get_prices_on", lambda s, days: {r["date"]: r for r in _rows()})
    monkeypatch.setattr(pdp.storage, "get_ticker_meta", lambda s: {"market_cap": 4e8})
    monkeypatch.setattr(pdp.fx_rates, "run", lambda *a, **k: None)
    monkeypatch.setattr(pdp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pdp, "DELAY", 0)
    monkeypatch.setattr(pdp, "datetime", _Clock)
    monkeypatch.delenv("CLOUD_RUN_TASK_INDEX", raising=False)
    monkeypatch.delenv("CLOUD_RUN_TASK_COUNT", raising=False)
    try:
        pdp.main()
    except SystemExit as exc:
        assert not exc.code, exc.code
    return written


class _Clock(pdp.datetime):
    @classmethod
    def now(cls, tz=None):
        return pdp.datetime(2026, 11, 12, 16, 30, tzinfo=tz)


def test_the_thursday_run_writes_new_rows_and_says_why(monkeypatch, caplog):
    caplog.set_level("INFO")
    written = _run(monkeypatch, "5y", "events")
    check("new rows only", written == {"AAPL": 5, "NEW": 5}, str(written))
    check("counted", "5-year refresh (mode events): new_rows 2; 10 rows written" in caplog.text, caplog.text[-500:])
    check("checked", "Price check: 2 stocks, 10 stored days compared; differences: 0 expected, "
          "0 unexplained (0 over 1%); 0 stocks with stored days missing" in caplog.text, caplog.text[-800:])


def test_a_new_ticker_still_gets_its_whole_history(monkeypatch):
    written = _run(monkeypatch, "5y", "events", backfill={"NEW"})
    check("backfill", written == {"AAPL": 5, "NEW": 10}, str(written))


def test_only_a_full_rewrite_moves_the_last_refresh_date(monkeypatch):
    """checked_at is when the whole history was last re-adjusted. A new-rows
    Thursday records its splits as a short download does (events only, the
    date stays), so an event Yahoo lists late, dated after it, still counts."""
    kept = {}
    _run(monkeypatch, "5y", "events", backfill={"NEW"}, kept=kept)
    check("kept", kept == {"AAPL": "5d", "NEW": "5y"}, str(kept))
    _run(monkeypatch, "5y", "full", kept=kept)
    check("switch off", kept == {"AAPL": "5y", "NEW": "5y"}, str(kept))


def test_a_short_download_keeps_the_date_and_adds_its_events():
    from datetime import date as _date
    import share_counts as sc
    from test_sec_share_counts import FakeDB
    db = FakeDB()
    sc.save_splits(db, "AAPL", {}, "5y", today=_date(2026, 11, 5))
    sc.save_splits(db, "AAPL", {}, "5d", today=_date(2026, 11, 12))
    sc.save_splits(db, "AAPL", {"2026-11-10": 4.0}, "5d", today=_date(2026, 11, 12))
    held = db.store["splits/AAPL"]
    check("date kept", held["checked_at"] == "2026-11-05" and held["events"] == {"2026-11-10": 4.0}, str(held))


def test_weeknights_and_the_switch_off_are_unchanged(monkeypatch):
    check("weeknight", _run(monkeypatch, "5d", "events") == {"AAPL": 10, "NEW": 10})
    check("switch off", _run(monkeypatch, "5y", "full") == {"AAPL": 10, "NEW": 10})


# ── the dates, from the download already made ───────────────────────────────

class _Ticker:
    history_metadata = {}

    def history(self, **kw):
        idx = pd.to_datetime(["2026-08-13", "2026-08-14", "2026-08-17"])
        return pd.DataFrame({"Open": [1.0] * 3, "High": [1.0] * 3, "Low": [1.0] * 3, "Close": [1.0] * 3,
                             "Volume": [1] * 3, "Dividends": [0.0, 0.26, 0.0],
                             "Stock Splits": [0.0] * 3, "Capital Gains": [0.0, 0.0, 0.5]}, index=idx)


def test_the_download_hands_over_its_dividend_and_capital_gains_dates(monkeypatch):
    monkeypatch.setattr(fetchers.yf, "Ticker", lambda s: _Ticker())
    adjustments = set()
    rows = fetchers.fetch_prices("AAPL", period="5y", adjustments=adjustments)
    check("dates", adjustments == {"2026-08-14", "2026-08-17"}, str(adjustments))
    check("rows as before", len(rows) == 3 and "Dividends" not in rows[0], str(rows[0]))
