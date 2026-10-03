"""The daily SEC sync of the ticker universe (universe_sync.py).

Every rule is tested on the pure plan; the I/O is tested with a fake Firestore.
"""
from datetime import date

import pytest

import universe_sync as us

TODAY = date(2026, 10, 2)
FIELDS = ["cik", "name", "ticker", "exchange"]


def test_sec_spellings_become_tek2day_symbols_and_extras_are_left_out():
    assert us.tek2day_symbol("BRK-B", "NYSE") == "BRK.B"
    assert us.tek2day_symbol("BF-B", "NYSE") == "BF.B"
    assert us.tek2day_symbol("NVDA", "Nasdaq") == "NVDA"
    for extra in ("AAC-WT", "AAC-UN", "AGM-PI", "XYZ-U", "XYZ-W", "XYZ-R"):
        assert us.tek2day_symbol(extra, "NYSE") is None
    for extra in ("AACIW", "AACPU", "ALPXR"):     # Nasdaq fifth letter: warrant, unit, right
        assert us.tek2day_symbol(extra, "Nasdaq") is None
    assert us.tek2day_symbol("GOOGL", "Nasdaq") == "GOOGL"


def test_only_main_exchange_listings_are_read():
    rows = [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"], [1, "Some OTC Co", "OTCX", "OTC"],
            [2, "No Exchange", "NOEX", None], [1067983, "BERKSHIRE", "BRK-B", "NYSE"],
            [3, "SPAC Warrants", "AAC-WT", "NYSE"]]
    listed = us.sec_listings(rows, FIELDS)
    assert set(listed) == {"NVDA", "BRK.B"}
    assert listed["NVDA"] == {"cik": 1045810, "name": "NVIDIA CORP", "sec_exchange": "Nasdaq"}


def _sec(*symbols, cik=100):
    return {s: {"cik": cik + i, "name": f"{s} Inc", "sec_exchange": "Nasdaq"} for i, s in enumerate(symbols)}


def _plan(sec, existing, last_price, identity=(), is_spac=None):
    return us.plan(sec, existing, last_price, set(identity), TODAY, is_spac=is_spac)


def test_new_listings_are_added_spacs_skipped_and_known_names_left_alone():
    sec = _sec("NEWCO", "OLDCO", "SPACQ")
    existing = {"OLDCO": {"active": True, "cik": 101, "added_at": "2026-05-26"}}
    result = _plan(sec, existing, {"OLDCO": "2026-10-01"}, is_spac=lambda s, n: s == "SPACQ")
    assert result["adds"] == ["NEWCO"] and result["skipped_spacs"] == ["SPACQ"]
    assert result["deactivate"] == [] and result["cik_changed"] == []


def test_deactivation_follows_tekdays_own_prices_never_absence_from_the_sec_list():
    existing = {
        "STALE": {"active": True, "added_at": "2026-05-26"},     # last price in July
        "OTCLIVE": {"active": True, "added_at": "2026-05-26"},   # not on the SEC list, still trading
        "NEVER": {"active": True, "added_at": "2026-05-26"},     # never priced, long enough to have
        "FRESH": {"active": True, "added_at": "2026-09-30"},     # never priced, just added: grace
        "ROUTED": {"active": True, "added_at": "2026-05-26"},    # reviewed identity route: untouched
        "OFF": {"active": False},
    }
    last = {"STALE": "2026-07-15", "OTCLIVE": "2026-10-01", "NEVER": None, "FRESH": None, "ROUTED": "2026-01-02"}
    result = _plan(_sec("X"), existing, last, identity={"ROUTED"})
    assert result["deactivate"] == [{"symbol": "NEVER", "last_price_date": None},
                                    {"symbol": "STALE", "last_price_date": "2026-07-15"}]


def test_exactly_thirty_days_is_still_active():
    existing = {"EDGE": {"active": True}, "OVER": {"active": True}}
    result = _plan(_sec("X"), existing, {"EDGE": "2026-09-02", "OVER": "2026-09-01"})
    assert [r["symbol"] for r in result["deactivate"]] == ["OVER"]


