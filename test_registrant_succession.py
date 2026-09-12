"""Offline successor, native transaction, source and current-consumer contracts."""
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

import identity_storage
import registrant_succession as rs
import security_identity as si
import storage
import succession_filings as sf
import succession_migration as sm
from scripts.inspect_ticker_identity import capture
from test_ticker_identity import DB


@pytest.fixture
def db(monkeypatch):
    from google.cloud import firestore
    def transactional(fn):
        def call(tx):
            data, times = deepcopy(tx.db.data), deepcopy(tx.db.times)
            result = fn(tx)
            try:
                tx.commit()
            except Exception:
                tx.db.data, tx.db.times = data, times
                raise
            return result
        return call
    monkeypatch.setattr(firestore, 'transactional', transactional)
    value = DB({'tickers/XOM': {'symbol': 'XOM', 'cik': 34088, 'currency': 'USD', 'exchange': 'NYQ',
                              'name': 'ExxonMobil Holdings Corporation', 'active': True},
                'tickers/XOM/financials/2026-Q2': {'symbol': 'XOM', 'period': '2026-Q2', 'period_end': '2026-06-30',
                     'income': {'Total Revenue': 100, 'Net Income': 0, 'Diluted Average Shares': 20},
                     'balance_sheet': {'Total Assets': None}, 'fetched_at': '2026-08-03'},
                'tickers/XOM/financials/missing/revisions/r1': {'value': 9},
                'tickers/XOM/prices/2026-07-01': {'symbol': 'XOM', 'date': '2026-07-01', 'close': 101},
                'tickers/XOM/estimates/2026-07-01': {'symbol': 'XOM', 'date': '2026-07-01', 'horizons': {'0q': '2026-09-30'}}})
    monkeypatch.setattr(storage, 'get_db', lambda: value)
    return value


def plan_for(db):
    return sm.build_plan({'project': db.project, 'database': db._database,
                          **capture(db, roots=sm.capture_roots())}, review_basis='Synthetic offline record binding')


def migrate(db, plan):
    return sm.SuccessionMigration(db, plan, lambda: capture(db, roots=sm.capture_roots()))


def public_submissions():
    return json.loads(Path('tests/fixtures/sec_xom_submissions_20260912.json').read_text(encoding='utf-8'))


def test_successor_is_never_a_simple_rename():
    with pytest.raises(si.IdentityError, match='not a simple rename'):
        si.validate_event(rs.XOM_SUCCESSION)
    assert si.event_for('XOM') is None
    assert si.routable_event_for('XOM')['event_type'] == 'registrant_succession'


@pytest.mark.parametrize('mutation', [
    lambda e: e.update(transaction_type='acquisition_merger'),
    lambda e: e.update(transaction_type='spinoff'),
    lambda e: e.update(transaction_type='delisting'),
    lambda e: e['to'].update(cik=e['from']['cik']),
    lambda e: e['to'].update(instrument_type='adr', adr_ratio=2),
    lambda e: e['to'].update(share_class='preferred'),
    lambda e: e['to'].update(symbol='OTHER'),
    lambda e: e['to'].update(currency='CAD'),
    lambda e: e.update(exchange_ratio={'old': 2, 'new': 1}),
    lambda e: e.update(effective_at='2026-07-01T00:00:00Z'),
])
def test_unsupported_corporate_actions_rejected(mutation):
    event = deepcopy(rs.XOM_SUCCESSION)
    mutation(event)
    with pytest.raises(si.IdentityError):
        rs.validate_succession(event)


def test_chain_and_ambiguous_relationship_rejected():
    with pytest.raises(si.IdentityError, match='Ambiguous'):
        rs.succession_for('XOM', [rs.XOM_SUCCESSION, rs.XOM_SUCCESSION])


def test_live_public_joint_filing_is_one_row_with_both_registrants():
    result = sf.assemble(rs.XOM_SUCCESSION, public_submissions())
    june = [r for r in result['filings'] if r['accession'] == '0000034088-26-000093']
    assert len(june) == 1
    assert june[0]['submission_ciks'] == ['0000034088', '0002115436']
    assert '/data/34088/' in june[0]['url']
    assert any(r['accession'] == '0000034088-26-000067' for r in result['filings'])
    assert any(r['accession'] == '0001193125-26-291990' for r in result['filings'])
    assert not any(r['form'] == '25-NSE' for r in result['filings'])


