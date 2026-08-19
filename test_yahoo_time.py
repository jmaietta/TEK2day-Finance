"""Yahoo's quote timestamp must survive whatever type Yahoo sends.

⚠️ WHY THIS FILE EXISTS. On 13 Aug 2026 two upstream changes lined up: yfinance
began returning `regularMarketTime` as a pandas Timestamp instead of an int, and
pandas 3 made `Timestamp + int` a TypeError. The line `ts + offset` existed in
FOUR places. Every one of 9,911 tickers failed nightly, the job reported
SUCCESS, and Firestore's prices sat frozen for a week.

The type will change again. These tests fail in CI when it does, instead of
silently in production.
"""
import math
from datetime import date, datetime, timezone

import fetchers


# 2026-08-19 20:00:00 UTC. New York is UTC-4, so with the exchange offset
# applied this is still the 19th in local terms — a bar stamped after the close
# belongs to the session that just ended, not to tomorrow.
EPOCH = 1787169600
NY_OFFSET = -4 * 3600


def check(label, condition, detail=""):
    assert condition, f"{label}: {detail}"


# ── the types Yahoo has actually been observed to send ──────────────────────

def test_int_timestamp():
    """The original contract. Must never regress."""
    assert fetchers.yahoo_local_date(EPOCH, NY_OFFSET) == date(2026, 8, 19)


def test_float_timestamp():
    assert fetchers.yahoo_local_date(float(EPOCH), NY_OFFSET) == date(2026, 8, 19)


def test_numeric_string_timestamp():
    assert fetchers.yahoo_local_date(str(EPOCH), NY_OFFSET) == date(2026, 8, 19)


def test_datetime_timestamp():
    dt = datetime.fromtimestamp(EPOCH, tz=timezone.utc)
    assert fetchers.yahoo_local_date(dt, NY_OFFSET) == date(2026, 8, 19)


def test_pandas_timestamp_is_the_one_that_broke_production():
    """⚠️ THE REGRESSION TEST. `ts + offset` raised TypeError on this type and
    `int(ts)` raises too — `.timestamp()` is the only accessor that returns
    SECONDS for both datetime and pandas Timestamp."""
    pd = __import__("pandas")
    ts = pd.Timestamp(EPOCH, unit="s", tz="UTC")
    assert fetchers.yahoo_local_date(ts, NY_OFFSET) == date(2026, 8, 19)


def test_int_would_have_been_the_wrong_fix():
    """Pins WHY the helper does not just call int(). Coercing a pandas
    Timestamp raises, and a caller that swallows it loses the quote silently —
    which is exactly what app.py did while the price job died loudly."""
    pd = __import__("pandas")
    ts = pd.Timestamp(EPOCH, unit="s", tz="UTC")
    raised = False
    try:
        int(ts)
    except TypeError:
        raised = True
    assert raised, "int() on a pandas Timestamp no longer raises; revisit the helper"
    assert fetchers.yahoo_local_date(ts, NY_OFFSET) == date(2026, 8, 19)


# ── the offset is not decoration ────────────────────────────────────────────

def test_offset_moves_the_date_across_the_boundary():
    """20:00 UTC is the 19th in New York and already the 20th in Tokyo. Getting
    this wrong files a bar under the wrong trading day."""
    assert fetchers.yahoo_local_date(EPOCH, NY_OFFSET) == date(2026, 8, 19)
    assert fetchers.yahoo_local_date(EPOCH, 9 * 3600) == date(2026, 8, 20)


def test_missing_offset_defaults_to_utc():
    assert fetchers.yahoo_local_date(EPOCH) == date(2026, 8, 19)
    assert fetchers.yahoo_local_date(EPOCH, None) == date(2026, 8, 19)


# ── refusal, never a guess ──────────────────────────────────────────────────

def test_unusable_values_return_none():
    for bad in (None, "", "not-a-time", object(), [], {}):
        assert fetchers.yahoo_local_date(bad, NY_OFFSET) is None, repr(bad)


def test_non_finite_is_refused():
    """The same presence-vs-finiteness confusion that has surfaced seven times
    in this codebase. NaN is not None."""
    for bad in (float("nan"), float("inf"), float("-inf")):
        assert fetchers.yahoo_local_date(bad, NY_OFFSET) is None, repr(bad)


def test_booleans_are_not_timestamps():
    """True is an int in Python and would silently mean 1 Jan 1970."""
    assert fetchers.yahoo_local_date(True, NY_OFFSET) is None
    assert fetchers.yahoo_local_date(False, NY_OFFSET) is None


def test_out_of_range_does_not_crash():
    assert fetchers.yahoo_local_date(10**20, NY_OFFSET) is None


# ── the bar builder that actually failed in production ──────────────────────

def test_bar_from_metadata_survives_a_pandas_timestamp():
    """The exact production failure: fetchers.py line 138, every ticker, nightly."""
    pd = __import__("pandas")
    meta = {
        "regularMarketTime": pd.Timestamp(EPOCH, unit="s", tz="UTC"),
        "regularMarketPrice": 51.54,
        "regularMarketDayHigh": 51.57,
        "regularMarketDayLow": 48.98,
        "regularMarketVolume": 6451791,
        "gmtoffset": NY_OFFSET,
    }
    bar = fetchers._bar_from_metadata(meta, "AA")
    assert bar is not None, "the provisional bar must still be built"
    assert bar["date"] == "2026-08-19"
    assert bar["close"] == 51.54
    assert bar["open"] is None, "metadata carries no open; it stays None by design"


def test_bar_from_metadata_refuses_an_unusable_timestamp():
    """A bad timestamp must yield no bar, never a bar filed under the wrong day."""
    meta = {
        "regularMarketTime": "not-a-time",
        "regularMarketPrice": 51.54,
        "gmtoffset": NY_OFFSET,
    }
    assert fetchers._bar_from_metadata(meta, "AA") is None


# ── one copy, not four ──────────────────────────────────────────────────────

def test_no_surface_reimplements_the_arithmetic():
    """⚠️ THE POINT OF THE WHOLE FIX. This line lived in four places; when the
    type changed, app.py happened to be written differently and the other two
    broke. The status doc's own ruling was "Do not reintroduce a second copy."
    It got reintroduced. This test fails if it happens again."""
    import ast
    import pathlib

    def looks_like(node, *words):
        """The name of a variable in this expression, if it mentions any word."""
        name = getattr(node, "id", None) or getattr(node, "attr", None)
        return bool(name) and any(w in name.lower() for w in words)

    root = pathlib.Path(__file__).parent
    offenders = []
    # Parsed, not grepped: a line-based scan trips over its own documentation,
    # and a comment explaining the bug is not the bug.
    for name in ("app.py", "terminal.py", "fetchers.py", "storage.py", "partner_api.py"):
        path = root / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=name)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add)):
                continue
            sides = (node.left, node.right)
            has_time = any(looks_like(s, "ts", "timestamp", "epoch") for s in sides)
            has_offset = any(looks_like(s, "offset", "gmtoffset") for s in sides)
            if has_time and has_offset:
                offenders.append("%s:%d" % (name, node.lineno))
    assert not offenders, (
        "timestamp+offset arithmetic outside fetchers.yahoo_local_date at:\n  "
        + "\n  ".join(offenders)
        + "\nUse fetchers.yahoo_local_date. This line broke production once."
    )