def test_ticker_reuse_and_relistings_are_flagged_never_acted_on():
    sec = _sec("REUSE", "BACK", cik=500)
    existing = {"REUSE": {"active": True, "cik": 42}, "BACK": {"active": False, "cik": 501}}
    result = _plan(sec, existing, {"REUSE": "2026-10-01"})
    assert result["cik_changed"] == [{"symbol": "REUSE", "stored_cik": 42, "sec_cik": 500, "sec_name": "REUSE Inc"}]
    assert result["relisted"] == ["BACK"] and result["adds"] == [] and result["deactivate"] == []


def test_renamed_records_are_known_never_readded_or_deactivated():
    existing = {"OLDNAME": {"active": True, "renamed_to": "NEWNAME"}}
    result = _plan(_sec("OLDNAME"), existing, {"OLDNAME": None})
    assert result["adds"] == [] and result["deactivate"] == [] and result["cik_changed"] == []


def test_a_run_that_would_change_too_much_is_refused():
    big = {f"N{i:04d}": {"cik": i, "name": "x", "sec_exchange": "NYSE"} for i in range(us.MIN_SEC_ROWS + 700)}
    result = _plan(big, {}, {})
    assert any("additions exceeds" in r for r in result["refused"])
    small = _plan(_sec("A"), {}, {})
    assert any("SEC list has only" in r for r in small["refused"])
    stale = {f"S{i}": {"active": True} for i in range(us.MAX_DEACTIVATIONS + 1)}
    assert any("deactivations exceeds" in r for r in _plan(big, stale, {})["refused"])


# ── run() with a fake Firestore ──────────────────────────────────────────────

class _Doc:
    def __init__(self, store, path):
        self.store, self.path = store, path

    def set(self, data, merge=False):
        current = self.store.setdefault(self.path, {}) if merge else {}
        for key, value in data.items():
            if isinstance(value, _ArrayRemove):
                current[key] = [v for v in (current.get(key) or []) if v not in value.values]
            elif isinstance(value, _ArrayUnion):
                current[key] = sorted(set(current.get(key) or []) | set(value.values))
            else:
                current[key] = value
        self.store[self.path] = current

    def get(self):
        data = self.store.get(self.path)
        return type("Snap", (), {"exists": data is not None, "to_dict": lambda _s: dict(data or {})})()


class _Col:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def document(self, key):
        return _Doc(self.store, f"{self.name}/{key}")


class _DB:
    def __init__(self):
        self.store = {}

    def collection(self, name):
        return _Col(self.store, name)


class _ArrayUnion:
    def __init__(self, values):
        self.values = values


class _ArrayRemove(_ArrayUnion):
    pass


@pytest.fixture
def fake(monkeypatch):
    import storage
    from google.cloud import firestore
    db = _DB()
    written, deactivated = {}, []
    monkeypatch.setattr(firestore, "ArrayUnion", _ArrayUnion)
    monkeypatch.setattr(firestore, "ArrayRemove", _ArrayRemove)
    monkeypatch.setattr(us, "fetch_sec", lambda: _sec(*[f"T{i:04d}" for i in range(us.MIN_SEC_ROWS)], "NEWCO"))
    existing = {f"T{i:04d}": {"active": True, "added_at": "2026-05-26"} for i in range(us.MIN_SEC_ROWS)}
    existing["DEAD"] = {"active": True, "added_at": "2026-05-26"}
    monkeypatch.setattr(us, "read_existing", lambda db_: existing)
    monkeypatch.setattr(us, "read_last_prices", lambda db_, symbols: {s: ("2026-06-01" if s == "DEAD" else "2026-10-01")
                                                                     for s in symbols})
    monkeypatch.setattr(storage, "event_for", lambda s: None)
    monkeypatch.setattr(storage, "write_ticker_meta", lambda s, m: written.__setitem__(s, m))
    monkeypatch.setattr(storage, "deactivate_ticker", lambda s, **kw: deactivated.append((s, kw)))
    # Never Yahoo in a test: the fill is recorded, not run.
    filled = []
    monkeypatch.setattr(us, "fill", lambda db_, storage_, order, existing_, delay=None: filled.append(order) or
                        {"filled": [], "no_sector": [], "unknown": [], "failed": []})
    monkeypatch.setattr(us, "read_priority", lambda db_: ["NSRGY"])
    return db, written, deactivated


