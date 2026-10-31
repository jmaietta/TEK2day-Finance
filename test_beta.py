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

from google.api_core.exceptions import AlreadyExists
from testkit import check

import beta
import fetchers
import share_counts
import storage
import terminal

REAL_FOR_SYMBOL = beta.for_symbol    # conftest blanks these for every other test
REAL_FOR_SYMBOLS = beta.for_symbols
REAL_STANDIN = beta.industry_standin
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


# ── the stored table: what the website and the API read ─────────────────────

class _Snap:
    def __init__(self, id, data):
        self.id, self._data, self.exists = id, data, data is not None

    def to_dict(self):
        return None if self._data is None else dict(self._data)


class _Ref:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.split("/")[-1]

    def get(self, transaction=None):
        self.db.reads.append(self.path)
        return _Snap(self.id, self.db.docs.get(self.path))

    def set(self, data, merge=False):
        self.db.writes.append(("set", self.path, data))
        self.db.docs[self.path] = dict(data)

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
        if self.db.fail_commit:
            raise RuntimeError("commit failed")
        for op, ref, data in self.ops:
            self.db.writes.append((op, ref.path, data))
            self.db.docs[ref.path] = dict(data)


class _Query:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def where(self, field, op, value):
        self.field, self.value = field, value
        return self

    def stream(self):
        prefix = self.name + "/"
        return [_Snap(p[len(prefix):], d) for p, d in sorted(self.db.docs.items())
                if p.startswith(prefix) and "/" not in p[len(prefix):] and d.get(self.field) == self.value]

    def document(self, symbol):
        return _Ref(self.db, f"{self.name}/{symbol}")


class _DB:
    def __init__(self, docs, broken=False):
        self.docs, self.reads, self.writes, self.batched = docs, [], [], []
        self.broken, self.fail_commit = broken, False

    def document(self, path):
        return _Ref(self, path)

    def collection(self, name):
        return _Query(self, name)

    def batch(self):
        return _Batch(self)

    def get_all(self, refs):
        refs = list(refs)
        self.batched.append([r.path for r in refs])
        if self.broken:
            raise RuntimeError("firestore down")
        return [_Snap(r.id, self.docs.get(r.path)) for r in refs]


def _doc(symbol, **fields):
    return {"symbol": symbol, **dict.fromkeys(beta.FIELDS), **fields, "method": beta.METHOD,
            "week_ending": "2026-10-30", "computed_at": "2026-10-31T01:00:00+00:00"}


def test_a_set_is_read_in_one_batch_from_its_own_collection(monkeypatch):
    db = _DB({f"{beta.STORED}/AAPL": _doc("AAPL", beta=1.043, years=2.0, window_end="2026-10-30")})
    monkeypatch.setattr(storage, "get_db", lambda: db)
    got = REAL_FOR_SYMBOLS(["AAPL", "NEWCO", "NOPE"], {"AAPL": {"x": 1}, "NEWCO": {"x": 1}, "NOPE": None})
    check("one batched read, only for held companies",
          db.batched == [[f"{beta.STORED}/AAPL", f"{beta.STORED}/NEWCO"]], str(db.batched))
    check("stored fields", got["AAPL"] == {**dict.fromkeys(beta.FIELDS), "beta": 1.043, "years": 2.0,
                                           "window_end": "2026-10-30"}, str(got["AAPL"]))
    check("not reached yet", got["NEWCO"] == {**dict.fromkeys(beta.FIELDS), "reason": "not_yet_computed"})
    check("no record: all None", got["NOPE"] == dict.fromkeys(beta.FIELDS))
    check("nothing to read, no read", REAL_FOR_SYMBOLS(["X"], {}) == {"X": dict.fromkeys(beta.FIELDS)}
          and len(db.batched) == 1)


def test_a_failed_read_or_a_doc_for_another_symbol_is_unavailable(monkeypatch):
    monkeypatch.setattr(storage, "get_db", lambda: _DB({}, broken=True))
    got = REAL_FOR_SYMBOLS(["AAPL", "MSFT"], {"AAPL": {"x": 1}, "MSFT": {"x": 1}})
    check("every row unavailable", list(got.values()) == [beta.UNAVAILABLE] * 2, str(got))
    db = _DB({f"{beta.STORED}/AAPL": _doc("MSFT", beta=0.9), f"{beta.STORED}/MSFT": {"beta": 0.9}})
    monkeypatch.setattr(storage, "get_db", lambda: db)
    got = REAL_FOR_SYMBOLS(["AAPL", "MSFT"], {"AAPL": {"x": 1}, "MSFT": {"x": 1}})
    check("wrong symbol inside", got["AAPL"] == beta.UNAVAILABLE, str(got["AAPL"]))
    check("no symbol inside", got["MSFT"] == beta.UNAVAILABLE, str(got["MSFT"]))


