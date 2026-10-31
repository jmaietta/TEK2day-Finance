#!/usr/bin/env python3
"""Tests for beta: TEK2day's own, 2 years of weekly returns against SPY.

    pytest test_beta.py

No network, no Firestore. Decided 30 Oct 2026 (beta.py has the method): Kilby
shows beta beside R-squared, which Yahoo does not publish, so both come from
one fit of ours. Yahoo's beta is still saved, under its own name, and never
shown or served.
"""
import random
import statistics
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from testkit import check

import beta
import fetchers
import share_counts
import storage
import terminal

REAL_FOR_SYMBOL = beta.for_symbol    # conftest blanks it for every other test
ET = ZoneInfo("America/New_York")


def _fridays(n, last=date(2026, 10, 23)):
    return [(last - timedelta(weeks=n - 1 - i)).isoformat() for i in range(n)]


def _market(n=105, slope=1.4, seed=7):
    """SPY and a stock whose weekly returns are slope x SPY's plus noise."""
    rng = random.Random(seed)
    ends = _fridays(n)
    spy, stock = {ends[0]: 400.0}, {ends[0]: 50.0}
    for a, b in zip(ends, ends[1:]):
        x = rng.gauss(0.002, 0.02)
        spy[b] = spy[a] * (1 + x)
        stock[b] = stock[a] * (1 + slope * x + rng.gauss(0, 0.02))
    return ends, spy, stock


def _excel(ends, spy, stock):
    """SLOPE and RSQ the way a spreadsheet does them, from the same closes."""
    pairs = [(spy[b] / spy[a] - 1, stock[b] / stock[a] - 1) for a, b in zip(ends, ends[1:])
             if a in stock and b in stock]
    xs, ys = zip(*pairs)
    return statistics.linear_regression(xs, ys).slope, statistics.correlation(xs, ys) ** 2, len(xs)


# ── the fit ──────────────────────────────────────────────────────────────────

def test_beta_and_r_squared_match_excel():
    ends, spy, stock = _market()
    got = beta.compute(ends, spy, stock, first_close=ends[0])
    slope, rsq, n = _excel(ends, spy, stock)
    check("104 weeks", got["weeks_used"] == n == 104, str(got))
    check("SLOPE", abs(got["beta"] - slope) < 1e-12, f"{got['beta']} vs {slope}")
    check("RSQ", abs(got["r_squared"] - rsq) < 1e-12, f"{got['r_squared']} vs {rsq}")
    check("window", (got["window_start"], got["window_end"]) == (ends[0], ends[-1]), str(got))
    check("two years", got["years"] == 2.0, str(got))
    check("no reason", got["reason"] is None)


def test_only_the_last_105_week_ends_are_used():
    ends, spy, stock = _market(n=130)
    got = beta.compute(ends, spy, stock, first_close=ends[0])
    check("window start", got["window_start"] == ends[-105], str(got))
    slope, _, _ = _excel(ends[-105:], spy, stock)
    check("same as the last 105 alone", abs(got["beta"] - slope) < 1e-12)


def test_a_missing_close_drops_both_weeks_it_touches_and_fills_nothing():
    ends, spy, stock = _market()
    del stock[ends[50]]
    got = beta.compute(ends, spy, stock, first_close=ends[0])
    slope, _, n = _excel(ends, spy, stock)
    check("102 weeks", got["weeks_used"] == n == 102, str(got))
    check("still Excel's slope", abs(got["beta"] - slope) < 1e-12)


def test_blank_reasons_in_order():
    ends, spy, stock = _market()
    got = beta.compute(ends, spy, stock, first_close=ends[0], split="2026-10-21")
    check("split first", (got["beta"], got["reason"]) == (None, "split_since_refresh"), str(got))
    got = beta.compute(ends, spy, stock, first_close=ends[-52])
    check("short history", (got["beta"], got["reason"]) == (None, "short_history"), str(got))
    thin = {d: c for i, (d, c) in enumerate(stock.items()) if i % 3}
    got = beta.compute(ends, spy, thin, first_close=ends[0])
    check("not enough weeks", (got["beta"], got["reason"]) == (None, "not_enough_weeks"), str(got))
    check("weeks still counted", 0 < got["weeks_used"] < 80, str(got))


