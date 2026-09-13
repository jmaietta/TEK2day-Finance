"""Synthetic offline price basis, identity, atomic repair and maintenance tests."""
from copy import deepcopy

import pytest

from test_sec_fallback import db
from scripts.inspect_ticker_identity import capture
from security_identity import BNY_EVENT, IdentityError, digest, route_path
import price_repair as pr


def fixture(db):
    rows = [dict(date='2026-06-' + d, Open=10., High=12., Low=9., Close=11., Volume=0.,
                 **{'Adj Close': 10., 'Stock Splits': 0., 'Dividends': 0.}) for d in ('15', '16', '17', '18')]
    for row in (rows[0], rows[-1]):
        db.data[db.root + '/prices/' + row['date']] = dict(pr.bar(row), symbol='BNY')
    source = {'symbol': 'BNY', 'metadata': {'symbol': 'BNY', 'currency': 'USD', 'exchangeName': 'NYQ', 'instrumentType': 'EQUITY'},
              'rows': rows, 'start': '2026-06-15', 'end_exclusive': '2026-06-19', 'interval': '1d',
              'auto_adjust': False, 'back_adjust': False, 'repair': False, 'retrieved_at': '2026-09-13T00:00:00Z'}
    native = {'project': db.project, 'database': db._database, 'symbol': 'BNY', 'root_path': db.root,
              'control_path': route_path(BNY_EVENT), 'identity': deepcopy(BNY_EVENT),
              'tree': capture(db, roots=[db.root, route_path(BNY_EVENT)]), 'source': source}
    return native


def plan_for(native):
    return pr.build_plan(native, start='2026-06-15', end='2026-06-19',
                         basis_review={'basis': pr.BASIS, 'rationale': 'Synthetic reviewed source', 'source_sha256': digest(native['source'])})


def test_atomic_interruption_retry_lost_reply_and_rollback(db):
    plan = plan_for(fixture(db)); before = deepcopy(db.data)
    assert pr.execute(db, plan) == 'ready' and db.writes == 0
    db.fail_at = 2
    with pytest.raises(RuntimeError): pr.execute(db, plan, apply=True)
    assert db.data == before and db.writes == 0
    db.fail_at = None
    assert pr.execute(db, plan, apply=True) == 'applied'
    assert db.writes == 3
    assert pr.execute(db, plan, apply=True) == 'noop' and db.writes == 3
    with pytest.raises(IdentityError): pr.execute(db, plan, apply=True, rollback=True)
    pr.control_status(db, plan, 'paused', apply=True)
    assert pr.execute(db, plan, apply=True, rollback=True) == 'rolled_back'
    assert all(w['path'] not in db.data for w in plan['writes'])
    pr.control_status(db, plan, 'active', apply=True)
    assert pr.execute(db, plan, apply=True) == 'held_after_rollback'
    assert all(db.data[p] == v for p, v in before.items())


@pytest.mark.parametrize('change', ['split','currency','class','venue','symbol','identity','route','null','duplicate','adjusted','zero_close','nan_volume','fractional_volume','outside','no_boundary'])
def test_unreviewed_or_ambiguous_inputs_fail_closed(db, change):
    n = fixture(db); s = n['source']; rows = s['rows']
    if change == 'split': rows[1]['Stock Splits'] = 2
    if change == 'currency': s['metadata']['currency'] = 'CAD'
    if change == 'class': n['identity']['to']['share_class'] = 'preferred'
    if change == 'venue': s['metadata']['exchangeName'] = 'NMS'
    if change == 'symbol': s['metadata']['symbol'] = 'REUSED'
    if change == 'identity': n['identity']['to']['cik'] = '0000000001'
    if change == 'route': n['tree']['records'][n['control_path']]['data']['version_path'] = 'tickers/OTHER'
    if change == 'null': rows[1]['Close'] = None
    if change == 'duplicate': rows.append(deepcopy(rows[0]))
    if change == 'adjusted': s['auto_adjust'] = True
    if change == 'zero_close': rows[1]['Close'] = 0
    if change == 'nan_volume': rows[1]['Volume'] = float('nan')
    if change == 'fractional_volume': rows[1]['Volume'] = .5
    if change == 'outside': rows[1]['date'] = '2026-06-20'
    if change == 'no_boundary': n['tree']['records'].pop(db.root + '/prices/2026-06-18')
    with pytest.raises((IdentityError, ValueError)): plan_for(n)