def test_one_company_reads_its_record_then_its_beta_and_never_raises(monkeypatch):
    db = _DB({f"{beta.STORED}/KO": _doc("KO", beta=0.5)})
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr(beta, "for_symbols", REAL_FOR_SYMBOLS)
    reads = []
    monkeypatch.setattr(storage, "get_ticker_meta", lambda s: reads.append(s) or {"symbol": s})
    check("read", REAL_FOR_SYMBOL("KO")["beta"] == 0.5 and reads == ["KO"])
    check("record held: not read again", REAL_FOR_SYMBOL("KO", {"symbol": "KO"})["beta"] == 0.5 and reads == ["KO"])

    def broken(symbol):
        raise RuntimeError("firestore down")
    monkeypatch.setattr(storage, "get_ticker_meta", broken)
    check("unavailable, not an error", REAL_FOR_SYMBOL("KO") == beta.UNAVAILABLE)


# ── the weekly run ───────────────────────────────────────────────────────────

def _week(monkeypatch, companies, fits, stored=None):
    db = _DB({**{f"{storage.COLLECTION_ROOT}/{s}": d for s, d in companies.items()},
              **({beta.INDUSTRIES: stored} if stored else {})})
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr(storage, "get_ticker_meta", lambda s: db.docs.get(f"{storage.COLLECTION_ROOT}/{s}"))
    monkeypatch.setattr(beta, "calendar", lambda: {"ends": ["2026-10-23", "2026-10-30"], "spy": {}})

    def fit(symbol, cal):
        if isinstance(fits[symbol], Exception):
            raise fits[symbol]
        return {**dict.fromkeys(beta.FIELDS), **fits[symbol], "window_end": "2026-10-30"}
    monkeypatch.setattr(beta, "fit", fit)
    return db


COMPANIES = {
    "LMT": {"active": True, "industry": "Aerospace & Defense", "name": "Lockheed"},
    "RTX": {"active": True, "industry": "Aerospace & Defense"},
    "GD": {"active": True, "industry": "Aerospace & Defense"},
    "HONA": {"active": True, "industry": "Aerospace & Defense"},
    "FDRXX": {"active": True, "quote_type": "MONEYMARKET"},
    "OLD": {"active": False, "industry": "Aerospace & Defense"},
}
FITS = {"LMT": {"beta": 0.3}, "RTX": {"beta": 0.5}, "GD": {"beta": 0.4},
        "HONA": {"reason": "short_history"}, "FDRXX": {"reason": "short_history"}, "OLD": {"beta": 9.0}}


def test_the_weekly_run_stores_each_fit_its_history_and_the_industry_medians(monkeypatch):
    db = _week(monkeypatch, COMPANIES, FITS)
    records = {p: dict(d) for p, d in db.docs.items()}
    summary = beta.run()
    check("counts", (summary["companies"], summary["written"], summary["held"], summary["with_beta"],
                     summary["failed"]) == (5, 5, 0, 3, 0), str(summary))
    check("company records never written",
          not any(p.startswith(storage.COLLECTION_ROOT + "/") for _, p, _ in db.writes)
          and all(db.docs[p] == d for p, d in records.items()), str(db.writes))
    lmt = db.docs[f"{beta.STORED}/LMT"]
    check("current doc", (lmt["symbol"], lmt["beta"], lmt["method"], lmt["week_ending"])
          == ("LMT", 0.3, beta.METHOD, "2026-10-30"), str(lmt))
    check("history the same", db.docs[f"{beta.STORED}/LMT/{beta.HISTORY}/2026-10-30"] == lmt)
    check("history created, never set", all(op == "create" for op, p, _ in db.writes if f"/{beta.HISTORY}/" in p))
    check("inactive left alone", f"{beta.STORED}/OLD" not in db.docs)
    table = db.docs[beta.INDUSTRIES]
    check("median of the active", table["industries"] == {"Aerospace & Defense": {"median": 0.4, "peers": 3}},
          str(table["industries"]))
    check("week and complete", (table["window_end"], table["complete"]) == ("2026-10-30", True), str(table))


def test_a_week_already_stored_reads_only_spy_and_stops(monkeypatch):
    db = _week(monkeypatch, COMPANIES, FITS, stored={"window_end": "2026-10-30", "complete": True})
    check("skipped", beta.run() == {"skipped": True, "window_end": "2026-10-30"})
    check("no writes", db.writes == [], str(db.writes))
    db = _week(monkeypatch, COMPANIES, FITS, stored={"window_end": "2026-10-23", "complete": True})
    check("a newer week runs", beta.run()["written"] == 5)