@pytest.mark.parametrize('change', ['cik', 'joint_filing', 'missing_predecessor', 'new_listing'])
def test_source_conflicts_do_not_silently_merge(change):
    bodies = public_submissions()
    new = bodies['0002115436']
    if change == 'cik':
        new['cik'] = 34088
    elif change == 'joint_filing':
        new['filings']['recent']['reportDate'][0] = '2026-03-31'
    elif change == 'missing_predecessor':
        bodies.pop('0000034088')
    else:
        bodies['0000034088']['tickers'] = ['OTHER']
    with pytest.raises(si.IdentityError):
        sf.assemble(rs.XOM_SUCCESSION, bodies)


class Client:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def get(self, path):
        self.calls.append(path)
        if self.fail:
            raise RuntimeError('SEC temporarily unavailable')
        cik = path.removeprefix('submissions/CIK').removesuffix('.json')
        bodies = public_submissions()
        return bodies[cik], bodies[cik]['receipt']


def test_filings_cache_bounds_errors_and_preserves_capture_time():
    sf._CACHE.clear()
    client = Client()
    first = sf.filings_payload('XOM', client=client, now=10000)
    assert len(client.calls) == 2
    assert sf.filings_payload('XOM', client=Client(True), now=10001) == first
    stale = sf.filings_payload('XOM', client=Client(True), now=14000)
    assert stale['stale'] is True and stale['source_captures'] == first['source_captures']
    with pytest.raises(RuntimeError):
        sf.filings_payload('XOM', client=Client(True), now=100000)
    sf._CACHE.clear()
    with pytest.raises(RuntimeError):
        sf.filings_payload('XOM', client=Client(True), now=10000)
    assert not sf._CACHE


def observation():
    e = rs.XOM_SUCCESSION
    return {'series_id': e['series_id'], 'basis': e['financial_continuity']['basis'],
            'issuer_id': e['from']['issuer_id'], 'security_id': e['from']['security_id'],
            'cik': e['from']['cik'], 'currency': 'USD', 'period_start': '2026-04-01', 'period_end': '2026-06-30',
            'freq': 'Q', 'form': '10-Q', 'accession': '0000034088-26-000093',
            'source_url': e['sources'][2]['url'], 'income': {'Net Income': 0, 'Total Revenue': 100}}


def test_financial_duplicate_does_not_double_count_or_erase_zero():
    one = observation()
    two = {**deepcopy(one), 'captured_at': '2026-09-12'}
    result = rs.select_reporting_observations(rs.XOM_SUCCESSION, [one, two])
    assert len(result['selected']) == 1 and len(result['observations']) == 2
    assert result['selected'][0]['income']['Net Income'] == 0
    assert not result['review']


@pytest.mark.parametrize('change', ['conflict', 'complement', 'null', 'amendment', 'other_accession', 'duration'])
def test_conflicting_complementary_and_restatement_observations_held(change):
    one, two = observation(), observation()
    if change == 'conflict':
        two['income']['Net Income'] = 5
    elif change == 'complement':
        two['balance_sheet'] = {'Total Assets': 200}
    elif change == 'null':
        two['income']['Net Income'] = None
    elif change == 'amendment':
        two['form'] = '10-Q/A'
    elif change == 'other_accession':
        two['accession'] = '0000034088-26-000099'
    else:
        two['period_start'] = '2026-03-25'
    result = rs.select_reporting_observations(rs.XOM_SUCCESSION, [one, two])
    assert not result['selected'] and result['review']
    assert result['observations'] == [one, two]


def test_historical_source_cik_is_not_current_metadata_cik():
    one = observation()
    one['cik'] = rs.XOM_SUCCESSION['to']['cik']
    with pytest.raises(si.IdentityError, match='identity'):
        rs.select_reporting_observations(rs.XOM_SUCCESSION, [one])


def test_reviewed_joint_financials_retain_two_ciks_and_select_one_quarter():
    old, new = observation(), observation()
    new.update({k: rs.XOM_SUCCESSION['to'][k] for k in ('cik', 'issuer_id', 'security_id')})
    result = rs.select_reporting_observations(rs.XOM_SUCCESSION, [old, new])
    assert len(result['selected']) == 1 and len(result['observations']) == 2
    assert result['selected'][0]['cik'] == rs.XOM_SUCCESSION['from']['cik']
    assert result['selected'][0]['reporting_issuer_id'] == rs.XOM_SUCCESSION['from']['issuer_id']
    assert result['observations'][1]['cik'] == rs.XOM_SUCCESSION['to']['cik']
    new['income']['Net Income'] = 5
    result = rs.select_reporting_observations(rs.XOM_SUCCESSION, [old, new])
    assert not result['selected'] and result['review']


