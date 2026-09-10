"""Offline identity, reconciliation and recovery tests. All data is synthetic."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import identity_storage
import security_identity as si
import storage
import ticker_migration as tm
from scripts.inspect_ticker_identity import capture


class Snapshot:
    def __init__(self, ref):
        self.reference, self.id = ref, ref.id
        self.exists = ref.path in ref.db.data
        self.value = deepcopy(ref.db.data.get(ref.path))
        self.update_time = ref.db.times.get(ref.path)

    def to_dict(self):
        return deepcopy(self.value)


class Ref:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.split('/')[-1]

    def get(self, **kwargs):
        tx = kwargs.get('transaction')
        assert tx is None or not tx.pending, 'Firestore forbids reads after transaction writes'
        return Snapshot(self)

    @property
    def parent(self):
        return Collection(self.db, self.path.rsplit('/', 1)[0])

    def set(self, data, merge=False):
        self.db.writes += 1
        if self.db.fail_at == self.db.writes:
            raise RuntimeError("interrupted")
        if merge:
            data = {**self.db.data.get(self.path, {}), **data}
        self.db.data[self.path] = deepcopy(data)
        self.db.times[self.path] = datetime(2026, 9, 9, tzinfo=timezone.utc)

    def create(self, data):
        assert self.path not in self.db.data
        self.set(data)

    def collection(self, name):
        return Collection(self.db, self.path + '/' + name)

    def collections(self, **kwargs):
        prefix = self.path + '/'
        names = {p[len(prefix):].split('/')[0] for p in self.db.data
                 if p.startswith(prefix) and '/' in p[len(prefix):]}
        return [self.collection(n) for n in sorted(names)]


class Collection:
    def __init__(self, db, path):
        self.db, self.path, self.id = db, path, path.split('/')[-1]
        self.filters, self.order, self.bound = [], None, None

    @property
    def parent(self):
        return Ref(self.db, self.path.rsplit('/', 1)[0])

    def where(self, key, op, value):
        assert op == '=='
        self.filters.append((key, value))
        return self

    def order_by(self, field, direction=None):
        self.order = (field, direction)
        return self

    def limit(self, value):
        self.bound = value
        return self

    def document(self, name):
        return Ref(self.db, self.path + '/' + name)

    def list_documents(self, **kwargs):
        prefix = self.path + '/'
        names = {p[len(prefix):].split('/')[0] for p in self.db.data if p.startswith(prefix)}
        return [self.document(n) for n in sorted(names)]

    def stream(self):
        docs = [ref.get() for ref in self.list_documents() if ref.get().exists]
        docs = [d for d in docs if all(d.to_dict().get(k) == v for k, v in self.filters)]
        if self.order:
            key, direction = self.order
            docs = [d for d in docs if key in d.to_dict()]
            docs.sort(key=lambda d: d.to_dict()[key], reverse=direction == 'DESCENDING')
        return docs[:self.bound] if self.bound is not None else docs


class Transaction:
    def __init__(self, db):
        self.db, self.pending = db, []

    def set(self, ref, data, merge=False):
        self.pending.append((ref, deepcopy(data), merge))

    def delete(self, ref):
        self.pending.append((ref, None, False))

    def commit(self):
        for ref, data, merge in self.pending:
            if data is None:
                self.db.data.pop(ref.path, None)
                self.db.times.pop(ref.path, None)
            else:
                ref.set(data, merge=merge)


class DB:
    project, _database = "yfinance-cli", "(default)"

    def __init__(self, data):
        self.data, self.times = deepcopy(data), {}
        self.writes, self.fail_at = 0, None

    def document(self, path):
        return Ref(self, path)

    def collection(self, name):
        return Collection(self, name)

    def transaction(self):
        return Transaction(self)

    def collection_group(self, name):
        col = Collection(self, name)
        col.list_documents = lambda: [Ref(self, p) for p in self.data if p.split('/')[-2] == name]
        return col


@pytest.fixture
def db(monkeypatch):
    from google.cloud import firestore
    def transactional(fn):
        def call(tx):
            result = fn(tx)
            tx.commit()
            return result
        return call
    monkeypatch.setattr(firestore, "transactional", transactional)
    db = DB({
        "tickers/BK": {"symbol": "BK", "cik": 1390777, "name": "Synthetic issuer", "active": True},
        "tickers/BK/financials/2026-Q1": {"symbol": "BK", "period": "2026-Q1", "period_end": "2026-03-31",
                                             "income": {"Total Revenue": 100, "Diluted EPS": 0}, "fetched_at": "2026-05-01T10:00:00+00:00"},
        "tickers/BK/prices/2026-05-20": {"symbol": "BK", "date": "2026-05-20", "close": 10},
        "tickers/BK/estimates/2026-05-20": {"symbol": "BK", "date": "2026-05-20", "eps_avg": {"0y": 1}},
        "tickers/BK/financials/missing/revisions/r1": {"value": 19},
    })
    monkeypatch.setattr(storage, "get_db", lambda: db)
    return db


def plan_for(db):
    snapshot = {"project": db.project, "database": db._database, **capture(db)}
    bindings = {f"tickers/{symbol}": {"security_id": si.BNY_EVENT["security_id"],
                  "record_sha256": si.digest(snapshot["records"][f"tickers/{symbol}"]),
                  "review_basis": "Synthetic offline fixture; not event evidence"} for symbol in ("BK", "BNY")}
    return tm.build_plan(snapshot, si.BNY_EVENT, bindings=bindings)


def executor(db, plan):
    return tm.FirestoreMigration(db, plan, lambda: capture(db))


def test_cik_does_not_join_a_different_security():
    for key, value in [("cik", "0000000001"), ("share_class", "preferred"), ("cusip", "999999999"),
                       ("currency", "GBP"), ("exchange_mic", "XNAS"), ("adr_ratio", "2"),
                       ("instrument_type", "adr"), ("share_basis", "converted")]:
        event = deepcopy(si.BNY_EVENT)
        event["to"][key] = value
        with pytest.raises(si.IdentityError):
            si.validate_event(event)


@pytest.mark.parametrize("kind", ["merger", "successor_registrant", "delisting", "spinoff", "ticker_reuse", "share_class_conversion", "adr_change"])
def test_other_events_refused(kind):
    event = deepcopy(si.BNY_EVENT)
    event["event_type"] = kind
    with pytest.raises(si.IdentityError):
        si.validate_event(event)


def test_self_alias_cycles_chains_and_ambiguity():
    event = deepcopy(si.BNY_EVENT)
    event["to"]["symbol"] = "BK"
    with pytest.raises(si.IdentityError):
        si.validate_event(event)
    event["from"]["symbol"] = "BNY"
    with pytest.raises(si.IdentityError):
        si.event_for("BK", [si.BNY_EVENT, event])
    event["to"]["symbol"] = "NEW"
    with pytest.raises(si.IdentityError):
        si.event_for("BK", [si.BNY_EVENT, event])


def test_zero_null_conflicts_and_periods(db):
    db.data["tickers/BNY"] = {"symbol": "BNY", "name": "Populated destination", "sector": None}
    db.data["tickers/BNY/financials/2026-Q1"] = {
        "symbol": "BNY", "period": "2026-Q1", "period_end": "2026-03-31",
        "income": {"Total Revenue": 110, "Diluted EPS": None, "Net Income": 0}}
    plan = plan_for(db)
    selected = next(w["data"] for w in plan["writes"] if w["path"].endswith('/financials/2026-Q1'))
    assert selected["income"] == {"Total Revenue": 110, "Diluted EPS": 0, "Net Income": 0}
    assert selected["period_end"] == "2026-03-31"
    assert any(c["field"].endswith('/Total Revenue') for c in plan["conflicts"])
    assert any(w["path"].endswith('/financials/missing/revisions/r1') for w in plan["writes"])
    assert len([w for w in plan["writes"] if '/observations/' in w["path"]]) == len(plan["source_records"])
    db.data["tickers/BNY/financials/2026-Q1"]["period_end"] = "2026-06-30"
    with pytest.raises(si.IdentityError, match="period"):
        plan_for(db)


def test_missing_and_nonfinite_are_not_zero():
    conflicts = []
    merged = tm.reconcile({"a": 2, "b": 0, "c": 1}, {"a": float('nan'), "b": None, "c": 0}, conflicts=conflicts)
    assert merged == {"a": 2, "b": 0, "c": 0}
    assert len(conflicts) == 1
    assert si.digest({"v": float('nan')}) == si.digest({"v": float('nan')})
    assert si.digest({"v": None}) != si.digest({"v": 0})


def test_unreviewed_bindings_or_mutated_plan_refused(db):
    plan = plan_for(db)
    plan["writes"][0]["data"]["symbol"] = "OTHER"
    with pytest.raises(si.IdentityError, match="altered"):
        tm.validate_plan(plan)
    db.data["tickers/BNY"] = {"symbol": "BNY", "cik": 1}
    with pytest.raises(si.IdentityError, match="registrant"):
        plan_for(db)


def test_interruption_resume_publish_and_exact_reads(db):
    plan = plan_for(db)
    migrate = executor(db, plan)
    db.fail_at = 4
    with pytest.raises(RuntimeError, match="interrupted"):
        migrate.stage()
    assert storage.get_ticker_meta("BNY") is None
    assert storage.get_ticker_meta("BK")["symbol"] == "BK"
    with pytest.raises(si.IdentityError, match="paused"):
        storage.write_price("BNY", "2026-09-09", {"close": 12})
    db.fail_at = None
    migrate.stage()
    migrate.stage()  # exact rerun is safe
    migrate.publish()
    migrate.publish()
    migrate.stage()  # completed reruns do not re-copy old data
    assert storage.public_symbol("BK") == "BNY"
    assert storage.get_ticker_meta("BNY")["security_id"] == si.BNY_EVENT["security_id"]
    with pytest.raises(si.RetiredSymbol):
        storage.get_ticker_meta("BK")
    with pytest.raises(si.RetiredSymbol):
        storage.write_ticker_meta("BK", {"symbol": "BK"})


def test_missing_target_and_tombstones_refused(db):
    plan = plan_for(db)
    db.data[si.route_path(si.BNY_EVENT)] = plan["publish"]["route"]
    with pytest.raises(si.IdentityError, match="target"):
        storage.get_ticker_meta("BNY")
    db.data["tickers/FAKE"] = {"symbol": "FAKE", "renamed_to": "MISSING"}
    with pytest.raises(si.IdentityError, match="alias"):
        storage.get_ticker_meta("FAKE")


def test_published_identity_with_lost_route_is_not_a_partial_legacy_record(db):
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    del db.data[si.route_path(si.BNY_EVENT)]
    with pytest.raises(si.IdentityError, match="route missing"):
        storage.get_ticker_meta('BNY')
    with pytest.raises(si.IdentityError, match="route missing"):
        storage.get_ticker_meta('BK')


def test_source_change_aborts_publish(db):
    migrate = executor(db, plan_for(db))
    migrate.stage()
    db.data["tickers/BK/prices/2026-09-09"] = {"close": 1}
    with pytest.raises(si.IdentityError, match="source tree changed"):
        migrate.publish()
    assert storage.get_ticker_meta("BNY") is None


def test_unexpected_nested_stage_document_aborts_publish(db):
    plan = plan_for(db)
    migrate = executor(db, plan)
    migrate.stage()
    db.data[plan['publish']['route']['version_path'] + '/prices/2026-01-01/nested/extra'] = {"oops": 1}
    with pytest.raises(si.IdentityError, match="nested"):
        migrate.publish()


def test_rollback_preserves_sources_and_refuses_new_maintenance_data(db):
    original = deepcopy(db.data)
    plan = plan_for(db)
    migrate = executor(db, plan)
    migrate.stage()
    migrate.publish()
    migrate.rollback()
    migrate.rollback()
    for path, data in original.items():
        assert db.data[path] == data
    assert "tickers/BNY" not in db.data
    assert storage.get_ticker_meta("BK")["symbol"] == "BK"
    assert any(p.startswith('security_data/') for p in db.data)  # retained audit/version


def test_post_publish_writes_route_and_retain_observations(db):
    plan = plan_for(db)
    migrate = executor(db, plan)
    migrate.stage()
    migrate.publish()
    storage.write_price("BNY", "2026-05-20", {"symbol": "BNY", "date": "2026-05-20", "close": 11})
    root = plan["publish"]["route"]["version_path"]
    assert db.data[root + '/prices/2026-05-20']["close"] == 11
    assert db.data['tickers/BK/prices/2026-05-20']["close"] == 10
    assert any(p.startswith(root + '/prices/2026-05-20/identity_observations/') for p in db.data)
    with pytest.raises(si.IdentityError, match="changed"):
        migrate.rollback()
    assert db.data[si.route_path(si.BNY_EVENT)]["status"] == "paused"


def test_cache_key_changes_after_publication(db):
    before = storage.identity_cache_key("BNY")
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    assert storage.identity_cache_key("BNY") != before


def test_all_canonical_datasets_and_active_universe_share_route(db):
    plan = plan_for(db)
    migrate = executor(db, plan)
    migrate.stage()
    before = storage.query_estimates_by_date("2026-05-20")
    assert len(before) == 1 and before[0]['_symbol'] == "BK"
    migrate.publish()
    assert storage.list_active_tickers() == ['BNY']
    # A stale external root must not cause the guarded job to refresh BK twice.
    db.data['tickers/BK']['active'] = True
    assert storage.list_active_tickers() == ['BNY']
    assert storage.get_all_financials('BNY')[0]['period_end'] == '2026-03-31'
    assert storage.get_prices_history('BNY')[0]['close'] == 10
    assert storage.get_estimate_history('BNY')[0]['date'] == '2026-05-20'
    rows = storage.query_estimates_by_date('2026-05-20')
    assert len(rows) == 1 and rows[0]['_symbol'] == 'BNY'


def test_real_metadata_cache_does_not_reuse_prepublication_entry(db):
    import terminal
    db.data['tickers/BNY'] = {'symbol': 'BNY', 'name': 'Synthetic prepublication'}
    before = terminal._firestore_meta('BNY')
    assert 'security_id' not in before
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    assert terminal._firestore_meta('BNY')['security_id'] == si.BNY_EVENT['security_id']


def test_current_kilby_compatibility_and_no_ancestry_metadata(db):
    import partner_api
    import json
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    assert partner_api._resolve("BNY")[:2][0] == "BNY"
    response = partner_api._resolve("BK")[2]
    assert response.status_code == 409
    body = json.loads(response.body)
    assert "security_resolution" not in body
    assert "renamed_to" not in storage.get_ticker_meta("BNY")


def test_dataset_freshness_is_independent_and_horizons_not_invented():
    args = dict(quote={"price": 10, "observed_at": "2026-09-09T15:00:00+00:00"},
                earnings={"period_end": "2026-03-31", "complete": True},
                estimates={"eps_avg": {"0q": 1}, "observed_on": "2026-09-09"},
                history={"through": "2026-07-17", "complete": False},
                required_period="2026-06-30", earnings_event_id="bny-2q26", released_on="2026-07-15",
                quote_not_before="2026-09-09T14:00:00+00:00", as_of="2026-09-09T16:00:00+00:00",
                estimate_target_period="2026-09-30", history_required_through="2026-09-08")
    assert si.dataset_status(**args) == {"quotes": True, "earnings": False, "estimates": False, "history": False}
    args["earnings"]["period_end"] = "2026-06-30"
    args["quote"]["observed_at"] = None
    assert si.dataset_status(**args)["earnings"] is True
    assert si.dataset_status(**args)["quotes"] is False


def test_estimate_horizons_preserve_only_explicit_provider_dates():
    from fetchers import estimate_horizons
    assert estimate_horizons(None) == {}
    assert estimate_horizons([{"period": "0q", "endDate": "2026-09-30"},
                              {"period": "0y", "endDate": "2026-12-31"}]) == {
                                  "0q": "2026-09-30", "0y": "2026-12-31"}
    assert estimate_horizons([{"period": "0q", "endDate": "2026-09-30"},
                              {"period": "0q", "endDate": "2026-06-30"},
                              {"period": "+1y", "endDate": "invalid"}]) == {}


def test_new_capture_and_future_horizons_do_not_prove_results_incorporated():
    record = {"date": "2026-09-09", "horizons": {"0q": "2026-09-30", "+1q": "2026-12-31"}}
    codes = {w['code'] for w in si.freshness_warnings('BNY', earnings_period='2026-06-30',
                                                    estimate=record, check_estimates=True)}
    assert codes == {'estimate_post_results_provenance_unverified'}
    record['earnings_event_id'] = 'bny-results-2026-q2'
    assert si.freshness_warnings('BNY', earnings_period='2026-06-30', estimate=record, check_estimates=True) == []


@pytest.mark.parametrize("module", ["pull_daily_prices", "pull_weekly_estimates", "pull_quarterly_financials"])
def test_write_failure_not_reported_as_success(module, monkeypatch):
    import importlib
    job = importlib.import_module(module)
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    assert job.firestore_write_with_retry(lambda: None, "synthetic success") is True
    def fail():
        raise RuntimeError("synthetic failed write")
    assert job.firestore_write_with_retry(fail, "synthetic failure") is False


def test_critical_ticker_failure_not_hidden_by_healthy_universe(monkeypatch):
    import pull_daily_prices as job
    monkeypatch.setattr(storage, "list_active_tickers", lambda: ["AAPL", "MSFT", "BNY"])
    monkeypatch.setattr(storage, "write_prices_batch", lambda *_: None)
    monkeypatch.setattr(job.fetchers, "fetch_prices", lambda s, **_: [] if s == "BNY" else [{"date": "2026-09-09"}])
    monkeypatch.setattr(job.time, "sleep", lambda _: None)
    monkeypatch.setattr(job, "SYMBOLS", "")
    monkeypatch.setattr(job, "LIMIT", 0)
    with pytest.raises(SystemExit):
        job.main()


def test_staging_rollback_never_overwrites_a_changed_source(db):
    plan = plan_for(db)
    migrate = executor(db, plan)
    db.fail_at = 4
    with pytest.raises(RuntimeError):
        migrate.stage()
    db.fail_at = None
    db.data["tickers/BK"]["name"] = "Concurrent observation"
    migrate.rollback()
    assert db.data["tickers/BK"]["name"] == "Concurrent observation"


def test_saved_plan_roundtrip_preserves_timestamp_and_nan(db):
    import json
    from scripts.run_ticker_migration import restore
    db.data["tickers/BK"]["missing"] = float('nan')
    db.times["tickers/BK"] = datetime(2026, 9, 8, tzinfo=timezone.utc)
    plan = plan_for(db)
    restored = restore(json.loads(json.dumps(si.portable(plan))))
    assert si.digest(restored) == si.digest(plan)
    tm.validate_plan(restored)


def test_required_dataset_dates_and_future_observations_fail():
    args = dict(quote={"price": 10, "observed_at": "2026-09-10T15:00:00+00:00"},
                earnings={"period_end": "2026-06-30", "complete": True},
                estimates={"target_period_end": "2026-09-30", "earnings_event_id": "2q", "observed_on": "2026-07-14"},
                history={"through": "2026-07-17", "complete": True}, required_period="2026-06-30",
                earnings_event_id="2q", released_on="2026-07-15", quote_not_before="2026-09-09T14:00:00+00:00",
                as_of="2026-09-09T16:00:00+00:00", estimate_target_period="2026-09-30", history_required_through="2026-09-08")
    assert si.dataset_status(**args) == {"quotes": False, "earnings": True, "estimates": False, "history": False}
    args['quote']['observed_at'] = "2026-09-09T15:00:00+00:00"
    for unusable in (float('nan'), float('inf'), True, None, 0, -1):
        args['quote']['price'] = unusable
        assert si.dataset_status(**args)['quotes'] is False
    args['estimates']['observed_on'] = "2026-09-09"
    assert si.dataset_status(**args)['estimates'] is True
    args['estimates']['earnings_event_id'] = "previous"
    assert si.dataset_status(**args)['estimates'] is False


def test_guarded_maintenance_rejects_identity_changes(db):
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    for change in [{'cik': 1}, {'currency': 'GBP'}, {'share_class': 'preferred'}, {'security_id': 'other'}, {'exchange': 'NMS'}]:
        with pytest.raises(si.IdentityError):
            storage.write_ticker_meta('BNY', {'symbol': 'BNY', **change})


def test_maintenance_rejects_changed_reporting_period(db):
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    for data in [{'period': '2026-Q2'}, {'period': '2026-Q1', 'period_end': '2026-06-30'}]:
        with pytest.raises(si.IdentityError):
            storage.write_financials('BNY', '2026-Q1', {'symbol': 'BNY', **data})


def test_history_refresh_uses_bounded_transactions_and_retains_observations(db, monkeypatch):
    migrate = executor(db, plan_for(db))
    migrate.stage()
    migrate.publish()
    sizes = []
    original = Transaction.commit
    def commit(tx):
        sizes.append(len(tx.pending))
        assert len(tx.pending) <= 500
        original(tx)
    monkeypatch.setattr(Transaction, 'commit', commit)
    from datetime import timedelta
    rows = [{'symbol': 'BNY', 'date': (datetime(2025, 1, 1) + timedelta(days=i)).date().isoformat(), 'close': i+1}
            for i in range(501)]
    storage.write_prices_batch('BNY', rows)
    storage.write_prices_batch('BNY', rows)
    assert len(sizes) == 6 and max(sizes) == 400
    assert len(storage.get_prices_history('BNY', limit=600)) == 502


def test_quarterly_job_refuses_a_newly_released_stub(monkeypatch):
    import pull_quarterly_financials as job
    monkeypatch.setattr(storage, 'list_active_tickers', lambda: ['BNY'])
    monkeypatch.setattr(storage, 'write_financials', lambda *_: None)
    monkeypatch.setattr(job.fetchers, 'fetch_financials', lambda _: [
        {'period': '2026-Q2', 'period_end': '2026-06-30', 'income': {'Diluted EPS': 2.45}, 'balance_sheet': {}, 'cash_flow': {}}])
    monkeypatch.setattr(job.fetchers, 'fetch_annual_financials', lambda _: [{'period': '2025-FY', 'period_end': '2025-12-31'}])
    monkeypatch.setattr(job.time, 'sleep', lambda _: None)
    monkeypatch.setattr(job, '_review', lambda *_: 0)
    monkeypatch.setattr(job, '_note_ticker', lambda *_: None)
    monkeypatch.setattr(job, '_records', [])
    monkeypatch.setenv('TRANCHE_COUNT', '1')
    with pytest.raises(RuntimeError, match='Financial maintenance incomplete'):
        job.main()


def test_weekly_job_refuses_fresh_capture_with_unknown_horizons(monkeypatch):
    import pull_weekly_estimates as job
    monkeypatch.setattr(storage, 'list_active_tickers', lambda: ['BNY'])
    monkeypatch.setattr(storage, 'write_estimates', lambda *_: None)
    monkeypatch.setattr(storage, 'write_ticker_meta', lambda *_: None)
    monkeypatch.setattr(job.fetchers, 'fetch_estimates', lambda _: {'date': '2026-09-09', 'eps_avg': {'0q': 1}})
    monkeypatch.setattr(job.fetchers, 'fetch_ticker_info', lambda _: {'symbol': 'BNY', 'name': 'Synthetic'})
    monkeypatch.setattr(job.time, 'sleep', lambda _: None)
    monkeypatch.setenv('TRANCHE_COUNT', '1')
    with pytest.raises(RuntimeError, match='Estimate/metadata maintenance incomplete'):
        job.main()