def test_observe_reports_and_changes_nothing(fake, monkeypatch):
    db, written, deactivated = fake
    monkeypatch.setattr(us, "MODE", "observe")
    result = us.run(db=db, today=TODAY)
    assert result["adds"] == ["NEWCO"] and [r["symbol"] for r in result["deactivate"]] == ["DEAD"]
    assert written == {} and deactivated == []
    report = db.store["universe_sync_runs/2026-10-02"]
    assert report["mode"] == "observe" and report["applied"] is False
    assert report["counts"]["adds"] == 1 and report["adds"] == ["NEWCO"]
    assert "universe_sync/state" not in db.store


def test_apply_adds_with_sec_identity_queues_history_and_deactivates_with_reason(fake, monkeypatch):
    db, written, deactivated = fake
    monkeypatch.setattr(us, "MODE", "apply")
    us.run(db=db, today=TODAY)
    meta = written["NEWCO"]
    assert meta["name"] == "NEWCO Inc" and meta["cik"] == us.MIN_SEC_ROWS + 100 and meta["active"] is True
    assert meta["added_by"] == "universe_sync" and meta["sector"] == "" and "exchange" not in meta
    assert db.store["universe_sync/state"]["backfill_pending"] == ["NEWCO"]
    assert deactivated == [("DEAD", {"reason": "no_price_30d", "last_price_date": "2026-06-01"})]
    assert db.store["universe_sync_runs/2026-10-02"]["applied"] is True
    us.backfill_done(db, "NEWCO")
    assert db.store["universe_sync/state"]["backfill_pending"] == []
    assert us.backfill_pending(db) == set()


def test_a_refused_run_changes_nothing_even_in_apply(fake, monkeypatch):
    db, written, deactivated = fake
    monkeypatch.setattr(us, "MODE", "apply")
    monkeypatch.setattr(us, "MAX_ADDS", 0)
    result = us.run(db=db, today=TODAY)
    assert result["refused"] and written == {} and deactivated == []
    assert db.store["universe_sync_runs/2026-10-02"]["applied"] is False


def test_a_failing_sync_never_raises(monkeypatch):
    monkeypatch.setattr(us, "MODE", "apply")
    monkeypatch.setattr(us, "fetch_sec", lambda: (_ for _ in ()).throw(RuntimeError("SEC down")))
    assert us.run(db=_DB(), today=TODAY) is None


def test_off_does_nothing(monkeypatch):
    monkeypatch.setattr(us, "MODE", "off")
    db = _DB()
    assert us.run(db=db, today=TODAY) is None and db.store == {}


# ── inside the daily price job ───────────────────────────────────────────────