def test_a_younger_stock_is_fitted_over_the_weeks_it_has():
    """Owner, 31 Oct 2026: one to two years of prices gets a beta, with its span."""
    ends, spy, stock = _market()
    listed = (date.fromisoformat(ends[16]) - timedelta(days=2)).isoformat()    # a Wednesday
    young = {d: c for d, c in stock.items() if d >= listed}
    got = beta.compute(ends, spy, young, first_close=listed)
    slope, rsq, n = _excel(ends[16:], spy, young)
    check("from its first week-end", got["window_start"] == ends[16] and got["window_end"] == ends[-1], str(got))
    check("88 weeks, 1.7 years", (got["weeks_used"], got["years"]) == (88, 1.7) and n == 88, str(got))
    check("Excel's slope and RSQ", abs(got["beta"] - slope) < 1e-12 and abs(got["r_squared"] - rsq) < 1e-12)


def test_a_year_is_the_least_and_gaps_count_the_same_share():
    ends, spy, stock = _market()
    got = beta.compute(ends, spy, stock, first_close=ends[-53])
    check("52 weeks: a beta", got["beta"] is not None and got["years"] == 1.0, str(got))
    got = beta.compute(ends, spy, stock, first_close=ends[-52])
    check("51 weeks: blank", got["reason"] == "short_history", str(got))
    held = {d: c for d, c in stock.items() if d >= ends[-89]}
    del held[ends[-80]], held[ends[-70]], held[ends[-60]], held[ends[-50]], held[ends[-40]], held[ends[-30]],         held[ends[-20]], held[ends[-10]], held[ends[-5]], held[ends[-3]], held[ends[-85]]
    got = beta.compute(ends, spy, held, first_close=ends[-89])
    check("88 possible, 66 used: more than 24 of 104 missing", got["reason"] == "not_enough_weeks", str(got))


def test_eighty_weeks_is_enough():
    ends, spy, stock = _market()
    held = dict(stock)
    for d in ends[1:25:2]:          # 12 closes gone: 24 weeks lost, 80 left
        del held[d]
    got = beta.compute(ends, spy, held, first_close=ends[0])
    check("80 used", got["weeks_used"] == 80 and got["beta"] is not None, str(got))


def test_a_price_that_never_moves_has_beta_zero_and_no_r_squared():
    ends, spy, _ = _market()
    got = beta.compute(ends, spy, dict.fromkeys(ends, 10.0), first_close=ends[0])
    check("zero", got["beta"] == 0.0 and got["r_squared"] is None, str(got))


def test_spy_against_itself_is_one():
    ends, spy, _ = _market()
    got = beta.compute(ends, spy, spy, first_close=ends[0])
    check("one", abs(got["beta"] - 1) < 1e-12 and abs(got["r_squared"] - 1) < 1e-12, str(got))


# ── which weeks ──────────────────────────────────────────────────────────────

def test_week_ends_are_spys_last_close_of_each_week():
    days = ["2026-03-30", "2026-03-31", "2026-04-01", "2026-04-02",          # Good Friday 3 Apr
            "2026-04-06", "2026-04-07", "2026-04-08", "2026-04-09", "2026-04-10"]
    check("holiday Thursday, then Friday", beta.week_ends(days) == ["2026-04-02", "2026-04-10"],
          str(beta.week_ends(days)))


def test_a_week_is_not_used_until_its_friday_close_is_stored():
    """A late or failed Friday run must not make Thursday the week's end."""
    week = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23",
            "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29"]
    check("unfinished week left out", beta.week_ends(week) == ["2026-10-23"], str(beta.week_ends(week)))
    check("finished with Friday", beta.week_ends(week + ["2026-10-30"])[-1] == "2026-10-30")


def test_todays_close_counts_only_after_the_run_has_settled():
    days = ["2026-10-29", "2026-10-30"]
    friday = datetime(2026, 10, 30, tzinfo=ET)
    check("during the run", beta.settled(days, friday.replace(hour=17)) == ["2026-10-29"])
    check("after 7 PM", beta.settled(days, friday.replace(hour=19, minute=1)) == days)
    check("next day", beta.settled(days, datetime(2026, 10, 31, 9, tzinfo=ET)) == days)


