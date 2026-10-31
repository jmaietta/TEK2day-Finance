#!/usr/bin/env python3
"""Tests for volatility: one year of daily log returns, annualized, kept up to date.

    pytest test_volatility.py

No network, no Firestore. Agreed with Kilby 31 Oct 2026, owner's go the same
day. The figure must equal Kilby's own price_statistics.volatility() window 252
(re-implemented here in pandas, independently of volatility.py), and the
evening run that reads only the changed closes must always land on the figure a
full read would give.
"""
import math
import random
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from google.api_core.exceptions import AlreadyExists, FailedPrecondition
from google.cloud import firestore
from testkit import check

import beta
import partner_api
import share_counts
import storage
import volatility

REAL_FOR_SYMBOLS = volatility.for_symbols    # conftest blanks these for every other test
REAL_MARK = volatility.mark


# ── the reference: Kilby's price_statistics.volatility(), window 252 ─────────

def kilby(spy_dates, closes: dict):
    """Kilby's align() + volatility()["windows"]["252"], with the agreed
    200-of-252 minimum: closes reindexed on SPY's dates, non-positive closes
    dropped, the window counted from the first valid close."""
    col = pd.Series({d: float(c) for d, c in closes.items()}, dtype=float).reindex(pd.Index(sorted(spy_dates)))
    col[col <= 0] = np.nan
    first = col.first_valid_index()
    if first is None:
        return None
    col = col.loc[first:]
    if len(col) < 253:
        return None
    rets = np.log(col / col.shift(1)).iloc[1:].iloc[-252:].dropna()
    if len(rets) < 200:
        return None
    return float(rets.std(ddof=1) * math.sqrt(252))


def _weekdays(n, first=date(2025, 1, 2)):
    out, d = [], first
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _walk(dates, seed, start=50.0, vol=0.02):
    rng, price, out = random.Random(seed), start, {}
    for d in dates:
        price *= math.exp(rng.gauss(0, vol))
        out[d] = price
    return out


# ── the method ───────────────────────────────────────────────────────────────

def test_the_figure_is_kilbys_to_within_a_millionth():
    dates = _weekdays(300)
    window = dates[-253:]
    for seed in range(20):
        closes = _walk(dates, seed, vol=0.005 + seed * 0.003)
        rng = random.Random(seed)
        for d in rng.sample(dates, seed * 2):            # gaps, up to 38 of them
            closes.pop(d)
        got = volatility.compute(window, volatility.returns(window, closes), min(closes))
        want = kilby(dates, closes)
        check(f"seed {seed}", (got["volatility"] is None and want is None)
              or abs(got["volatility"] - want) < 1e-12, f"{got} vs {want}")


def test_a_hand_worked_example_as_excel_does_it():
    window = _weekdays(253)
    closes = {d: 100.0 * (1.01 if i % 2 else 1.0) for i, d in enumerate(window)}   # up 1%, down 1%, ...
    got = volatility.compute(window, volatility.returns(window, closes), window[0])
    r = math.log(1.01)
    xs = [r if i % 2 else -r for i in range(1, 253)]
    mean = sum(xs) / 252
    excel = math.sqrt(sum((x - mean) ** 2 for x in xs) / 251) * math.sqrt(252)      # STDEV.S * SQRT(252)
    check("STDEV.S x SQRT(252)", abs(got["volatility"] - excel) < 1e-15, f"{got['volatility']} vs {excel}")
    check("252 returns, the window's ends", (got["days_used"], got["window_start"], got["window_end"])
          == (252, window[0], window[-1]))


def test_blank_with_the_reason_in_order():
    window = _weekdays(253)
    full = _walk(window, 1)
    compute = volatility.compute
    rets = volatility.returns(window, full)
    check("split first", compute(window, rets, window[0], "2025-11-03")["reason"] == "split_since_refresh")
    check("no prices", compute(window, {}, None)["reason"] == "short_history")
    check("first close after window_start", compute(window, rets, window[1])["reason"] == "short_history")
    check("on window_start is a full year", compute(window, rets, window[0])["volatility"] is not None)
    holes = dict(full)
    for d in window[1:53:2]:                      # 26 closes gone: 52 returns gone, 200 left
        holes.pop(d)
    got = compute(window, volatility.returns(window, holes), window[0])
    check("200 is enough", got["days_used"] == 200 and got["volatility"] is not None, str(got))
    holes.pop(window[100])
    got = compute(window, volatility.returns(window, holes), window[0])
    check("199 is not", (got["days_used"], got["volatility"], got["reason"]) == (198, None, "not_enough_days"),
          str(got))
    flat = compute(window, volatility.returns(window, dict.fromkeys(window, 10.0)), window[0])
    check("a price that never moves: 0", flat["volatility"] == 0.0, str(flat))