def test_atomic_publication_preserves_all_nested_documents(db):
    plan = plan_for(db)
    history = {p: deepcopy(d) for p, d in db.data.items() if p.startswith('tickers/XOM/')}
    before_key = storage.identity_cache_key('XOM')
    ex = migrate(db, plan)
    assert ex.stage() == 'staged_writers_paused'
    assert storage.get_ticker_meta('XOM')['cik'] == 34088
    with pytest.raises(si.IdentityError, match='paused'):
        storage.write_price('XOM', '2026-09-12', {'symbol': 'XOM', 'date': '2026-09-12', 'close': 105})
    assert ex.publish() == 'published'
    assert storage.get_ticker_meta('XOM')['cik'] == '0002115436'
    assert storage.public_symbol('XOM') == 'XOM'
    assert storage.identity_cache_key('XOM') != before_key
    assert {p: d for p, d in db.data.items() if p.startswith('tickers/XOM/')} == history
    assert len(plan['writes']) == 9 and plan['history_writes'] == 0
    assert ex.stage() == ex.publish() == 'already_published'


def test_interrupted_publication_and_rerun(db):
    plan = plan_for(db)
    ex = migrate(db, plan)
    ex.stage()
    before = deepcopy(db.data)
    db.fail_at = db.writes + 3
    with pytest.raises(RuntimeError, match='interrupted'):
        ex.publish()
    assert db.data == before
    assert storage.get_ticker_meta('XOM')['cik'] == 34088
    db.fail_at = None
    assert ex.publish() == 'published'


def test_rollback_and_retry_keep_audits_and_original_metadata(db):
    plan = plan_for(db)
    ex = migrate(db, plan)
    ex.stage()
    ex.publish()
    assert ex.rollback() == 'rolled_back'
    assert ex.rollback() == 'already_rolled_back'
    assert storage.get_ticker_meta('XOM') == plan['source_records']['tickers/XOM']['data']
    assert all(p in db.data for p in rs.identity_documents(rs.XOM_SUCCESSION))
    with pytest.raises(si.IdentityError):
        ex.stage()


def test_cancel_staging_before_publication(db):
    ex = migrate(db, plan_for(db))
    ex.stage()
    ex.rollback()
    assert storage.get_ticker_meta('XOM')['cik'] == 34088


def test_rollback_refuses_new_nested_observation_and_leaves_paused(db):
    plan = plan_for(db)
    ex = migrate(db, plan)
    ex.stage()
    ex.publish()
    db.data['tickers/XOM/financials/missing/revisions/r1/notes/new'] = {'value': 10}
    with pytest.raises(si.IdentityError, match='reverse plan'):
        ex.rollback()
    assert ex.held()['status'] == 'paused'


def test_source_change_between_stage_and_publish_refused(db):
    ex = migrate(db, plan_for(db))
    ex.stage()
    db.data['tickers/XOM/financials/new/revisions/r2'] = {'value': 0}
    with pytest.raises(si.IdentityError, match='tree changed'):
        ex.publish()


def test_populated_identity_destination_never_overwritten(db):
    db.data[next(iter(rs.identity_documents(rs.XOM_SUCCESSION)))] = {'cik': '9999999999'}
    with pytest.raises(si.IdentityError, match='already populated'):
        plan_for(db)


@pytest.mark.parametrize('bad', ['missing', 'loop', 'alias', 'identity', 'missing_issuer'])
def test_missing_target_alias_loop_and_identity_changes_refused(db, bad):
    plan = plan_for(db)
    ex = migrate(db, plan)
    ex.stage()
    ex.publish()
    if bad == 'missing':
        db.data.pop('tickers/XOM')
    elif bad == 'loop':
        db.data[plan['route_path']]['target_path'] = plan['route_path']
    elif bad == 'alias':
        db.data['tickers/XOM']['renamed_to'] = 'XOM'
    elif bad == 'missing_issuer':
        db.data.pop('issuers/' + rs.XOM_SUCCESSION['from']['issuer_id'])
    else:
        db.data['tickers/XOM']['security_id'] = 'other-share-class'
    with pytest.raises(si.IdentityError):
        storage.get_ticker_meta('XOM')


