"""Offline approval fences, recovery and runtime parity; synthetic stored data."""
from copy import deepcopy
from datetime import timedelta

import pytest

from test_sec_fallback import db, evidence, translated, NOW
import sec_maintenance as sm
from scripts.inspect_ticker_identity import capture
from scripts.run_sec_repair import check_tree, public_manifest
from security_identity import BNY_EVENT, IdentityError, digest, route_path


def prepare(db, evidence, *, stub=True):
    candidate = translated(evidence)
    path = db.root + "/financials/2026-Q2"
    if stub:
        db.data[path] = {"symbol": "BNY", "period": "2026-Q2", "period_end": "2026-06-30", "freq": "Q",
                         "fetched_at": "2026-09-01T00:00:00Z", "income": {"Diluted EPS": 2.45,
                         "Tax Rate For Calcs": 0, "Net Income": None}, "balance_sheet": {}, "cash_flow": {}}
        db.times[path] = NOW - timedelta(days=1)
    tree = capture(db, roots=[path], max_documents=200)
    native = {"project": "yfinance-cli", "database": "(default)", "root_path": db.root,
              "route_path": route_path(BNY_EVENT), "route": db.data[route_path(BNY_EVENT)],
              "captured_at": NOW.isoformat(), **tree}
    return sm.reviewed_repair(native, candidate)


@pytest.mark.parametrize("stub", [False, True])
def test_review_matches_atomic_writes_and_replay(db, evidence, stub):
    plan = prepare(db, evidence, stub=stub)
    old = deepcopy(db.data.get(plan['path']))
    check_tree(db, plan)
    assert sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan)['action'] == 'fill'
    assert db.writes == 0
    sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True, now=NOW)
    expected = sm.candidate_writes(sm.make_plan(plan['path'], old, plan['candidate']),
                                  plan['route'], plan['tree'][plan['path']]['update_time'],
                                  NOW.isoformat(), approval_sha256=digest(plan))
    assert db.writes == 2
    for write in expected:
        assert digest(db.data[write['path']]) == digest(write['data'])
    if stub:
        assert db.data[plan['path']]['fetched_at'] == old['fetched_at']
        assert db.data[plan['path']]['income']['Tax Rate For Calcs'] == 0
    check_tree(db, plan)
    assert sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True)['action'] == 'noop'
    assert db.writes == 2


@pytest.mark.parametrize('change', ['value', 'timestamp', 'route', 'source', 'template', 'audit'])
def test_stale_or_changed_approval_refused(db, evidence, change):
    plan = prepare(db, evidence)
    candidate = deepcopy(plan['candidate'])
    if change == 'value': db.data[plan['path']]['income']['Net Income'] = 0
    if change == 'timestamp': db.times[plan['path']] += timedelta(seconds=1)
    if change == 'route':
        plan['route'] = deepcopy(plan['route'])
        db.data[route_path(BNY_EVENT)]['changed'] = True
    if change == 'source': candidate['sec_provenance']['source_capture']['retrieved_at'] = NOW.isoformat()
    if change == 'template': plan['write_templates'][0]['data']['income']['Net Income'] = 0
    if change == 'audit': db.data[plan['write_templates'][-1]['path']] = {'status': 'applied'}
    with pytest.raises(IdentityError):
        sm.commit_candidate(db, 'BNY', candidate, approval=plan, apply=True)
    assert db.writes == 0


def test_interruption_and_rollback_keep_nested_observations(db, evidence):
    path = db.root + '/financials/2026-Q2'
    child = path + '/original_observations/missing/nested/kept'
    db.data[child] = {'source': 'synthetic', 'value': 0}
    db.times[child] = NOW - timedelta(days=10)
    plan = prepare(db, evidence)
    assert len(plan['tree']) == 3  # Includes missing ancestor.
    db.fail_at = 2
    with pytest.raises(RuntimeError):
        sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True, now=NOW)
    assert db.writes == 0 and digest(db.data[path]) == digest(plan['tree'][path]['data'])
    db.fail_at = None
    sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True, now=NOW)
    # Missing ancestors have no timestamp; their actual children are checked.
    db.data[route_path(BNY_EVENT)]['status'] = 'paused'
    assert sm.rollback_candidate(db, 'BNY', '2026-Q2', sm.candidate_key(plan['candidate'])) == 'rolled_back_route_still_paused'
    assert digest(db.data[path]) == digest(plan['tree'][path]['data'])
    assert path + '/original_observations/missing' not in db.data
    assert db.data[child] == {'source': 'synthetic', 'value': 0}


def test_new_descendant_requires_new_preflight(db, evidence):
    plan = prepare(db, evidence)
    db.data[plan['path'] + '/late/observation'] = {'value': 1}
    with pytest.raises(IdentityError, match='tree changed'): check_tree(db, plan)
    assert db.writes == 0


