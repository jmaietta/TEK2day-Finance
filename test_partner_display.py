#!/usr/bin/env python3
"""Tests for the partner `display` block.

    python test_partner_display.py

No network, no Firestore.

WHY THE BLOCK EXISTS. A partner that formats our numbers itself reimplements
our rules — when a figure becomes T rather than B, how many decimals a ratio
gets, the "x" suffix. The day those drift, two products show the same company
differently. Market cap disagreeing by $40bn was a definition problem worth
fixing; market cap disagreeing by a rounding rule would be a silly one.

So the raw value and the rendered string ship together, and the string is made
with the website's own formatters.
"""
import sys

import partner_api

_passed = 0
_failed = []


def check(name, condition, detail=""):
    global _passed
    if condition:
        _passed += 1
    else:
        _failed.append(f"{name}{(' — ' + detail) if detail else ''}")


SAMPLE = {
    "quote": {
        "price": 305.93, "change": 0.67, "change_pct": 0.2195,
        "volume": 26_072_932.0, "day_high": 307.49, "day_low": 304.30,
        "fifty_two_week_high": 344.57, "fifty_two_week_low": 223.78,
    },
    "valuation": {
        "market_cap": 4_501_660_828_680.0, "enterprise_value": 4_546_460_828_680.0,
        "pe_ttm": 34.9155, "forward_pe": 32.0858, "ps_ttm": 9.6432,
        "ev_revenue": 9.7392, "ev_ebitda": 27.0689, "ev_opcf": 30.9865,
        "ev_fcf": 33.2628,
    },
    "fundamentals": {
        "revenue": 466_823_000_000.0, "ebitda": 167_959_000_000.0,
        "net_income": 128_930_000_000.0, "eps_ttm": 8.762, "forward_eps": 9.5347,
        "diluted_shares": 14_714_676_000.0, "beta": 1.086, "dividend_yield": 0.35,
    },
}


def test_renders_the_real_apple_record():
    d = partner_api._display(SAMPLE)
    expected = {
        ("quote", "price"): "$305.93",
        ("quote", "volume"): "26.1M",
        ("valuation", "market_cap"): "$4.50T",
        ("valuation", "pe_ttm"): "34.9x",
        ("fundamentals", "revenue"): "$466.82B",
        ("fundamentals", "eps_ttm"): "$8.76",
        ("fundamentals", "diluted_shares"): "14.71B",
        ("fundamentals", "beta"): "1.09",
    }
    for (section, field), want in expected.items():
        got = d[section][field]
        check(f"{section}.{field} renders {want}", got == want, f"got {got!r}")


def test_dividend_yield_is_not_multiplied():
    """AAPL stores 0.35 meaning 0.35%. terminal._pct would multiply by 100 and
    render "35.00%", overstating Apple's yield a hundredfold."""
    d = partner_api._display(SAMPLE)
    got = d["fundamentals"]["dividend_yield"]
    check("dividend yield stays 0.35%", got == "0.35%", f"got {got!r}")
    check("dividend yield is not 35%", got != "35.00%")


def test_change_pct_carries_a_sign():
    d = partner_api._display(SAMPLE)
    check("positive change is signed", d["quote"]["change_pct"] == "+0.22%",
          str(d["quote"]["change_pct"]))
    down = {**SAMPLE, "quote": {**SAMPLE["quote"], "change_pct": -0.62}}
    check("negative change is signed", partner_api._display(down)["quote"]["change_pct"] == "-0.62%")


def test_missing_stays_null_not_the_string_na():
    """How to draw an absent value is the consumer's call — Kilby draws an em
    dash. A string "N/A" here would be indistinguishable from a real value."""
    empty = {"quote": {}, "valuation": {}, "fundamentals": {}}
    d = partner_api._display(empty)
    for section in ("quote", "valuation", "fundamentals"):
        for field, value in d[section].items():
            check(f"absent {section}.{field} is null", value is None, f"got {value!r}")


