"""Synthetic multi-security enrollment, translation, recovery and isolation."""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from test_sec_fallback import db, NOW
from scripts.inspect_ticker_identity import capture
from sec_catalog import load_catalog, validate_binding, validate_profile
from sec_enrollment import activation_plan, activate, context, guarded_write, control_status
from sec_batch import run_batch, STATE_PATH
from sec_cohort import prepare, public_manifest
from sec_mapping import BINDINGS, PROFILES, COMMON
from sec_fallback import build_candidate, filings_from
import sec_maintenance as sm
from security_identity import digest, IdentityError


def binding(symbol='ALFA', number=1):
    source = {'url': 'https://example.invalid/synthetic/filing', 'publication_date': '2026-08-01',
              'effective_date': '2026-01-01', 'passage': 'SYNTHETIC common security and consolidated reporting basis.'}
    return {'symbol': symbol, 'cik': str(number).zfill(10), 'currency': 'USD', 'exchange': 'NYSE',
            'issuer_id': f'10000000-0000-4000-8000-{number:012d}',
            'security_id': f'20000000-0000-4000-8000-{number:012d}', 'share_class': 'common', 'is_adr': False,
            'storage_kind': 'existing_symbol', 'root_path': 'tickers/' + symbol,
            'control_path': f'sec_bindings/20000000-0000-4000-8000-{number:012d}', 'event_type': 'existing_security',
            'identity_guard': {'symbol': symbol, 'cik': str(number).zfill(10), 'currency': 'USD', 'exchange': 'NYQ'},
            'periods_from': '2026-01-01', 'periods_through': '2026-12-31',
            'profile': 'us-gaap-industrial-v1', 'profile_sha256': digest({'common': COMMON, 'profile': PROFILES['us-gaap-industrial-v1']}),
            'cash_flow_policy': 'contiguous_ytd', 'per_share_periods': [], 'evidence': [source['url']],
            'identity_review': {'reviewed_at': NOW.isoformat(), 'security_description': 'Synthetic common shares',
                                'reporting_basis': 'Same consolidated fiscal-year reporting basis', 'sources': [source]}}


def evidence(b):
    reports = [{'accessionNumber': f"{b['cik']}-26-00000{i}", 'filingDate': filed, 'reportDate': end,
                'form': '10-Q', 'primaryDocument': 'synthetic.htm', 'source_url': 'https://example.invalid/synthetic/' + end}
               for i, (end, filed) in enumerate([('2026-03-31', '2026-05-01'), ('2026-06-30', '2026-08-01')], 1)]
    facts = {'cik': int(b['cik']), 'facts': {'us-gaap': {}}}
    for i, f in enumerate(reports):
        for tag, val, start in [('NetIncomeLoss', 20 + i, ['2026-01-01', '2026-04-01'][i]),
                                ('RevenueFromContractWithCustomerExcludingAssessedTax', 100 + i, ['2026-01-01', '2026-04-01'][i]),
                                ('Assets', 300 + i, None),
                                ('NetCashProvidedByUsedInOperatingActivities', 40 + 60 * i, '2026-01-01')]:
            row = {'accn': f['accessionNumber'], 'filed': f['filingDate'], 'form': f['form'], 'end': f['reportDate'], 'val': val}
            if start: row['start'] = start
            facts['facts']['us-gaap'].setdefault(tag, {'units': {'USD': []}})['units']['USD'].append(row)
    submissions = {'cik': int(b['cik']), 'tickers': [b['symbol']], 'exchanges': ['NYSE'],
                   'filings': {'recent': {k: [f[k] for f in reports] for k in ['accessionNumber', 'filingDate', 'reportDate', 'form', 'primaryDocument']}}}
    return {'facts': facts, 'receipt': {'retrieved_at': NOW.isoformat(), 'url': 'https://example.invalid/facts', 'sha256': digest(facts)},
            'submissions': submissions}, reports