def test_rollback_uses_server_time_and_holds_rerun(db, evidence):
    plan = prepare(db, evidence)
    original = deepcopy(db.data[plan['path']])
    sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True, now=NOW - timedelta(days=20))
    # Client clock was behind; ordinary pre-existing child predates the actual
    # server commit but postdates that client clock. It must survive rollback.
    audit_path = plan['write_templates'][-1]['path']
    server_time = db.times[audit_path]
    child = plan['path'] + '/history/original'
    db.data[child] = {'value': 0}
    db.times[child] = server_time - timedelta(seconds=1)
    db.data[route_path(BNY_EVENT)]['status'] = 'paused'
    assert sm.rollback_candidate(db, 'BNY', '2026-Q2', sm.candidate_key(plan['candidate'])) == 'rolled_back_route_still_paused'
    assert digest(db.data[plan['path']]) == digest(original)
    assert db.data[child] == {'value': 0}
    assert sm.rollback_candidate(db, 'BNY', '2026-Q2', sm.candidate_key(plan['candidate'])) == 'already_rolled_back'
    db.data[route_path(BNY_EVENT)]['status'] = 'active'
    assert sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True)['action'] == 'held_after_rollback'


def test_manifest_contains_digests_not_private_original_values(db, evidence):
    plan = prepare(db, evidence)
    manifest = public_manifest(plan)
    assert manifest['plan_sha256'] == digest(plan)
    assert len(manifest['proposed_writes']) == 2
    assert all('data' not in row for row in manifest['original_tree'])


def test_scheduler_skips_complete_march_and_fills_only_june(db, evidence):
    from sec_fallback import build_candidate, filings_from
    from sec_mapping import BINDINGS
    filings, _ = filings_from(evidence['submissions'], BINDINGS['BNY'], NOW)
    q1 = build_candidate(evidence['facts'], evidence['source_capture'], BINDINGS['BNY'], filings[0], filings)
    q1['income']['Total Revenue'] = 123  # Populated difference is not permission to overwrite.
    db.data[db.root + '/financials/2026-Q1'] = deepcopy(q1)
    plan = prepare(db, evidence)
    class Client:
        def get(self, path):
            return (evidence['submissions'] if path.startswith('submissions') else evidence['facts'], evidence['source_capture'])
    result, failures = sm.run_fallback(['BNY'], client=Client(), db=db, mode='observe', now=NOW,
                                     yahoo_seen={'BNY': ([], [])})
    assert not failures and db.writes == 0
    assert [r['period'] for r in result if r.get('status') == 'fill'] == ['2026-Q2']
    assert [r['period'] for r in result if r.get('status') == 'stored_complete'] == ['2026-Q1']
    result, failures = sm.run_fallback(['BNY'], client=Client(), db=db, mode='apply', now=NOW,
                                     yahoo_seen={'BNY': ([], [])})
    assert not failures and db.writes == 2
    assert db.data[db.root + '/financials/2026-Q1'] == q1
    assert sm.commit_candidate(db, 'BNY', plan['candidate'])['action'] == 'noop'


@pytest.mark.parametrize('change', ['none', 'amendment', 'facts', 'yahoo_zero'])
def test_execution_rechecks_sources_before_using_approved_candidate(db, evidence, monkeypatch, change):
    from scripts.run_sec_repair import check_sources
    import sec_fallback
    import fetchers
    plan = prepare(db, evidence)
    if change == 'amendment':
        evidence['submissions']['filings']['recent']['form'][0] = '10-Q/A'
    if change == 'facts':
        rows = evidence['facts']['facts']['us-gaap']['NetIncomeLoss']['units']['USD']
        for row in rows:
            if row.get('start') == '2026-04-01': row['val'] = 1
    class Client:
        def get(self, path):
            return (evidence['submissions'] if path.startswith('submissions') else evidence['facts'], evidence['source_capture'])
    monkeypatch.setattr(sec_fallback, 'SecClient', Client)
    yahoo = [] if change != 'yahoo_zero' else [{'period': '2026-Q2', 'period_end': '2026-06-30',
                                              'income': {'Net Income': 0}}]
    monkeypatch.setattr(fetchers, 'fetch_financials', lambda _: yahoo)
    if change == 'none': assert check_sources(plan)['candidate_key'] == sm.candidate_key(plan['candidate'])
    else:
        with pytest.raises(IdentityError): check_sources(plan)
    assert db.writes == 0


def test_operator_pause_restore_resume_and_refused_premature_resume(db, evidence):
    plan = prepare(db, evidence)
    plan = deepcopy(plan)
    sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True, now=NOW)
    assert sm.repair_route_status(db, plan, 'paused') == 'paused'
    assert sm.repair_route_status(db, plan, 'paused') == 'already_paused'
    with pytest.raises(IdentityError, match='restoration'): sm.repair_route_status(db, plan, 'active')
    sm.rollback_candidate(db, 'BNY', '2026-Q2', sm.candidate_key(plan['candidate']))
    assert sm.repair_route_status(db, plan, 'active') == 'active'
    assert sm.repair_route_status(db, plan, 'active') == 'already_active'
    assert sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True)['action'] == 'held_after_rollback'


def test_operator_cannot_pause_other_or_changed_repair(db, evidence):
    plan = deepcopy(prepare(db, evidence))
    with pytest.raises(IdentityError, match='audit'): sm.repair_route_status(db, plan, 'paused')
    sm.commit_candidate(db, 'BNY', plan['candidate'], approval=plan, apply=True, now=NOW)
    db.data[plan['path']]['income']['Net Income'] = 123
    with pytest.raises(IdentityError, match='changed'): sm.repair_route_status(db, plan, 'paused')
