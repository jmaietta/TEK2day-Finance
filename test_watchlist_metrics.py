"""Tests for the pure watchlist metric maths.

No Firestore, no network. Rows are built by hand so each assertion pins one
behaviour, and the awkward cases — thin history, a null close, a loss-making
forward estimate — are the point rather than an afterthought.
"""
from __future__ import annotations

import watchlist_metrics as wm


def _prices(closes, start_day=1):
    """Ascending rows, as get_prices_history returns them."""
    return [
        {"symbol": "T", "date": f"2026-01-{start_day + i:02d}", "close": close}
        for i, close in enumerate(closes)
    ]


def test_moving_average_needs_a_full_window():
    assert wm.moving_average([1, 2, 3]) is None
    assert wm.moving_average(list(range(1, 11))) == 5.5


def test_moving_average_uses_only_the_last_ten_closes():
    closes = [100.0] * 20 + [200.0] * 10
    assert wm.moving_average(closes) == 200.0


def test_trend_reports_above_or_below_the_average():
    rising = wm.build_row("T", "Test", _prices([10.0] * 9 + [50.0]), [])
    assert rising["trend"] == "above"
    assert rising["ma_10"] == 14.0

    falling = wm.build_row("T", "Test", _prices([50.0] * 9 + [10.0]), [])
    assert falling["trend"] == "below"


def test_thin_history_yields_nulls_not_a_wrong_average():
    row = wm.build_row("T", "Test", _prices([10.0, 11.0, 12.0]), [])

    assert row["covered"] is True
    assert row["price"] == 12.0
    assert row["ma_10"] is None
    assert row["trend"] is None
    assert row["change_1m_pct"] is None


def test_a_null_close_is_dropped_rather_than_treated_as_zero():
    rows = _prices([10.0] * 10)
    rows[4]["close"] = None

    row = wm.build_row("T", "Test", rows, [])

    # Nine usable closes is short of the window, so no average is reported.
    assert row["ma_10"] is None
    assert row["price"] == 10.0


def test_no_price_history_is_uncovered():
    row = wm.build_row("T", None, [], [])

    assert row["covered"] is False
    assert row["price"] is None
    assert row["forward_pe"] is None


def test_one_month_and_three_month_changes_use_trading_sessions():
    closes = [100.0] * 64
    closes[-1] = 110.0
    row = wm.build_row("T", "Test", _prices(closes), [])

    assert row["change_1m_pct"] == 10.0
    assert row["change_3m_pct"] == 10.0


def test_forward_pe_uses_the_estimate_in_effect_at_the_time():
    closes = [100.0] * 64
    prices = _prices(closes)
    estimates = [
        {"symbol": "T", "date": prices[0]["date"], "eps_avg": 4.0},
        {"symbol": "T", "date": prices[-1]["date"], "eps_avg": 5.0},
    ]

    row = wm.build_row("T", "Test", prices, estimates)

    # Today: 100 / 5. A quarter ago the older consensus stood: 100 / 4.
    assert row["forward_pe"] == 20.0
    assert row["forward_pe_prior_q"] == 25.0
    assert row["forward_pe_change_q_pct"] == -20.0


def test_eps_avg_is_a_period_keyed_map_not_a_number():
    """The bug this replaced: eps_avg is a dict, so every multiple came back null.

    Stored rows look like {"eps_avg": {"0q": .., "+1q": .., "0y": .., "+1y": ..}}.
    Forward means +1y, matching terminal._latest_forward_eps, so the same label
    does not carry a different multiple here than on the terminal or the website.
    """
    prices = _prices([100.0] * 12)
    estimates = [{
        "date": prices[-1]["date"],
        "eps_avg": {"0q": 2.083, "+1q": 2.352, "0y": 8.996, "+1y": 12.890},
    }]

    row = wm.build_row("T", "Test", prices, estimates)

    assert row["forward_eps"] == 12.890
    assert round(row["forward_pe"], 4) == round(100.0 / 12.890, 4)


def test_forward_eps_falls_back_to_the_current_year():
    prices = _prices([100.0] * 12)
    estimates = [{"date": prices[-1]["date"], "eps_avg": {"0q": 1.0, "0y": 5.0}}]

    assert wm.build_row("T", "T", prices, estimates)["forward_eps"] == 5.0


def test_forward_eps_reads_the_plus_spelled_key():
    """Yahoo has produced 'plus1y' as well as '+1y'; terminal handles both."""
    prices = _prices([100.0] * 12)
    estimates = [{"date": prices[-1]["date"], "eps_avg": {"plus1y": 4.0, "0y": 9.0}}]

    assert wm.build_row("T", "T", prices, estimates)["forward_eps"] == 4.0


def test_forward_eps_reads_per_period_maps():
    prices = _prices([100.0] * 12)
    estimates = [{"date": prices[-1]["date"], "eps_+1y": {"avg": 6.0}}]

    assert wm.build_row("T", "T", prices, estimates)["forward_eps"] == 6.0


def test_a_loss_making_forward_estimate_reports_no_multiple():
    prices = _prices([100.0] * 12)
    estimates = [{"symbol": "T", "date": prices[-1]["date"], "eps_avg": -2.0}]

    row = wm.build_row("T", "Test", prices, estimates)

    assert row["forward_eps"] == -2.0
    assert row["forward_pe"] is None


def test_compression_and_re_rating_are_distinguishable():
    """The pairing the whole row exists for: same price move, opposite meaning."""
    prices = _prices([100.0] * 63 + [120.0])
    old_date = prices[0]["date"]
    new_date = prices[-1]["date"]

    compressing = wm.build_row("T", "T", prices, [
        {"date": old_date, "eps_avg": 4.0},
        {"date": new_date, "eps_avg": 8.0},
    ])
    re_rating = wm.build_row("T", "T", prices, [
        {"date": old_date, "eps_avg": 4.0},
        {"date": new_date, "eps_avg": 4.0},
    ])

    assert compressing["change_3m_pct"] == 20.0
    assert re_rating["change_3m_pct"] == 20.0
    assert compressing["forward_pe_change_q_pct"] < 0
    assert re_rating["forward_pe_change_q_pct"] > 0


def test_estimates_dated_after_the_price_are_ignored():
    prices = _prices([100.0] * 12)
    estimates = [{"date": "2027-01-01", "eps_avg": 10.0}]

    row = wm.build_row("T", "Test", prices, estimates)

    assert row["forward_eps"] is None
    assert row["forward_pe"] is None


def test_malformed_rows_never_raise():
    row = wm.build_row("T", "Test", [None, {"close": "abc"}, {"date": 5}], [None, {"eps_avg": "x"}])

    assert row["covered"] is False