def native(db, b):
    root = b['root_path']
    db.data[root] = {**b['identity_guard'], 'active': True, 'name': 'Synthetic ' + b['symbol']}
    db.times[root] = NOW - timedelta(days=10)
    snap = db.document(root).get()
    tree = capture(db, roots=[root])
    return {'project': 'yfinance-cli', 'database': '(default)', 'root_path': root, 'captured_at': NOW.isoformat(),
            'financial_tree_complete': True,
            'metadata': {'exists': True, 'data': snap.to_dict(), 'update_time': snap.update_time}, **tree}


def enrolled(db, monkeypatch, b):
    n = native(db, b)
    plan = activation_plan(b, n['metadata'])
    assert activate(db, plan) == 'planned'
    assert activate(db, plan, apply=True) == 'activate'
    monkeypatch.setitem(BINDINGS, b['symbol'], b)
    return n, plan


def test_two_issuers_same_engine_exact_manifest_and_atomic_recovery(db, monkeypatch):
    bindings = [binding(), binding('BETA', 2)]
    captures = {b['symbol']: native(db, b) for b in bindings}
    sources = {b['symbol']: evidence(b)[0] for b in bindings}
    cohort = prepare(bindings, captures, sources, NOW)
    assert not cohort['blocked_symbols'] and db.writes == 0
    manifest = public_manifest(cohort)
    assert manifest['activation_write_count'] == 2 and manifest['financial_write_count'] == 8
    for item in cohort['items']:
        b = item['binding']
        activate(db, item['activation'], apply=True)
        assert activate(db, item['activation'], apply=True) == 'noop'
        monkeypatch.setitem(BINDINGS, b['symbol'], b)
        for plan in item['repairs']:
            before = deepcopy(db.data)
            db.fail_at = db.writes + 2
            with pytest.raises(RuntimeError):
                sm.commit_candidate(db, b['symbol'], plan['candidate'], approval=plan, apply=True, now=NOW)
            assert db.data == before
            db.fail_at = None
            assert sm.commit_candidate(db, b['symbol'], plan['candidate'], approval=plan, apply=True, now=NOW)['action'] == 'fill'
            assert sm.commit_candidate(db, b['symbol'], plan['candidate'], approval=plan, apply=True)['action'] == 'noop'
    assert db.data['tickers/ALFA/financials/2026-Q2']['cash_flow']['Operating Cash Flow'] == 60
    assert db.data['tickers/BETA/financials/2026-Q2']['cik'] == '0000000002'
    assert not any(p.startswith('security_data/') and 'ALFA' in str(v) for p, v in db.data.items())


@pytest.mark.parametrize('change', ['cik', 'symbol', 'currency', 'exchange', 'security_id', 'renamed_to', 'active'])
def test_identity_change_stops_all_enrolled_writers(db, monkeypatch, change):
    b = binding()
    enrolled(db, monkeypatch, b)
    db.data[b['root_path']][change] = False if change == 'active' else 'CHANGED'
    count = db.writes
    with pytest.raises(IdentityError):
        guarded_write(db, b['symbol'], [('/prices/2026-09-10', {'date': '2026-09-10', 'close': 42})])
    assert db.writes == count


def test_activation_fences_metadata_then_replays(db):
    b = binding()
    n = native(db, b)
    plan = activation_plan(b, n['metadata'])
    db.times[b['root_path']] += timedelta(seconds=1)
    with pytest.raises(IdentityError, match='changed since review'): activate(db, plan, apply=True)
    assert db.writes == 0


@pytest.mark.parametrize('change,value', [('is_adr', True), ('event_type', 'merger'), ('event_type', 'delisting'),
                                        ('event_type', 'ticker_reuse'), ('root_path', 'tickers/BETA')])
def test_non_simple_identity_never_enrolled_as_ordinary(change, value):
    b = binding(); b[change] = value
    with pytest.raises(IdentityError): validate_binding(b)


