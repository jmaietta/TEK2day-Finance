#!/usr/bin/env python3
"""Each company's beta: Yahoo's, kept from the weekly refresh, with our own as a
stand-in until Yahoo's arrives.

Decided 30 Oct 2026. The owner wanted Yahoo's beta (the figure investors see
on Yahoo: "Beta (5Y Monthly)") but would not wait a week for the weekly
refresh to reach every company. So:

  - fetchers.fetch_ticker_info keeps Yahoo's `beta` from the answer the
    weekly job already gets (no extra Yahoo call), with beta_source "yahoo";
  - this module works out a stand-in from our stored closes, the same way
    (5 years of monthly moves), against SPY; scripts/seed_standin_beta.py
    writes it once, and never over a Yahoo beta.

As each company's weekly refresh comes in, Yahoo's beta replaces the stand-in.
A fund Yahoo gives no beta for keeps the stand-in.

⚠️ THE STAND-IN IS CLOSE TO YAHOO'S, NOT EQUAL. Yahoo measures against the
S&P 500 index; we measure against SPY's dividend-adjusted closes, and our
latest month may end on a different day from Yahoo's. Expect 1.10 where
Yahoo shows 1.08, not 1.6.
"""
import logging

logger = logging.getLogger("ydp.beta")

BENCHMARK = "SPY"
STANDIN_SOURCE = "tek2day_5y_monthly_vs_spy"
YAHOO_SOURCE = "yahoo"
MONTHS = 60
# Fewer monthly moves than three years is too little to stand in for Yahoo's.
MIN_MONTHS = 36


def month_ends(dates, today=None):
    """The last of `dates` in each calendar month, oldest first, complete months only.

    The newest month counts only when its last weekday is held: a month still
    in progress (or one whose last close is not written yet) would give a
    partial monthly move.
    """
    from datetime import date, timedelta  # noqa: PLC0415

    last = {}
    for d in sorted(dates):
        last[d[:7]] = d
    ends = [last[m] for m in sorted(last)]
    if ends:
        newest = date.fromisoformat(ends[-1])
        nxt = (newest.replace(day=1) + timedelta(days=32)).replace(day=1)
        final = nxt - timedelta(days=1)
        while final.weekday() >= 5:
            final -= timedelta(days=1)
        if newest < final:
            ends.pop()
    return ends


def monthly_beta(stock, spy):
    """{beta, r_squared, months, window_start, window_end} or None.

    `stock`, `spy`: {date: adjusted close}. The month ends are SPY's; a
    monthly move counts only when the stock has a close at both ends of it.
    Ordinary least squares with an intercept, mean-centred.
    """
    ends = month_ends(spy)[-(MONTHS + 1):]
    xs, ys = [], []
    for a, b in zip(ends, ends[1:]):
        if stock.get(a) and stock.get(b) and spy.get(a):
            xs.append(spy[b] / spy[a] - 1)
            ys.append(stock[b] / stock[a] - 1)
    if len(xs) < MIN_MONTHS:
        return None
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if not sxx:
        return None
    return {
        "beta": sxy / sxx,
        "r_squared": (sxy * sxy / (sxx * syy)) if syy else None,
        "months": n,
        "window_start": ends[0],
        "window_end": ends[-1],
    }


def month_end_closes(db, symbol, ends):
    """{date: close} at `ends`: one read per month end, not five years of days.

    Price documents are keyed by date, so the 61 month ends are fetched
    directly. Missing ones are left out.
    """
    import envelope  # noqa: PLC0415
    import storage  # noqa: PLC0415

    prices = storage.ticker_ref(symbol, db=db).collection("prices")
    out = {}
    for snap in db.get_all([prices.document(d) for d in ends]):
        row = snap.to_dict() if snap.exists else None
        if row and envelope.finite(row.get("close")) and row["close"] > 0:
            out[snap.id] = float(row["close"])
    return out