def _drive_price_job(monkeypatch, task_index=0, task_count=1, sync=None, pending=()):
    import pull_daily_prices as pdp
    periods, calls, cleared = {}, [], []
    monkeypatch.setattr(pdp, "SYMBOLS", "")
    monkeypatch.setattr(pdp, "LIMIT", 0)
    monkeypatch.setattr(pdp.storage, "list_active_tickers", lambda: ["AAA", "BBB", "NEWCO"])
    monkeypatch.setattr(pdp.storage, "event_for", lambda s: None)
    monkeypatch.setattr(pdp.fetchers, "fetch_prices",
                        lambda s, period: periods.__setitem__(s, period) or [{"date": "2026-10-02", "close": 1.0}])
    monkeypatch.setattr(pdp.storage, "write_prices_batch", lambda s, rows: None)
    monkeypatch.setattr(pdp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(pdp, "DELAY", 0)
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", str(task_index))
    monkeypatch.setenv("CLOUD_RUN_TASK_COUNT", str(task_count))
    monkeypatch.setattr(us, "backfill_pending", lambda db=None: set(pending))
    monkeypatch.setattr(us, "backfill_done", lambda db, s: cleared.append(s))
    monkeypatch.setattr(us, "run", sync or (lambda: calls.append("sync")))
    pdp.main()
    return periods, calls, cleared


def test_new_names_get_their_history_in_the_one_request_tonight(monkeypatch):
    periods, _, cleared = _drive_price_job(monkeypatch, pending={"NEWCO"})
    assert periods == {"AAA": "5d", "BBB": "5d", "NEWCO": us.BACKFILL_PERIOD}
    assert cleared == ["NEWCO"]


def test_the_sync_runs_once_after_task_zero_and_never_in_a_smoke_test(monkeypatch):
    _, calls, _ = _drive_price_job(monkeypatch, task_index=0, task_count=6)
    assert calls == ["sync"]
    _, calls, _ = _drive_price_job(monkeypatch, task_index=3, task_count=6)
    assert calls == []
    import pull_daily_prices as pdp
    monkeypatch.setattr(pdp, "SYMBOLS", "AAA")
    calls = []
    monkeypatch.setattr(us, "run", lambda: calls.append("sync"))
    pdp.main()
    assert calls == []


def test_a_failing_sync_does_not_change_the_price_jobs_result(monkeypatch):
    # run() never raises by contract; even a raise is the sync's own bug, so it is
    # pinned here that the real run() swallows its failures.
    monkeypatch.setattr(us, "MODE", "apply")
    monkeypatch.setattr(us, "fetch_sec", lambda: (_ for _ in ()).throw(RuntimeError("SEC down")))
    real_run = us.run
    periods, _, _ = _drive_price_job(monkeypatch, sync=lambda: real_run(db=_DB()))
    assert set(periods) == {"AAA", "BBB", "NEWCO"}   # and main() returned normally



# ── the sector fill ──────────────────────────────────────────────────────────

def test_fill_order_puts_customer_names_first_then_tonights_adds_then_the_rest():
    existing = {
        "OLDBLANK": {"active": True, "sector": "", "added_at": "2026-05-01"},
        "NEWBLANK": {"active": True, "sector": None, "added_at": "2026-09-01"},
        "HASSECTOR": {"active": True, "sector": "Technology"},
        "DEAD": {"active": False, "sector": ""},
        "ROUTED": {"active": True, "sector": ""},
        "OLDNAME": {"active": True, "sector": "", "renamed_to": "NEWNAME"},
    }
    order = us.fill_order(existing, {"ROUTED"}, ["NSRGY", "HASSECTOR", "OLDBLANK"], ["TONIGHT"], limit=10)
    assert order == ["NSRGY", "OLDBLANK", "TONIGHT", "NEWBLANK"]
    assert us.fill_order(existing, set(), [], [], limit=1) == ["NEWBLANK"]


def test_fill_saves_yahoo_details_covers_new_customer_names_and_never_raises(monkeypatch):
    import fetchers
    import storage
    answers = {"HONA": {"name": "Honeywell Aerospace", "sector": "Industrials", "industry": "Aerospace & Defense"},
               "XLK": {"name": "Technology Select Sector SPDR", "sector": "ETF", "industry": "Technology"},
               "ZZZZ": None}
    monkeypatch.setattr(fetchers, "fetch_ticker_info",
                        lambda s: (_ for _ in ()).throw(RuntimeError("Yahoo down")) if s == "BOOM" else
                        (dict(answers[s]) if answers.get(s) else None))
    written = {}
    monkeypatch.setattr(storage, "write_ticker_meta", lambda s, m: written.__setitem__(s, m))
    from google.cloud import firestore
    monkeypatch.setattr(firestore, "ArrayUnion", _ArrayUnion)
    monkeypatch.setattr(firestore, "ArrayRemove", _ArrayRemove)
    db = _DB()
    us.add_priority(db, ["hona", "XLK", "ZZZZ", "BOOM"])
    result = us.fill(db, storage, ["HONA", "XLK", "ZZZZ", "BOOM"], {"HONA": {"active": True}}, delay=0)
    assert result == {"filled": ["HONA", "XLK"], "no_sector": [], "unknown": ["ZZZZ"], "failed": ["BOOM"]}
    assert written["HONA"]["sector"] == "Industrials" and "added_by" not in written["HONA"]
    assert written["XLK"]["added_by"] == "kilby_priority"          # a customer's name TEK2day now covers
    assert db.store["universe_sync/state"]["priority"] == ["BOOM"]  # retried next night


def test_funds_get_a_label_instead_of_a_blank():
    import fetchers
    assert fetchers.fund_labels({"quoteType": "ETF", "category": "Technology"}) == ("ETF", "Technology")
    assert fetchers.fund_labels({"quoteType": "MONEYMARKET"}) == ("Money market fund", "")
    assert fetchers.fund_labels({"quoteType": "MUTUALFUND", "category": "Money Market - Taxable"}) == (
        "Money market fund", "Money Market - Taxable")
    assert fetchers.fund_labels({"quoteType": "MUTUALFUND", "category": "Large Blend"}) == ("Mutual fund", "Large Blend")
    assert fetchers.fund_labels({"quoteType": "EQUITY", "sector": "Healthcare", "industry": "Drug Manufacturers"}) == (
        "Healthcare", "Drug Manufacturers")
    assert fetchers.fund_labels({"quoteType": "EQUITY"}) == ("", "")


def test_a_run_fills_after_the_sync_and_observe_only_lists_what_it_would_fill(fake, monkeypatch):
    db, written, deactivated = fake
    monkeypatch.setattr(us, "MODE", "observe")
    result = us.run(db=db, today=TODAY)
    assert result["fill"]["would_fill"][0] == "NSRGY" and len(result["fill"]["would_fill"]) == us.FILL_LIMIT
    monkeypatch.setattr(us, "MODE", "apply")
    result = us.run(db=db, today=TODAY)
    assert db.store["universe_sync_runs/2026-10-02"]["fill_counts"] == {"filled": 0, "no_sector": 0, "unknown": 0, "failed": 0}


def test_the_job_can_run_only_the_sync_and_fill(monkeypatch):
    import pull_daily_prices as pdp
    calls = []
    monkeypatch.setenv("UNIVERSE_SYNC_ONLY", "1")
    monkeypatch.setattr(us, "run", lambda: calls.append("sync") or {})
    monkeypatch.setattr(pdp.fetchers, "fetch_prices", lambda *a, **k: calls.append("PRICE REQUEST"))
    with pytest.raises(SystemExit) as done:
        pdp.main()
    assert done.value.code == 0 and calls == ["sync"]


def test_kilby_queues_its_customers_names(monkeypatch):
    import partner_api
    import storage
    db = _DB()
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr(partner_api, "require_kilby", lambda request: "kilby")
    from google.cloud import firestore
    monkeypatch.setattr(firestore, "ArrayUnion", _ArrayUnion)
    body = partner_api._Priority(symbols=["hona", "brk-b", ""])
    assert partner_api.symbol_priority(object(), body) == {"queued": 2}
    assert db.store["universe_sync/state"]["priority"] == ["BRK.B", "HONA"]


def test_a_sync_only_run_happens_once_even_with_six_tasks(monkeypatch):
    import pull_daily_prices as pdp
    calls = []
    monkeypatch.setenv("UNIVERSE_SYNC_ONLY", "1")
    monkeypatch.setenv("CLOUD_RUN_TASK_INDEX", "3")
    monkeypatch.setattr(us, "run", lambda: calls.append("sync") or {})
    with pytest.raises(SystemExit) as done:
        pdp.main()
    assert done.value.code == 0 and calls == []


# ── foreign names: SEC industry code, home listing, manual override ─────────

def test_the_fill_tries_yahoo_then_the_sec_code_then_the_home_listing(monkeypatch):
    import fetchers
    import sec_sectors
    import storage
    from google.cloud import firestore
    monkeypatch.setattr(firestore, "ArrayUnion", _ArrayUnion)
    monkeypatch.setattr(firestore, "ArrayRemove", _ArrayRemove)
    yahoo = {"YAHOO": {"name": "Yahoo Classified", "sector": "Technology", "industry": "Software", "sector_source": "yahoo"},
             "SECCO": {"name": "Foreign Filer PLC"},      # Yahoo knows it, no sector
             "ADRCO": {"name": "Nestle SA-Spons ADR"},
             "NOWHERE": {"name": "Unclassifiable Co"}}
    monkeypatch.setattr(fetchers, "fetch_ticker_info", lambda s: dict(yahoo[s]) if s in yahoo else None)
    monkeypatch.setattr(sec_sectors, "sector_from_sec", lambda cik: ("Industrials", "Aircraft Engines") if cik == 77 else None)
    monkeypatch.setattr(fetchers, "search_sector", lambda name, exclude="": ("Consumer Defensive", "Packaged Foods", "NESN.SW")
                        if fetchers.company_key(name) == "nestle" else None)
    written = {}
    monkeypatch.setattr(storage, "write_ticker_meta", lambda s, m: written.__setitem__(s, m))
    existing = {"SECCO": {"active": True, "cik": 77}, "ADRCO": {"active": True}, "NOWHERE": {"active": True},
                "YAHOO": {"active": True}}
    result = us.fill(_DB(), storage, ["YAHOO", "SECCO", "ADRCO", "NOWHERE", "GHOST"], existing, delay=0)
    assert result == {"filled": ["YAHOO", "SECCO", "ADRCO"], "no_sector": ["NOWHERE"], "unknown": ["GHOST"], "failed": []}
    assert (written["SECCO"]["sector"], written["SECCO"]["sector_source"]) == ("Industrials", "sec_sic")
    assert written["ADRCO"]["sector_source"] == "home_listing:NESN.SW"
    assert "sector" not in written["NOWHERE"]       # a blank is never written over a stored sector


def test_an_override_wins_and_no_job_ever_fills_over_it(monkeypatch):
    db = _DB()
    from config import COLLECTION_ROOT

    class _Col(type(db.collection("x"))):
        pass
    # set_override writes the ticker and a history record
    calls = []
    monkeypatch.setattr(db, "collection", lambda name: type("C", (), {
        "document": lambda self, key=None: type("D", (), {"set": lambda s, data, merge=False: calls.append((name, key, data))})()})())
    entry = us.set_override(db, "nsrgy", "Consumer Defensive", "Packaged Foods", "owner", "home listing NESN.SW")
    assert entry["sector_override"] == "Consumer Defensive"
    assert [c[0] for c in calls] == [COLLECTION_ROOT, "sector_overrides"] and calls[0][1] == "NSRGY"
    existing = {"NSRGY": {"active": True, "sector": "", "sector_override": "Consumer Defensive"}}
    assert us.fill_order(existing, set(), ["NSRGY"], [], limit=10) == []


def test_the_master_serves_the_override_first(monkeypatch):
    import partner_api
    import storage
    docs = {"NSRGY": {"name": "Nestle", "sector": "", "sector_override": "Consumer Defensive",
                      "industry_override": "Packaged Foods", "active": True},
            "SECCO": {"name": "Foreign Filer", "sector": "Industrials", "sector_source": "sec_sic", "active": True}}

    class _Doc:
        def __init__(self, k, v):
            self.id, self._v = k, v

        def to_dict(self):
            return dict(self._v)
    db = type("DB", (), {"collection": lambda self, n: type("Q", (), {
        "select": lambda s, f: s, "stream": lambda s: [_Doc(k, v) for k, v in docs.items()]})()})()
    monkeypatch.setattr(storage, "get_db", lambda: db)
    monkeypatch.setattr(partner_api, "_master_cache", {})
    rows, _ = partner_api._master_rows()
    by = {r["symbol"]: r for r in rows}
    assert (by["NSRGY"]["sector"], by["NSRGY"]["industry"], by["NSRGY"]["sector_source"]) == (
        "Consumer Defensive", "Packaged Foods", "override")
    assert by["SECCO"]["sector_source"] == "sec_sic"


def test_company_names_match_only_exactly():
    import fetchers
    assert fetchers.company_key("Nestlé S.A.") == fetchers.company_key("NESTLE SA-SPONS ADR") == "nestle"
    assert fetchers.company_key("Nestlé India Limited") == "nestle india"