def test_a_beta_lasts_until_the_next_price_run():
    thursday = datetime(2026, 10, 29, 10, tzinfo=ET)
    check("4:30 PM", beta.expires_at(thursday) == thursday.replace(hour=16, minute=30).astimezone(timezone.utc))
    friday_night = datetime(2026, 10, 30, 20, tzinfo=ET)
    check("Monday", beta.expires_at(friday_night).astimezone(ET).date() == date(2026, 11, 2))
    in_run = datetime(2026, 10, 30, 17, tzinfo=ET)
    check("five minutes in a run", beta.expires_at(in_run) == (in_run + timedelta(minutes=5)).astimezone(timezone.utc))


# ── reading it ───────────────────────────────────────────────────────────────

def _install(monkeypatch, reads):
    ends, spy, stock = _market()
    beta._CACHE.clear()
    beta._CALENDAR.clear()
    # SPY's daily rows: the week-end closes plus a Wednesday in each week.
    daily = [{"date": d, "close": c} for d, c in spy.items()]
    daily += [{"date": (date.fromisoformat(d) - timedelta(days=2)).isoformat(), "close": c} for d, c in spy.items()]

    def history(symbol, limit=1260):
        reads.append(("history", symbol))
        assert symbol == "SPY"
        return sorted(daily, key=lambda r: r["date"])[-limit:]

    def closes_on(symbol, dates):
        reads.append(("closes", symbol))
        return {d: stock[d] for d in dates if d in stock}

    monkeypatch.setattr(storage, "get_prices_history", history)
    monkeypatch.setattr(beta, "_closes_on", closes_on)
    monkeypatch.setattr(beta, "_first_close", lambda s: "2021-11-01")
    monkeypatch.setattr(share_counts, "split_since_refresh", lambda s: None)
    return ends, spy, stock


def test_for_symbol_reads_once_then_from_memory(monkeypatch):
    reads = []
    ends, spy, stock = _install(monkeypatch, reads)
    got = REAL_FOR_SYMBOL("KO")
    slope, rsq, _ = _excel(ends, spy, stock)
    check("Excel's slope", abs(got["beta"] - slope) < 1e-12, str(got))
    check("window ends on the last Friday", got["window_end"] == ends[-1], str(got))
    n = len(reads)
    REAL_FOR_SYMBOL("KO")
    check("second time reads nothing", len(reads) == n, str(reads))


def test_unreadable_prices_give_a_blank_never_an_error(monkeypatch):
    reads = []
    _install(monkeypatch, reads)

    def broken(symbol, dates):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(beta, "_closes_on", broken)
    check("blank", REAL_FOR_SYMBOL("KO") == dict.fromkeys(beta.FIELDS))


# ── Yahoo's beta: saved under its own name, never shown ──────────────────────

def _info(monkeypatch, info):
    class T:
        def __init__(self, symbol):
            self.info = info
    monkeypatch.setattr(fetchers.yf, "Ticker", T)
    return fetchers.fetch_ticker_info("KO")


def test_the_weekly_profile_keeps_yahoos_beta_under_its_own_name(monkeypatch):
    meta = _info(monkeypatch, {"symbol": "KO", "shortName": "Coca-Cola", "beta": 0.43})
    check("kept", meta["yahoo_beta"] == 0.43 and len(meta["yahoo_beta_as_of"]) == 10, str(meta))
    check("never as ours", "beta" not in meta and "beta_source" not in meta, str(meta))
    for missing in ({}, {"beta": None}, {"beta": float("nan")}, {"beta": True}):
        meta = _info(monkeypatch, {"symbol": "KO", "shortName": "Coca-Cola", **missing})
        check(f"nothing for {missing}", "yahoo_beta" not in meta, str(meta))


def test_the_website_cell_shows_the_span_beside_the_number(monkeypatch):
    def no_yahoo(symbol):
        raise AssertionError("the beta row asked Yahoo")
    monkeypatch.setattr(terminal, "_yahoo", no_yahoo)
    fits = {"AAPL": {"beta": 1.043, "years": 2.0}, "SNDK": {"beta": 1.431, "years": 1.7},
            "SPCX": {"reason": "short_history"}}
    monkeypatch.setattr(beta, "for_symbol", lambda s: {**dict.fromkeys(beta.FIELDS), **fits[s]})
    check("two years", terminal._beta("AAPL") == ("1.04 2Y", "TEK2day's own beta: 2 years of weekly returns vs SPY"),
          str(terminal._beta("AAPL")))
    check("1.7 years", terminal._beta("SNDK")[0] == "1.43 1.7Y", str(terminal._beta("SNDK")))
    check("blank says why", terminal._beta("SPCX") == ("N/A", "Under 1 year of prices"))