@pytest.mark.parametrize('change', ['populated', 'null_parent', 'nested', 'anchor', 'tamper', 'paused', 'loop', 'missing_target'])
def test_changed_native_preflight_or_plan_refused(db, change):
    plan = plan_for(fixture(db)); path = plan['writes'][0]['path']
    if change == 'populated': db.data[path] = {'close': 0}
    if change == 'null_parent': db.data[path] = {'close': None}
    if change == 'nested': db.data[path + '/revisions/one/deeper/two'] = {'close': 7}
    if change == 'anchor': db.data[plan['anchors'][0]]['close'] = 12
    if change == 'tamper': plan['writes'][0]['data']['close'] = 12
    if change == 'paused': db.data[plan['control_path']]['status'] = 'paused'
    if change == 'loop': db.data[plan['control_path']]['version_path'] = plan['control_path']
    if change == 'missing_target': db.data.pop(db.root)
    with pytest.raises(IdentityError): pr.execute(db, plan, apply=True)
    assert db.writes == 0


def test_missing_parent_descendants_are_not_empty_dates(db):
    n = fixture(db)
    p = db.root + '/prices/2026-06-16'
    db.data[p + '/old/missing/deeper/kept'] = {'volume': 0}
    n['tree'] = capture(db, roots=[db.root, route_path(BNY_EVENT)])
    with pytest.raises(IdentityError, match='descendants'): plan_for(n)


def test_populated_conflicts_retained_manifest_private_values_redacted(db):
    n = fixture(db); path = db.root + '/prices/2026-06-15'
    n['tree']['records'][path]['data']['volume'] = 918273
    plan = plan_for(n); m = pr.manifest(plan)
    assert len(plan['writes']) == 2 and plan['writes'][0]['data']['volume'] == 0
    assert plan['conflicts'][0]['stored'] == 918273
    assert '918273' not in str(m) and m['atomic_write_count'] == 3


def test_maintenance_preserves_repaired_basis_and_audits_revision(db):
    import identity_storage
    plan = plan_for(fixture(db)); pr.execute(db, plan, apply=True)
    w = plan['writes'][0]; incoming = {k:v for k,v in w['data'].items() if k not in {'price_basis', 'price_repair'}}
    writes = db.writes
    identity_storage.guarded_write(db, 'BNY', [('/prices/' + incoming['date'], incoming)])
    assert db.writes == writes  # Provenance alone does not manufacture revisions.
    incoming['close'] = 10
    identity_storage.guarded_write(db, 'BNY', [('/prices/' + incoming['date'], incoming)])
    assert db.data[w['path']] == w['data']
    assert any(p.startswith(w['path'] + '/identity_observations/') for p in db.data)
    assert pr.execute(db, plan, apply=True) == 'noop'
    pr.control_status(db, plan, 'paused', apply=True)
    with pytest.raises(IdentityError, match='descendants'): pr.execute(db, plan, apply=True, rollback=True)
    assert db.data[w['path']] == w['data']


def test_unenrolled_merger_or_successor_is_not_a_rename(db):
    n = fixture(db)
    for symbol in ('XOM', 'HOLX', 'REUSED', 'BK'):
        n['symbol'] = symbol
        with pytest.raises(IdentityError): plan_for(n)


def test_same_workflow_uses_ordinary_security_control(db):
    from sec_enrollment import binding_for, activation_plan
    import identity_storage
    n = fixture(db); binding = binding_for('AMZN')
    root = binding['root_path']; db.root = root
    db.data = {root: dict(binding['identity_guard'], active=True)}
    control = activation_plan(binding, {'exists': True, 'data': db.data[root]})['writes'][0]
    db.data[control['path']] = control['data']
    for row in (n['source']['rows'][0], n['source']['rows'][-1]):
        db.data[root + '/prices/' + row['date']] = dict(pr.bar(row), symbol='AMZN')
    n.update(symbol='AMZN', root_path=root, control_path=control['path'], identity=binding,
             tree=capture(db, roots=[root, control['path']]))
    n['source']['symbol'] = 'AMZN'
    n['source']['metadata'].update(symbol='AMZN', exchangeName='NMS')
    plan = plan_for(n)
    assert pr.execute(db, plan, apply=True) == 'applied'
    w = plan['writes'][0]; incoming = dict(w['data'], close=10)
    identity_storage.guarded_write(db, 'AMZN', [('/prices/' + incoming['date'], incoming)])
    assert db.data[w['path']] == w['data']
    assert any(p.startswith(w['path'] + '/identity_observations/') for p in db.data)


def test_whole_tree_verification_rejects_changes_to_protected_data(db):
    from scripts.verify_price_repair import verify_exact
    db.data[db.root + '/financials/old/deeper/original'] = {'value': 0}
    plan = plan_for(fixture(db)); pr.execute(db, plan, apply=True)
    after = capture(db, roots=[db.root, plan['control_path'], pr.audit_path(plan)])
    assert verify_exact(plan, after)['exact_atomic_write_count'] == 3
    after['records'][db.root + '/financials/old/deeper/original']['data']['value'] = None
    with pytest.raises(IdentityError, match='Protected'): verify_exact(plan, after)