def test_a_return_needs_both_closes_on_spy_dates():
    window = _weekdays(4)
    closes = {window[0]: 10.0, window[1]: 11.0, window[3]: 12.0, "2025-01-04": 99.0}    # a Saturday close
    rets = volatility.returns(window, closes)
    check("both closes", rets[window[1]] == math.log(1.1))
    check("missing close: both returns touching it", rets[window[2]] is None and rets[window[3]] is None)
    check("a non-positive close is not a close",
          volatility.returns(window, {**closes, window[1]: 0.0})[window[1]] is None)


# ── a database, for the run ──────────────────────────────────────────────────

class _Snap:
    def __init__(self, db, path, data, update_time=None):
        self.reference = _Ref(db, path)
        self.id, self._data, self.exists, self.update_time = path.split("/")[-1], data, data is not None, update_time

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class _Ref:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.split("/")[-1]

    def get(self, transaction=None):
        self.db.reads += 1
        return _Snap(self.db, self.path, self.db.docs.get(self.path), self.db.times.get(self.path))

    def set(self, data, merge=False):
        if self.db.broken_writes:
            raise RuntimeError("firestore down")
        old = dict(self.db.docs.get(self.path) or {}) if merge else {}
        for key, value in data.items():
            if isinstance(value, firestore.Minimum):
                old[key] = min(old[key], value.value) if isinstance(old.get(key), int) else value.value
            elif isinstance(value, firestore.ArrayUnion):
                old[key] = sorted(set(old.get(key) or []) | set(value.values))
            elif value is firestore.SERVER_TIMESTAMP:
                old[key] = self.db.tick()
            else:
                old[key] = value
        self.db.put(self.path, old)

    def delete(self, option=None):
        if option and self.db.times.get(self.path) != option["last_update_time"]:
            raise FailedPrecondition("changed")
        self.db.docs.pop(self.path, None)
        self.db.times.pop(self.path, None)

    def collection(self, name):
        return _Query(self.db, f"{self.path}/{name}")


class _Batch:
    def __init__(self, db):
        self.db, self.ops = db, []

    def create(self, ref, data):
        self.ops.append(("create", ref, data))

    def set(self, ref, data):
        self.ops.append(("set", ref, data))

    def commit(self):
        if any(op == "create" and ref.path in self.db.docs for op, ref, _ in self.ops):
            raise AlreadyExists("exists")
        for op, ref, data in self.ops:
            self.db.put(ref.path, dict(data))


class _Query:
    def __init__(self, db, name, start=None, first=False):
        self.db, self.name, self.start, self.first = db, name, start, first

    def where(self, filter=None):
        assert (filter.field_path, filter.op_string) == ("date", ">=")
        return _Query(self.db, self.name, filter.value)

    def order_by(self, field):
        return _Query(self.db, self.name, self.start, True)

    def limit(self, n):
        return self

    def select(self, fields):
        return self

    def document(self, id):
        return _Ref(self.db, f"{self.name}/{id}")

    def stream(self):
        prefix = self.name + "/"
        out = [_Snap(self.db, p, d, self.db.times.get(p)) for p, d in sorted(self.db.docs.items())
               if p.startswith(prefix) and "/" not in p[len(prefix):]
               and (self.start is None or p[len(prefix):] >= self.start)]
        out = out[:1] if self.first else out
        self.db.reads += max(len(out), 1)
        if self.name.endswith("/prices"):
            self.db.price_reads += max(len(out), 1)
        return out


class _DB:
    def __init__(self):
        self.docs, self.times, self.clock = {}, {}, 0
        self.reads = self.price_reads = 0
        self.broken_writes = self.broken_reads = False
        self.masks = []

    def tick(self):
        self.clock += 1
        return self.clock

    def put(self, path, data):
        self.docs[path] = data
        self.times[path] = self.tick()

    def document(self, path):
        return _Ref(self, path)

    def collection(self, name):
        return _Query(self, name)

    def batch(self):
        return _Batch(self)

    def write_option(self, last_update_time=None):
        return {"last_update_time": last_update_time}

    def get_all(self, refs, field_paths=None):
        if self.broken_reads:
            raise RuntimeError("firestore down")
        refs = list(refs)
        self.masks.append(field_paths)
        self.reads += len(refs)
        return [_Snap(self, r.path, None if self.docs.get(r.path) is None else
                      {k: v for k, v in self.docs[r.path].items() if not field_paths or k in field_paths},
                      self.times.get(r.path)) for r in refs]