def test_nan_is_treated_as_missing():
    """Firestore stores Yahoo's NaN verbatim; it must not render as a number."""
    nan = float("nan")
    d = partner_api._display({"quote": {"price": nan}, "valuation": {"market_cap": nan},
                              "fundamentals": {}})
    check("NaN price is null", d["quote"]["price"] is None)
    check("NaN market cap is null", d["valuation"]["market_cap"] is None)


def test_zero_is_rendered_not_dropped():
    """Zero is a real value. Treating it as missing is the FS7 mistake in
    reverse — and a genuinely zero figure must still show."""
    d = partner_api._display({"quote": {"change": 0.0}, "valuation": {}, "fundamentals": {}})
    check("zero renders", d["quote"]["change"] == "$0.00", str(d["quote"]["change"]))


ESTIMATES = {
    "revenue": {
        "avg": {"0q": 113_550_860_190.0, "+1q": 154_215_461_860.0},
        "growth": {"0q": 0.1082, "+1q": 0.0728},
        "numberofanalysts": {"0q": 28.0, "+1q": 21.0},
        "currency": {"0q": "USD"},
    },
    "eps": {
        "avg": {"0q": 1.97656, "0y": 8.80268},
        "growth": {"0q": 0.0684},
        "yearagoeps": {"0q": 1.85},
    },
    "periods": {"0q": "Curr Q"},
    "rolling": True,
}


def test_estimates_render_to_the_same_strings_the_card_shows():
    """Without these, Kilby formats the estimates table with its own rules and
    the drift simply moves from the quote to the estimates."""
    d = partner_api._display({**SAMPLE, "estimates": ESTIMATES})
    est = d["estimates"]
    check("revenue avg 0q", est["revenue"]["avg"]["0q"] == "$113.55B",
          str(est["revenue"]["avg"]["0q"]))
    check("revenue avg +1q", est["revenue"]["avg"]["+1q"] == "$154.22B")
    check("eps avg 0q", est["eps"]["avg"]["0q"] == "$1.98", str(est["eps"]["avg"]["0q"]))
    check("eps avg 0y", est["eps"]["avg"]["0y"] == "$8.80")


def test_estimate_growth_is_a_fraction_and_does_multiply():
    """Unlike dividend_yield, growth IS stored as a fraction — NVDA's 0.4257 is
    the "roughly 43%" figure. Getting these two backwards is the whole risk."""
    d = partner_api._display({**SAMPLE, "estimates": ESTIMATES})
    got = d["estimates"]["revenue"]["growth"]["0q"]
    check("growth 0.1082 renders 10.82%", got == "10.82%", str(got))


def test_analyst_counts_are_whole_numbers():
    d = partner_api._display({**SAMPLE, "estimates": ESTIMATES})
    got = d["estimates"]["revenue"]["numberofanalysts"]["0q"]
    check("28.0 renders as 28", got == "28", str(got))


def test_non_numeric_estimate_metrics_are_left_out():
    """Currency is already a string; formatting it would be meaningless."""
    d = partner_api._display({**SAMPLE, "estimates": ESTIMATES})
    check("currency omitted from display", "currency" not in d["estimates"]["revenue"])


def test_absent_estimates_produce_null_not_an_empty_shell():
    d = partner_api._display(SAMPLE)
    check("no estimates -> null", d.get("estimates") is None, str(d.get("estimates")))


def test_structure_mirrors_the_data_exactly():
    """A consumer reads data.valuation.market_cap to calculate and
    display.valuation.market_cap to render. If the shapes diverge it has to
    learn two maps."""
    d = partner_api._display(SAMPLE)
    for section in ("quote", "valuation", "fundamentals"):
        check(f"{section} keys match", set(d[section]) == set(SAMPLE[section]),
              f"data={sorted(SAMPLE[section])} display={sorted(d[section])}")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    total = _passed + len(_failed)
    if _failed:
        print(f"FAILED {len(_failed)}/{total}")
        for f in _failed:
            print(f"  - {f}")
        return 1
    print(f"{_passed}/{total} tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
