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


def test_the_series_is_the_last_month_of_closes_oldest_first():
    row = wm.build_row("T", "T", _prices([float(i) for i in range(1, 41)]), [])

    assert len(row["series"]) == wm.SERIES_SESSIONS
    assert row["series"][0]["close"] == 40 - wm.SERIES_SESSIONS + 1
    assert row["series"][-1]["close"] == 40.0


def test_the_series_holds_raw_closes_not_percentages():
    """Rebasing depends on the window drawn, so it belongs at render time."""
    row = wm.build_row("T", "T", _prices([100.0] * 12), [])

    assert all(point["close"] == 100.0 for point in row["series"])


def test_align_series_builds_one_axis_and_positions_every_row_against_it():
    a = wm.build_row("A", "A", _prices([10.0, 11.0, 12.0]), [])
    b = wm.build_row("B", "B", _prices([20.0, 21.0, 22.0]), [])

    dates = wm.align_series([a, b])

    assert dates == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert a["closes"] == [10.0, 11.0, 12.0]
    assert b["closes"] == [20.0, 21.0, 22.0]


def test_a_day_a_company_did_not_trade_is_a_gap_not_a_value():
    """A false point on a chart is worse than a missing one: it looks like data."""
    full = wm.build_row("FULL", "F", _prices([10.0, 11.0, 12.0]), [])
    late = wm.build_row("LATE", "L", _prices([50.0, 51.0], start_day=2), [])

    dates = wm.align_series([full, late])

    assert dates == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert late["closes"] == [None, 50.0, 51.0]
    assert full["closes"] == [10.0, 11.0, 12.0]


def test_alignment_puts_the_same_date_at_the_same_position_for_every_row():
    a = wm.build_row("A", "A", _prices([1.0, 2.0, 3.0]), [])
    b = wm.build_row("B", "B", _prices([9.0], start_day=3), [])

    dates = wm.align_series([a, b])
    position = dates.index("2026-01-03")

    assert a["closes"][position] == 3.0
    assert b["closes"][position] == 9.0


def test_an_uncovered_row_aligns_to_all_nulls_rather_than_shortening_the_axis():
    covered = wm.build_row("A", "A", _prices([1.0, 2.0]), [])
    empty = wm.build_row("B", "B", [], [])

    dates = wm.align_series([covered, empty])

    assert empty["closes"] == [None] * len(dates)


def test_the_raw_series_is_removed_once_aligned():
    row = wm.build_row("A", "A", _prices([1.0, 2.0]), [])
    wm.align_series([row])

    assert "series" not in row
    assert "closes" in row


def _fy(year, eps):
    return {"period": f"{year}-FY", "period_end": f"{year}-01-31",
            "income": {"Diluted EPS": eps}}


def test_annual_diluted_eps_is_oldest_first_and_capped():
    docs = [_fy(y, float(y) - 2020) for y in range(2019, 2027)]

    out = wm.annual_diluted_eps(docs)

    assert len(out) == wm.ANNUAL_YEARS
    assert [row["fiscal_year"] for row in out] == ["2022", "2023", "2024", "2025", "2026"]
    assert out[-1]["diluted_eps"] == 6.0


def test_quarters_are_ignored():
    docs = [_fy(2026, 4.9), {"period": "2026-Q2", "income": {"Diluted EPS": 1.2}}]

    out = wm.annual_diluted_eps(docs)

    assert [row["period"] for row in out] == ["2026-FY"]


def test_a_period_without_a_diluted_eps_line_is_skipped_not_zeroed():
    docs = [_fy(2025, 3.0), {"period": "2026-FY", "income": {"Basic EPS": 4.93}}]

    out = wm.annual_diluted_eps(docs)

    assert [row["fiscal_year"] for row in out] == ["2025"]


def test_diluted_is_taken_never_basic():
    docs = [{"period": "2026-FY",
             "income": {"Basic EPS": 4.93, "Diluted EPS": 4.90}}]

    assert wm.annual_diluted_eps(docs)[0]["diluted_eps"] == 4.90


def test_annual_eps_survives_a_company_with_no_price_history():
    """Accounts are filed independently of whether we hold closes."""
    row = wm.build_row("T", "T", [], [], [_fy(2026, 4.9)])

    assert row["covered"] is False
    assert row["annual_diluted_eps"][0]["diluted_eps"] == 4.9


def _est(zero_y, plus_one_y):
    return [{"date": "2026-08-20", "eps_avg": {"0y": zero_y, "+1y": plus_one_y}}]


def test_the_first_estimate_is_the_year_after_the_last_reported_one():
    """Yahoo's 0y is the year in progress, which is the first unreported year."""
    history = wm.annual_diluted_eps([_fy(2025, 18.21)])

    out = wm.annual_eps_outlook(history, _est(20.59, 22.70))

    assert [(r["fiscal_year"], r["diluted_eps"]) for r in out] == [
        ("2026", 20.59), ("2027", 22.70)
    ]


def test_the_same_yahoo_keys_land_on_different_years_for_different_companies():
    """Costco's year ends in August, Microsoft's in June — same keys, later years."""
    costco = wm.annual_eps_outlook(
        wm.annual_diluted_eps([_fy(2025, 18.21)]), _est(20.59, 22.70))
    microsoft = wm.annual_eps_outlook(
        wm.annual_diluted_eps([_fy(2026, 17.95)]), _est(19.71, 23.57))

    assert costco[0]["fiscal_year"] == "2026"
    assert microsoft[0]["fiscal_year"] == "2027"


def test_estimates_leave_no_gap_after_the_reported_years():
    history = wm.annual_diluted_eps([_fy(2024, 1.19), _fy(2025, 2.94), _fy(2026, 4.90)])
    out = wm.annual_eps_outlook(history, _est(8.96, 12.80))

    years = [int(r["fiscal_year"]) for r in history] + [int(r["fiscal_year"]) for r in out]
    assert years == list(range(years[0], years[0] + len(years)))


def test_every_estimate_is_flagged_as_one():
    out = wm.annual_eps_outlook(wm.annual_diluted_eps([_fy(2026, 4.9)]), _est(8.96, 12.80))

    assert all(row["estimated"] is True for row in out)


def test_with_nothing_reported_the_year_is_left_null_rather_than_guessed():
    out = wm.annual_eps_outlook([], _est(8.96, 12.80))

    assert [row["fiscal_year"] for row in out] == [None, None]
    assert [row["diluted_eps"] for row in out] == [8.96, 12.80]


def test_a_missing_period_is_skipped_not_shifted_forward():
    """Dropping 0y must not slide +1y into its year."""
    out = wm.annual_eps_outlook(
        wm.annual_diluted_eps([_fy(2026, 4.9)]),
        [{"date": "2026-08-20", "eps_avg": {"+1y": 12.80}}],
    )

    # +1y stays two years past the last reported year. Sliding it up to 2027
    # would put a two-year-out forecast under next year's heading, which is the
    # mislabelling this whole change exists to correct.
    assert len(out) == 1
    assert out[0]["period"] == "+1y"
    assert out[0]["fiscal_year"] == "2028"


def test_malformed_financial_docs_never_raise():
    assert wm.annual_diluted_eps([None, {"period": "2026-FY"}, {"income": {}}]) == []


def test_malformed_rows_never_raise():
    row = wm.build_row("T", "Test", [None, {"close": "abc"}, {"date": 5}], [None, {"eps_avg": "x"}])

    assert row["covered"] is False