class Market:
    """SPY and some stocks with stored closes, and the evening run against them."""

    def __init__(self, monkeypatch, n=300, stocks=("AAA", "BBB", "CCC")):
        self.db, self.splits, self.routes, self.dates = _DB(), {}, {}, _weekdays(n + 30)
        self.spy_dates, self.later = self.dates[:n], self.dates[n:]
        self.active = list(stocks)
        self._store("SPY", _walk(self.spy_dates, 0, 400.0, 0.01))
        for i, s in enumerate(stocks):
            self._store(s, _walk(self.spy_dates, i + 1, 30.0 + i, 0.01 + 0.01 * i))
        db = self.db
        monkeypatch.setattr(storage, "get_db", lambda: db)
        monkeypatch.setattr(storage, "list_active_tickers", lambda: sorted(self.active))
        monkeypatch.setattr(storage, "ticker_ref", lambda s, db=None: self.db.document(self.root(s)))
        monkeypatch.setattr(storage, "public_symbol", lambda s: s)
        monkeypatch.setattr(storage, "get_prices_history", self.history)
        monkeypatch.setattr(share_counts, "split_since_refresh", lambda s: self.splits.get(s))
        monkeypatch.setattr(volatility, "mark", REAL_MARK)

    def root(self, symbol):
        return self.routes.get(symbol, f"tickers/{symbol}")

    def _store(self, symbol, closes):
        for d, c in closes.items():
            self.db.put(f"{self.root(symbol)}/prices/{d}", {"date": d, "close": c})

    def write(self, symbol, closes):
        """A price write as storage makes it: the closes, then the mark."""
        self._store(symbol, closes)
        volatility.mark(symbol, min(closes))

    def closes(self, symbol):
        prefix = f"{self.root(symbol)}/prices/"
        return {p[len(prefix):]: d["close"] for p, d in self.db.docs.items()
                if p.startswith(prefix) and "/" not in p[len(prefix):]}

    def history(self, symbol, limit=1260):
        rows = sorted((d for p, d in self.db.docs.items() if p.startswith(f"{self.root(symbol)}/prices/")),
                      key=lambda r: r["date"])
        return rows[-limit:]

    def next_day(self, symbols=None, move=0.01):
        """One more trading day: SPY and every stock close, written as the nightly pull writes them."""
        d = self.later.pop(0)
        self.spy_dates.append(d)
        self.write("SPY", {d: self.closes("SPY")[self.spy_dates[-2]] * 1.001})
        for s in (symbols if symbols is not None else self.active):
            last = self.closes(s)[max(self.closes(s))]
            self.write(s, {d: last * (1 + move)})
        return d

    def doc(self, symbol):
        return self.db.docs.get(f"{volatility.STORED}/{symbol}")

    def check_against_full(self, label):
        for s in self.active:
            want = kilby(self.spy_dates, self.closes(s))
            got = self.doc(s)["volatility"]
            check(f"{label}: {s} as a full read", (got is None and want is None)
                  or (got is not None and want is not None and abs(got - want) < 1e-12), f"{got} vs {want}")


# ── the evening run ──────────────────────────────────────────────────────────

def test_the_first_run_reads_every_window_and_matches_kilby(monkeypatch):
    m = Market(monkeypatch)
    out = volatility.run()
    check("all written", (out["written"], out["failed"], out["read_as"]) == (3, 0, {"new": 3}), str(out))
    m.check_against_full("first run")
    doc = m.doc("AAA")
    check("252 returns kept", len(doc["returns"]) == 252 and doc["window_end"] == m.spy_dates[-1], str(doc.keys()))
    check("version 1 in history", m.db.docs.get(f"{volatility.STORED}/AAA/history/{m.spy_dates[-1]}-1") ==
          {k: doc[k] for k in ("symbol", *volatility.FIELDS, "method", "version", "computed_at")})
    check("the run's summary", m.db.docs[volatility.LATEST]["window_end"] == m.spy_dates[-1])