def test_history_is_never_overwritten_and_a_rerun_must_reproduce(monkeypatch):
    db = _week(monkeypatch, COMPANIES, FITS)
    beta.run()
    first = dict(db.docs[f"{beta.STORED}/LMT/{beta.HISTORY}/2026-10-30"])
    again = beta.run(force=True)
    check("same week again: held, nothing written", (again["written"], again["held"], again["failed"]) == (0, 5, 0),
          str(again))
    check("history untouched", db.docs[f"{beta.STORED}/LMT/{beta.HISTORY}/2026-10-30"] == first)
    db = _week(monkeypatch, COMPANIES, {**FITS, "LMT": {"beta": 0.31}})
    db.docs[f"{beta.STORED}/LMT/{beta.HISTORY}/2026-10-30"] = first
    db.docs[f"{beta.STORED}/LMT"] = first
    got = beta.run(symbols=["LMT"])
    check("a different answer for a stored week fails", "LMT" in got["errors"]
          and "differs" in got["errors"]["LMT"], str(got))
    check("old fit kept, current and history", db.docs[f"{beta.STORED}/LMT"]["beta"] == 0.3
          and db.docs[f"{beta.STORED}/LMT/{beta.HISTORY}/2026-10-30"]["beta"] == 0.3)


def test_too_many_failures_fail_the_job_and_mark_the_table_incomplete(monkeypatch):
    db = _week(monkeypatch, COMPANIES, {**FITS, "RTX": RuntimeError("firestore down")})
    try:
        beta.run()
        raised = False
    except RuntimeError:
        raised = True
    check("job fails", raised)
    check("incomplete, so tomorrow runs again", db.docs[beta.INDUSTRIES]["complete"] is False)
    check("a failed company keeps no half write", f"{beta.STORED}/RTX" not in db.docs
          and f"{beta.STORED}/RTX/{beta.HISTORY}/2026-10-30" not in db.docs)


def test_a_trial_writes_only_the_companies_named(monkeypatch):
    db = _week(monkeypatch, COMPANIES, FITS)
    got = beta.run(symbols=["LMT", "HONA"])
    check("two", got["written"] == 2 and set(got["results"]) == {"LMT", "HONA"}, str(got))
    check("no industry table", beta.INDUSTRIES not in db.docs)


def test_the_stand_in_comes_from_the_stored_table_and_never_from_an_older_week(monkeypatch):
    db = _DB({beta.INDUSTRIES: {"window_end": "2026-10-23",
                                "industries": {"Aerospace & Defense": {"median": 0.92, "peers": 106}}}})
    monkeypatch.setattr(storage, "get_db", lambda: db)
    beta._INDUSTRY_CACHE.clear()
    got = REAL_STANDIN("Aerospace & Defense", "2026-10-23")
    check("median", got == {"industry_beta_median": 0.92, "industry_beta_peers": 106,
                            "industry_beta_industry": "Aerospace & Defense", "industry_beta_reason": None}, str(got))
    REAL_STANDIN("Aerospace & Defense", "2026-10-23")
    check("cached", len(db.reads) == 1, str(db.reads))
    beta._INDUSTRY_CACHE["checked"] -= timedelta(seconds=61)
    stale = REAL_STANDIN("Aerospace & Defense", "2026-10-30")
    check("a row from a newer week: re-read, still older, unavailable",
          (stale["industry_beta_median"], stale["industry_beta_reason"]) == (None, "unavailable")
          and len(db.reads) == 2, str((stale, db.reads)))
    REAL_STANDIN("Aerospace & Defense", "2026-10-30")
    check("re-read at most once a minute", len(db.reads) == 2, str(db.reads))
    db.docs[beta.INDUSTRIES] = {"window_end": "2026-10-30",
                                "industries": {"Aerospace & Defense": {"median": 0.95, "peers": 107}}}
    beta._INDUSTRY_CACHE["checked"] -= timedelta(seconds=61)
    check("caught up", REAL_STANDIN("Aerospace & Defense", "2026-10-30")["industry_beta_median"] == 0.95)
    check("no industry", REAL_STANDIN(None) == {"industry_beta_median": None, "industry_beta_peers": 0,
                                                "industry_beta_industry": None, "industry_beta_reason": None})
    check("no peers", REAL_STANDIN("Shell Companies", "2026-10-30")["industry_beta_peers"] == 0)
    monkeypatch.setattr(storage, "get_db", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    beta._INDUSTRY_CACHE.clear()
    down = REAL_STANDIN("Aerospace & Defense", "2026-10-30")
    check("unreadable: unavailable", (down["industry_beta_median"], down["industry_beta_peers"],
                                      down["industry_beta_reason"]) == (None, None, "unavailable"), str(down))


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
    monkeypatch.setattr(beta, "for_symbol", lambda s, meta=None: {**dict.fromkeys(beta.FIELDS), **fits[s]})
    check("two years", terminal._beta("AAPL") == ("1.04 2Y", "TEK2day's own beta: 2 years of weekly returns vs SPY"),
          str(terminal._beta("AAPL")))
    check("1.7 years", terminal._beta("SNDK")[0] == "1.43 1.7Y", str(terminal._beta("SNDK")))
    check("blank says why", terminal._beta("SPCX") == ("N/A", "Under 1 year of prices"))
