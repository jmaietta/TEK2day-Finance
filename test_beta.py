#!/usr/bin/env python3
"""Tests for beta: Yahoo's kept from the weekly refresh, ours as a stand-in.

    pytest test_beta.py

No network, no Firestore. Decided 30 Oct 2026; see beta.py.
"""
import json
from pathlib import Path

import numpy as np

from testkit import check

import beta
import fetchers

REAL = json.loads((Path(__file__).parent / "tests/fixtures/beta_month_ends_20261030.json").read_text())


def test_real_stored_closes_give_the_textbook_slope():
    """KO and NVDA from the closes TEK2day actually holds, checked against
    numpy's least-squares fit, which shares no code with beta.py."""
    spy = REAL["SPY"]
    for symbol in ("KO", "NVDA"):
        got = beta.monthly_beta(REAL[symbol], spy)
        ends = REAL["month_ends"]
        xs = [spy[b] / spy[a] - 1 for a, b in zip(ends, ends[1:])]
        ys = [REAL[symbol][b] / REAL[symbol][a] - 1 for a, b in zip(ends, ends[1:])]
        slope, _ = np.polyfit(xs, ys, 1)
        r2 = np.corrcoef(xs, ys)[0, 1] ** 2
        check(f"{symbol} beta", abs(got["beta"] - slope) < 1e-9, f"{got['beta']} vs {slope}")
        check(f"{symbol} r2", abs(got["r_squared"] - r2) < 1e-9, f"{got['r_squared']} vs {r2}")
        check(f"{symbol} months", got["months"] == len(ends) - 1, str(got["months"]))
    check("NVDA moves more than the market", beta.monthly_beta(REAL["NVDA"], spy)["beta"] > 1.5)
    check("KO moves less", beta.monthly_beta(REAL["KO"], spy)["beta"] < 1.0)


def test_a_month_still_in_progress_is_left_out():
    dates = ["2026-08-31", "2026-09-29", "2026-09-30", "2026-10-28", "2026-10-29"]
    check("October dropped", beta.month_ends(dates) == ["2026-08-31", "2026-09-30"], str(beta.month_ends(dates)))
    # 30 Oct 2026 is a Friday, October's last weekday: October is complete.
    check("October kept", beta.month_ends(dates + ["2026-10-30"])[-1] == "2026-10-30")
    # 31 Jan 2026 is a Saturday: Friday the 30th closes January.
    check("weekend month end", beta.month_ends(["2026-01-29", "2026-01-30"]) == ["2026-01-30"])


def _series(n, start=100.0, move=lambda i: 0.01 * ((i % 5) - 2)):
    """n month ends (2020-01 onwards) and closes following `move`."""
    import calendar
    ends, closes, price = [], [], start
    for i in range(n):
        y, m = 2020 + i // 12, i % 12 + 1
        d = calendar.monthrange(y, m)[1]
        while calendar.weekday(y, m, d) >= 5:
            d -= 1
        ends.append(f"{y}-{m:02d}-{d:02d}")
        closes.append(price)
        price *= 1 + move(i)
    return dict(zip(ends, closes))


def test_twice_the_market_is_a_beta_of_two():
    spy = _series(61)
    ends = sorted(spy)
    stock, price = {ends[0]: 50.0}, 50.0
    for a, b in zip(ends, ends[1:]):
        price *= 1 + 2 * (spy[b] / spy[a] - 1)
        stock[b] = price
    got = beta.monthly_beta(stock, spy)
    check("beta 2", abs(got["beta"] - 2.0) < 1e-12, str(got))
    check("r2 1", abs(got["r_squared"] - 1.0) < 1e-12, str(got))
    check("60 moves", got["months"] == 60 and got["window_end"] == ends[-1])


def test_a_missing_month_end_removes_the_two_moves_that_touch_it():
    spy = _series(61)
    stock = dict(spy)
    del stock[sorted(spy)[30]]
    check("58 moves", beta.monthly_beta(stock, spy)["months"] == 58)


def test_too_little_history_gives_no_stand_in():
    spy = _series(61)
    recent = {d: c for d, c in spy.items() if d >= sorted(spy)[-36]}
    check("35 moves: none", beta.monthly_beta(recent, spy) is None)
    recent = {d: c for d, c in spy.items() if d >= sorted(spy)[-37]}
    check("36 moves: one", beta.monthly_beta(recent, spy) is not None)


def test_only_the_last_five_years_count():
    spy = _series(80)
    check("60 moves", beta.monthly_beta(dict(spy), spy)["months"] == 60)


# ── Yahoo's beta from the weekly refresh ─────────────────────────────────────

def _info(monkeypatch, info):
    class T:
        def __init__(self, symbol):
            self.info = info
    monkeypatch.setattr(fetchers.yf, "Ticker", T)
    return fetchers.fetch_ticker_info("KO")


def test_yahoos_beta_is_kept_from_the_answer_already_fetched(monkeypatch):
    meta = _info(monkeypatch, {"symbol": "KO", "shortName": "Coca-Cola", "beta": 0.43})
    check("beta", meta["beta"] == 0.43, str(meta))
    check("source", meta["beta_source"] == "yahoo")
    check("dated", len(meta["beta_as_of"]) == 10)


def test_no_beta_from_yahoo_leaves_the_stand_in_alone(monkeypatch):
    """The record is merged, so a key left out keeps what is stored."""
    for missing in ({}, {"beta": None}, {"beta": float("nan")}):
        meta = _info(monkeypatch, {"symbol": "KO", "shortName": "Coca-Cola", **missing})
        check(f"no beta key for {missing}", not {"beta", "beta_source", "beta_as_of"} & set(meta), str(meta))