def test_a_night_reads_only_the_new_closes_and_lands_on_the_full_figure(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    for night in range(5):
        m.next_day(move=0.03 * (-1) ** night)
        before = m.db.price_reads
        out = volatility.run()
        per_company = (m.db.price_reads - before - 1) / 3         # SPY's own read aside
        check(f"night {night}: updated", out["read_as"] == {"update": 3}, str(out))
        check(f"night {night}: a handful of closes, not a year", per_company <= 9, str(per_company))
        m.check_against_full(f"night {night}")
    check("marks cleared, SPY's too (not active here)", not [p for p in m.db.docs if p.startswith(volatility.DIRTY + "/")])


def test_a_missed_night_catches_up_and_a_long_gap_reads_it_all(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    m.next_day()
    m.next_day()
    out = volatility.run()
    check("two days at once", out["read_as"] == {"update": 3}, str(out))
    m.check_against_full("missed night")
    for _ in range(21):
        m.next_day()
    out = volatility.run()
    check("over 20 dates: a full read", out["read_as"] == {"gap": 3}, str(out))
    m.check_against_full("long gap")


def test_thursdays_re_adjust_is_read_in_full(monkeypatch):
    """A dividend re-adjust scales the older closes, and the 5-year download
    also fills days that were missing: every window is read again."""
    m = Market(monkeypatch)
    hole, before = m.spy_dates[-100], m.spy_dates[-101]
    m.db.docs.pop(f"tickers/BBB/prices/{hole}")
    volatility.run()
    m.next_day()
    closes = m.closes("BBB")
    refreshed = {d: c * (0.99 if d < m.spy_dates[-30] else 1.0) for d, c in closes.items()}
    refreshed[hole] = closes[before] * 0.99
    m.write("BBB", refreshed)
    out = volatility.run()
    check("re-read in full", out["read_as"] == {"update": 2, "since": 1}, str(out))
    check("the filled day counts", m.doc("BBB")["days_used"] == 252, str(m.doc("BBB")["days_used"]))
    m.check_against_full("after the re-adjust")


def test_an_older_close_changed_is_recomputed_from_its_mark(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    m.next_day()
    d = m.spy_dates[-60]
    m.write("CCC", {d: m.closes("CCC")[d] * 1.2})                # a price fixed by hand, through storage
    out = volatility.run()
    check("updated from the mark", out["read_as"] == {"update": 3}, str(out))
    m.check_against_full("a fixed close")


def test_a_write_that_leaves_no_mark_waits_for_thursday(monkeypatch):
    """Why the Thursday full read is the net: a close changed outside storage
    is not seen until a full read."""
    m = Market(monkeypatch)
    volatility.run()
    d = m.spy_dates[-60]
    m._store("CCC", {d: m.closes("CCC")[d] * 1.2})                 # no mark
    m.next_day()
    volatility.run()
    stale = m.doc("CCC")["volatility"]
    check("not seen yet", abs(stale - kilby(m.spy_dates, m.closes("CCC"))) > 1e-6)
    volatility.run(full=True)
    m.check_against_full("after a full read")


def test_a_lost_mark_is_repaired_the_same_evening_and_alerts(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    m.next_day()
    d = m.spy_dates[-60]
    m._store("AAA", {d: m.closes("AAA")[d] * 1.3})               # the close lands, its mark does not
    m.db.broken_writes = True
    check("mark fails quietly", volatility.mark("AAA", d) is False)
    m.db.broken_writes = False
    m.db.put(volatility.FAILED_MARKS, {"symbols": ["AAA"]})       # what mark's fallback records
    with pytest.raises(RuntimeError, match=r"marks were lost for \['AAA'\]"):
        volatility.run()
    check("read in full", m.db.docs[volatility.LATEST]["read_as"] == {"failed_mark": 1, "update": 2},
          str(m.db.docs[volatility.LATEST]["read_as"]))
    m.check_against_full("after the repair")
    check("record cleared", volatility.FAILED_MARKS not in m.db.docs)
    volatility.run()                                               # the next evening is quiet


def test_mark_never_raises_and_records_what_it_could_not_write(monkeypatch):
    db = _DB()
    monkeypatch.setattr(storage, "get_db", lambda: db)
    check("marked", REAL_MARK("AAA", "2026-10-28") and REAL_MARK("AAA", "2026-10-30"))
    check("the oldest date wins", db.docs[f"{volatility.DIRTY}/AAA"]["since"] == 20261028)

    def down():
        raise RuntimeError("firestore down")
    monkeypatch.setattr(storage, "get_db", down)
    check("never raises", REAL_MARK("BBB", "2026-10-30") is False)
    monkeypatch.setattr(storage, "get_db", lambda: db)
    calls = []
    real = _Ref.set

    def first_fails(self, data, merge=False):
        calls.append(self.path)
        if self.path.startswith(volatility.DIRTY):
            raise RuntimeError("write rejected")
        return real(self, data, merge)
    monkeypatch.setattr(_Ref, "set", first_fails)
    check("recorded as failed", REAL_MARK("CCC", "2026-10-30") is False
          and db.docs[volatility.FAILED_MARKS]["symbols"] == ["CCC"])


def test_a_mark_written_while_the_run_reads_survives_it(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    m.next_day()
    real = volatility._clear

    def late_write(db, snap):
        if snap.id == "AAA":
            d = m.spy_dates[-50]
            m.write("AAA", {d: m.closes("AAA")[d] * 1.1})          # lands after the run read AAA
        real(db, snap)
    monkeypatch.setattr(volatility, "_clear", late_write)
    volatility.run()
    check("still marked", f"{volatility.DIRTY}/AAA" in m.db.docs)
    monkeypatch.setattr(volatility, "_clear", real)
    m.next_day()
    volatility.run()
    m.check_against_full("next evening")


def test_spy_calendar_change_reads_everything_and_alerts(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    gone = m.spy_dates.pop(-40)
    m.db.docs.pop(f"tickers/SPY/prices/{gone}")
    m.next_day()
    with pytest.raises(RuntimeError, match="SPY's calendar changed: 3 windows"):
        volatility.run()
    m.check_against_full("new calendar")


def test_a_changed_route_or_a_price_repair_reads_it_all(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    for d, c in m.closes("AAA").items():                           # a ticker migration copies the history
        m.db.put(f"tickers/AAA-NEW/prices/{d}", {"date": d, "close": c})
    m.routes["AAA"] = "tickers/AAA-NEW"
    m.db.put("price_repairs/abc", {"status": "applied", "writes": [{"data": {"symbol": "BBB"}}]})
    m.next_day()
    out = volatility.run()
    check("route and repair read in full", out["read_as"] == {"route": 1, "price_repair": 1, "update": 1}, str(out))
    check("repair remembered", m.db.docs[volatility.REPAIRS_SEEN]["seen"] == {"abc": "applied"})
    m.next_day()
    out = volatility.run()
    check("then ordinary", out["read_as"] == {"update": 3}, str(out))
    m.check_against_full("after route and repair")


def test_a_split_blanks_it_until_thursday(monkeypatch):
    m = Market(monkeypatch)
    m.splits["AAA"] = m.spy_dates[-2]
    volatility.run()
    check("blank with why", (m.doc("AAA")["volatility"], m.doc("AAA")["reason"]) == (None, "split_since_refresh"))


def test_every_distinct_figure_is_kept_and_an_identical_rerun_is_held(monkeypatch):
    m = Market(monkeypatch)
    volatility.run()
    end = m.spy_dates[-1]
    first = m.doc("AAA")
    out = volatility.run()
    check("identical: held", out["held"] == 3 and out["written"] == 0, str(out))
    check("still serving since the first run", m.doc("AAA")["computed_at"] == first["computed_at"])
    d = m.spy_dates[-10]
    m.write("AAA", {d: m.closes("AAA")[d] * 1.05})                 # a close fixed the same evening
    volatility.run()
    second = m.db.docs.get(f"{volatility.STORED}/AAA/history/{end}-2")
    check("the new figure kept as version 2", second and second["volatility"] != first["volatility"]
          and m.doc("AAA")["version"] == 2, str(second))
    check("version 1 untouched", m.db.docs[f"{volatility.STORED}/AAA/history/{end}-1"]["volatility"]
          == first["volatility"])


def test_more_than_two_percent_failing_fails_the_job(monkeypatch):
    m = Market(monkeypatch)
    monkeypatch.setattr(share_counts, "split_since_refresh",
                        lambda s: (_ for _ in ()).throw(RuntimeError("down")) if s == "BBB" else None)
    with pytest.raises(RuntimeError, match="1 of 3 companies failed"):
        volatility.run()
    check("the others written", m.doc("AAA") and m.doc("CCC") and not m.doc("BBB"))
    check("named in the summary", m.db.docs[volatility.LATEST]["failed_symbols"] == ["BBB"])


def test_storage_marks_every_price_write_even_one_that_fails(monkeypatch):
    marks = []
    monkeypatch.setattr(volatility, "mark", lambda s, oldest: marks.append((s, oldest)) or True)
    monkeypatch.setattr(storage, "public_symbol", lambda s: s)
    monkeypatch.setattr(storage, "_write_prices_batch", lambda s, rows: None)
    storage.write_prices_batch("AAA", [{"date": "2026-10-30", "close": 1}, {"date": "2026-10-26", "close": 1}])

    def broken(s, rows):
        raise RuntimeError("chunk 2 failed")
    monkeypatch.setattr(storage, "_write_prices_batch", broken)
    with pytest.raises(RuntimeError):
        storage.write_prices_batch("BBB", [{"date": "2026-10-29", "close": 1}])
    check("oldest date, and on failure too", marks == [("AAA", "2026-10-26"), ("BBB", "2026-10-29")], str(marks))

    def no_mark(s, oldest):
        raise RuntimeError("mark broke")
    monkeypatch.setattr(volatility, "mark", no_mark)
    monkeypatch.setattr(storage, "_write_prices_batch", lambda s, rows: None)
    storage.write_prices_batch("CCC", [{"date": "2026-10-30", "close": 1}])        # does not raise


# ── reading the table: what the API serves ──────────────────────────────────

def test_the_reader_follows_betas_rules_and_reads_the_figure_alone(monkeypatch):
    db = _DB()
    monkeypatch.setattr(storage, "get_db", lambda: db)
    db.put(f"{volatility.STORED}/AAA", {"symbol": "AAA", "volatility": 0.31, "days_used": 252, "reason": None,
                                         "window_start": "2025-10-30", "window_end": "2026-10-30",
                                         "returns": {"2026-10-30": 0.01}})
    db.put(f"{volatility.STORED}/BAD", {"symbol": "AAA", "volatility": 0.5})
    got = REAL_FOR_SYMBOLS(["AAA", "NEW", "BAD", "GONE", "NOPE"],
                           {"AAA": {"active": True}, "NEW": {"active": True}, "BAD": {"active": True},
                            "GONE": {"active": False}, "NOPE": None})
    check("served", got["AAA"] == {"volatility": 0.31, "days_used": 252, "reason": None,
                                   "window_start": "2025-10-30", "window_end": "2026-10-30"}, str(got["AAA"]))
    check("not reached", got["NEW"]["reason"] == "not_yet_computed")
    check("filed under another symbol", got["BAD"] == volatility.UNAVAILABLE)
    check("inactive and no record: all None, no read",
          got["GONE"] == got["NOPE"] == dict.fromkeys(volatility.FIELDS) and db.reads == 3)
    check("the figure alone, never the returns", db.masks == [volatility._READ_MASK])
    db.broken_reads = True
    check("a failed read: unavailable", REAL_FOR_SYMBOLS(["AAA"], {"AAA": {"x": 1}})["AAA"] == volatility.UNAVAILABLE)


def test_a_volatility_failure_never_touches_the_betas(monkeypatch):
    calls = []
    monkeypatch.setattr(beta, "for_symbols", lambda s, m: calls.append("beta") or
                        {x: {**dict.fromkeys(beta.FIELDS), "beta": 1.2} for x in s})

    def broken(s, m):
        calls.append("volatility")
        raise RuntimeError("volatility read broke")
    monkeypatch.setattr(volatility, "for_symbols", broken)
    betas, vols = partner_api._with_volatility(["AAA"], {"AAA": {"active": True}})
    check("both asked", sorted(calls) == ["beta", "volatility"])
    check("beta served", betas["AAA"]["beta"] == 1.2)
    check("volatility unavailable", vols["AAA"] == volatility.UNAVAILABLE)
    row = partner_api._vol_row(vols["AAA"])
    check("row fields", list(row) == ["volatility_1y", "volatility_1y_days_used", "volatility_1y_reason",
                                      "volatility_1y_window_start", "volatility_1y_window_end",
                                      "volatility_1y_method"] and row["volatility_1y_reason"] == "unavailable")
    check("every field defined", set(row) <= set(partner_api._BETA_DEFINITIONS))