def test_class_separation_and_changed_registrant_catalog(monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace
    a, b = binding(), binding('BETA', 2)
    b.update(cik=a['cik'], issuer_id=a['issuer_id'], share_class='class B')
    b['identity_guard']['cik'] = a['cik']
    monkeypatch.setattr(Path, 'stat', lambda self: SimpleNamespace(st_size=5000))
    monkeypatch.setattr(Path, 'read_text', lambda self, **kw: json.dumps({'schema': 1, 'bindings': [a, b]}))
    assert len(load_catalog()) == 2  # separate trees/security IDs; no per-share insertion
    b['cik'] = b['identity_guard']['cik'] = '0000000002'
    with pytest.raises(IdentityError, match='registrant'): load_catalog()


def test_profile_changed_or_bank_mapping_reuse_refused():
    b = binding(); b['profile_sha256'] = '0' * 64
    with pytest.raises(IdentityError): validate_profile(b, PROFILES, COMMON)
    b['profile'] = 'us-gaap-bank-bny-v2'
    with pytest.raises(IdentityError): validate_profile(b, PROFILES, COMMON)


def test_missing_prior_cash_flow_and_closed_reporting_window():
    b = binding(); e, reports = evidence(b)
    with pytest.raises(IdentityError, match='Operating Cash Flow'):
        build_candidate(e['facts'], e['receipt'], b, reports[-1], reports[-1:])
    b['periods_through'] = '2026-03-31'
    with pytest.raises(IdentityError, match='exceeds'):
        build_candidate(e['facts'], e['receipt'], b, reports[-1], reports)
    rows, notices = filings_from(e['submissions'], b, NOW)
    assert len(rows) == 1 and notices[0]['status'] == 'review'


def test_conflicting_zero_blocks_one_symbol_without_losing_other_plan(db):
    a, b = binding(), binding('BETA', 2)
    captures = {r['symbol']: native(db, r) for r in (a, b)}
    path = a['root_path'] + '/financials/2026-Q2'
    captures['ALFA']['records'][path] = {'exists': True, 'update_time': NOW,
        'data': {'symbol': 'ALFA', 'period': '2026-Q2', 'period_end': '2026-06-30', 'income': {'Net Income': 0}}}
    cohort = prepare([a, b], captures, {r['symbol']: evidence(r)[0] for r in (a, b)}, NOW)
    assert cohort['blocked_symbols'] == ['ALFA']
    assert len(cohort['items'][1]['repairs']) == 2 and db.writes == 0


def test_enrolled_history_retained_and_rollback_isolated(db, monkeypatch):
    b = binding(); enrolled(db, monkeypatch, b)
    e, reports = evidence(b)
    candidate = build_candidate(e['facts'], e['receipt'], b, reports[-1], reports)
    path = b['root_path'] + '/financials/2026-Q2'
    old = {'symbol': b['symbol'], 'period': '2026-Q2', 'period_end': '2026-06-30', 'income': {'Net Income': None}}
    db.data[path] = deepcopy(old)
    child = path + '/history/missing/nested/kept'
    db.data[child] = {'value': 0}; db.times[child] = NOW - timedelta(days=10)
    sm.commit_candidate(db, b['symbol'], candidate, apply=True, now=NOW)
    guarded_write(db, b['symbol'], [('/financials/2026-Q2', {**old, 'income': {'Net Income': 999}})], write_once=True)
    assert db.data[path]['income']['Net Income'] == 21
    db.data[b['control_path']]['status'] = 'paused'
    with pytest.raises(IdentityError): guarded_write(db, b['symbol'], [('', {'symbol': b['symbol']})], merge=True)
    assert sm.rollback_candidate(db, b['symbol'], '2026-Q2', sm.candidate_key(candidate)) == 'rolled_back_route_still_paused'
    assert db.data[path] == old and db.data[child] == {'value': 0}


def test_batch_resume_retry_and_observe_no_checkpoint(db):
    calls = []
    def process(s):
        calls.append(s)
        return [{'symbol': s}], ['source unavailable'] if s == 'ALFA' else []
    _, failures = run_batch(db, ['BETA', 'ALFA', 'GAMA'], process, mode='apply', limit=2)
    assert calls == ['ALFA', 'BETA'] and failures and db.data[STATE_PATH]['failed_symbols'] == ['ALFA']
    calls.clear()
    run_batch(db, ['BETA', 'ALFA', 'GAMA'], process, mode='observe', limit=1)
    assert calls == ['GAMA'] and db.data[STATE_PATH]['after'] == 'BETA'
    calls.clear()
    _, failures = run_batch(db, ['BETA', 'ALFA', 'GAMA'], lambda s: (calls.append(s) or [], []), mode='apply')
    assert calls == ['GAMA', 'ALFA', 'BETA'] and not failures
    assert db.data[STATE_PATH]['failed_symbols'] == []


def test_batch_interruption_replays_only_uncheckpointed_symbol(db):
    def fail(s):
        if s == 'BETA': raise RuntimeError('process died')
        return [], []
    with pytest.raises(RuntimeError): run_batch(db, ['ALFA', 'BETA'], fail, mode='apply')
    assert db.data[STATE_PATH]['after'] == 'ALFA'
    seen = []
    run_batch(db, ['ALFA', 'BETA'], lambda s: (seen.append(s) or [], []), mode='apply', limit=1)
    assert seen == ['BETA']


def test_empty_packaged_catalog_preserves_existing_enrollment():
    assert load_catalog() == {} and set(BINDINGS) == {'BNY'}


def test_real_amzn_uses_shared_profile_and_exact_quarter():
    from pathlib import Path
    root = Path(__file__).parent
    b = json.loads((root / 'docs/amzn-proposed-enrollment-20260913.json').read_text())['bindings'][0]
    e = json.loads((root / 'tests/fixtures/sec_amzn_2026_q2.json').read_text())
    reports, _ = filings_from(e['submissions'], b, NOW)
    c = build_candidate(e['facts'], e['receipt'], b, reports[0], reports)
    assert c['income']['Total Revenue'] == 200_606_000_000
    assert c['income']['Net Income'] == 62_647_000_000
    assert c['balance_sheet']['Total Assets'] == 1_095_689_000_000
    assert c['cash_flow']['Operating Cash Flow'] == 45_387_000_000
    assert c['period_start'] == '2026-04-01' and c['period_end'] == '2026-06-30'
    assert 'Diluted EPS' not in c['income'] and 'Net PPE' not in c['balance_sheet']


def test_scheduled_cohort_uses_shared_client_and_idempotent_repairs(db, monkeypatch):
    bs = [binding(), binding('BETA', 2)]
    sources = {}
    for b in bs:
        enrolled(db, monkeypatch, b)
        sources[b['cik']] = evidence(b)[0]
    class Client:
        def get(self, path):
            cik = path.split('CIK')[1][:10]
            e = sources[cik]
            return (e['submissions'] if path.startswith('submissions') else e['facts']), e['receipt']
    kwargs = {'db': db, 'client': Client(), 'now': NOW, 'yahoo_seen': {b['symbol']: ([], []) for b in bs}}
    before = db.writes
    rows, failures = sm.run_fallback(['ALFA', 'BETA'], mode='observe', **kwargs)
    assert not failures and db.writes == before
    rows, failures = sm.run_fallback(['ALFA', 'BETA'], mode='apply', **kwargs)
    assert not failures and len([r for r in rows if r['status'] == 'fill']) == 4
    selected = deepcopy({p:v for p,v in db.data.items() if '/financials/' in p})
    sm.run_fallback(['ALFA', 'BETA'], mode='apply', **kwargs)
    assert selected == {p:v for p,v in db.data.items() if '/financials/' in p}


def test_pause_enrollment_before_any_financial_write(db, monkeypatch):
    b = binding(); _, plan = enrolled(db, monkeypatch, b)
    assert control_status(db, plan, 'paused') == 'paused'
    assert context(db, b['symbol'])[1].path == b['root_path']
    with pytest.raises(IdentityError): context(db, b['symbol'], write=True)
    assert control_status(db, plan, 'active') == 'active'


def test_batch_time_budget_and_checkpoint_race(db):
    times = iter([0, 0, 2])
    seen = []
    _, failures = run_batch(db, ['ALFA', 'BETA'], lambda s: (seen.append(s) or [], []),
                           mode='apply', seconds=1, clock=lambda: next(times))
    assert seen == ['ALFA'] and failures
    def race(s):
        db.data[STATE_PATH] = {'after': 'OTHER'}
        return [], []
    with pytest.raises(IdentityError, match='Concurrent cohort'): run_batch(db, ['ALFA'], race, mode='apply')


def test_gap_merge_cannot_hide_source_identity_change(db, monkeypatch):
    import storage
    b = binding(); enrolled(db, monkeypatch, b)
    incoming = {'symbol': 'ALFA', 'period': '2026-Q2', 'period_end': '2026-06-30',
                'cik': '0000000099', 'income': {'Net Income': 7}}
    with pytest.raises(IdentityError, match='Incoming enrolled financial identity'):
        storage.backfill_financials('ALFA', '2026-Q2', incoming, db=db)


def test_missing_control_and_root_never_create_destination(db, monkeypatch):
    b = binding(); monkeypatch.setitem(BINDINGS, 'ALFA', b)
    with pytest.raises(IdentityError): context(db, 'ALFA', write=True)
    assert 'tickers/ALFA' not in db.data


def test_packaging_includes_catalog_in_all_runtimes():
    from pathlib import Path
    import tomllib
    root = Path(__file__).parent
    config = tomllib.loads((root / 'pyproject.toml').read_text())['tool']['setuptools']
    assert 'sec_catalog' in config['packages'] and 'enrollments.json' in config['package-data']['sec_catalog']
    for docker in root.glob('Dockerfile.*'):
        if 'COPY *.py ./' in docker.read_text():
            assert 'COPY sec_catalog/ ./sec_catalog/' in docker.read_text(), docker.name
    for workflow in ['deploy-api.yml', 'deploy-jobs.yml']:
        assert '"sec_catalog/**"' in (root / '.github/workflows' / workflow).read_text(encoding='utf-8')


def test_generic_q3_ytd_uses_adjacent_noncalendar_fiscal_dates():
    b = binding(); e, reports = evidence(b)
    # A synthetic 52-week fiscal calendar; no month-end inference.
    reports[0]['reportDate'] = '2026-03-29'
    reports[1]['reportDate'] = '2026-06-28'
    for tag, obj in e['facts']['facts']['us-gaap'].items():
        for row in obj['units']['USD']:
            if row['end'] == '2026-03-31': row['end'] = '2026-03-29'
            else:
                row['end'] = '2026-06-28'
                if row.get('start') == '2026-04-01': row['start'] = '2026-03-30'
    third = {**reports[-1], 'accessionNumber': b['cik'] + '-26-000003',
             'filingDate': '2026-10-30', 'reportDate': '2026-09-27'}
    for tag, obj in e['facts']['facts']['us-gaap'].items():
        row = {**obj['units']['USD'][-1], 'accn': third['accessionNumber'],
               'filed': third['filingDate'], 'end': third['reportDate']}
        if 'start' in row:
            row['start'] = '2026-01-01' if tag == 'NetCashProvidedByUsedInOperatingActivities' else '2026-06-29'
        if tag == 'NetCashProvidedByUsedInOperatingActivities': row['val'] = 210
        obj['units']['USD'].append(row)
    reports.append(third)
    candidate = build_candidate(e['facts'], e['receipt'], b, third, reports)
    assert candidate['cash_flow']['Operating Cash Flow'] == 110
    assert candidate['period_start'] == '2026-06-29'