def test_maintenance_cannot_restore_old_cik_or_overwrite_financials(db):
    plan = plan_for(db)
    ex = migrate(db, plan)
    ex.stage()
    ex.publish()
    with pytest.raises(si.IdentityError, match='current identity'):
        storage.write_ticker_meta('XOM', {'symbol': 'XOM', 'cik': 34088})
    storage.write_ticker_meta('XOM', {'symbol': 'XOM', 'market_cap': 500})
    assert storage.get_ticker_meta('XOM')['cik'] == '0002115436'
    original = deepcopy(db.data['tickers/XOM/financials/2026-Q2'])
    incoming = deepcopy(original)
    incoming['income']['Net Income'] = 99
    storage.write_financials('XOM', '2026-Q2', incoming)
    assert db.data['tickers/XOM/financials/2026-Q2'] == original
    assert any('/identity_observations/' in p for p in db.data)
    incoming['income']['Diluted Average Shares'] = 40
    incoming['balance_sheet']['Total Assets'] = 200
    storage.backfill_financials('XOM', '2026-Q2', incoming)
    kept = db.data['tickers/XOM/financials/2026-Q2']
    assert kept['income']['Net Income'] == 0 and kept['income']['Diluted Average Shares'] == 20
    assert kept['balance_sheet']['Total Assets'] == 200
    assert kept['fetched_at'] == original['fetched_at'] and 'cik' not in kept


def test_market_dates_and_estimate_horizons_survive_guard(db):
    price = {'symbol': 'XOM', 'date': '2026-09-11', 'close': 105, 'observed_at': '2026-09-11T20:00:00Z'}
    estimate = {'symbol': 'XOM', 'date': '2026-09-11', 'horizons': {'0q': '2026-09-30'}, 'eps_avg': {'0q': 3}}
    storage.write_prices_batch('XOM', [price])
    storage.write_estimates('XOM', '2026-09-11', estimate)
    assert db.data['tickers/XOM/prices/2026-09-11']['observed_at'] == price['observed_at']
    assert storage.get_estimates('XOM', '2026-09-11')['horizons'] == estimate['horizons']
    assert storage.list_active_tickers() == ['XOM']


def test_no_automatic_xom_sec_enrollment():
    from sec_mapping import BINDINGS
    assert 'XOM' not in BINDINGS


def test_annual_and_quarter_on_same_end_date_are_separate_observations():
    quarter, annual = observation(), observation()
    for row in (quarter, annual):
        row.update(period_end='2025-12-31', accession='0000034088-26-000045')
    quarter['period_start'] = '2025-10-01'
    annual.update(period_start='2025-01-01', freq='FY', form='10-K')
    result = rs.select_reporting_observations(rs.XOM_SUCCESSION, [quarter, annual])
    assert len(result['selected']) == 2 and not result['review']


def test_firstparty_web_and_terminal_use_reviewed_lookup(monkeypatch):
    import app
    import terminal
    from io import StringIO
    from rich.console import Console
    payload = sf.assemble(rs.XOM_SUCCESSION, public_submissions())
    monkeypatch.setattr(sf, 'filings_payload', lambda symbol: deepcopy(payload))
    def forbidden():
        raise AssertionError('Reviewed successor must bypass mutable one-CIK cache')
    monkeypatch.setattr(terminal, '_load_cik_cache', forbidden)
    assert app._filings_payload('XOM') == payload
    output = StringIO()
    monkeypatch.setattr(terminal, 'console', Console(file=output, width=120))
    terminal.cmd_filings('XOM')
    assert output.getvalue().count('0000034088-26-000093') == 1
    assert '0002115436' in output.getvalue()


def test_manifest_has_no_private_financial_values(db):
    plan = plan_for(db)
    manifest = sm.public_manifest(plan)
    assert 'Net Income' not in json.dumps(si.portable(manifest))
    assert len(manifest['atomic_publish_writes']) == 9
    assert any(r['path'].endswith('/missing/revisions/r1') for r in manifest['source_documents'])
    plan['writes'][0]['data']['corruption'] = True
    with pytest.raises(si.IdentityError, match='altered'):
        sm.validate_plan(plan)
