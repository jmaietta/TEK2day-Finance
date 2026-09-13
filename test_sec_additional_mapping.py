"""Approved mapping: real SEC values, synthetic storage, no live data writes."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from test_sec_fallback import db, evidence, NOW
from sec_mapping import BINDINGS
from sec_fallback import build_candidate, filings_from, mapped_missing, merge_candidate
from sec_maintenance import commit_candidate
from security_identity import IdentityError


def staged(evidence):
    extra = json.loads((Path(__file__).parent / 'tests/fixtures/sec_bny_2026_additional.json').read_text())
    evidence['facts']['facts']['us-gaap'].update(extra['us-gaap'])
    binding = {**deepcopy(BINDINGS['BNY']), 'profile': 'us-gaap-bank-bny-v2'}
    reports, _ = filings_from(evidence['submissions'], binding, NOW)
    return binding, reports


def candidate(evidence, binding, reports, index=-1):
    return build_candidate(evidence['facts'], evidence['source_capture'], binding, reports[index], reports)


def test_three_fields_match_filing_and_quarter_arithmetic(db, evidence):
    binding, reports = staged(evidence)
    q2 = candidate(evidence, binding, reports)
    q1 = candidate(evidence, binding, reports, 0)
    assert q2['income']['Pretax Income'] == 2_267_000_000  # 1,792 consolidated NI + 475 tax.
    assert q2['balance_sheet']['Common Stock Equity'] == 39_910_000_000  # 44,664 - 4,754.
    assert q2['cash_flow']['Cash Dividends Paid'] == -453_000_000  # -(887 YTD - 434 Q1).
    assert q1['income']['Pretax Income'] == 2_016_000_000
    assert q1['balance_sheet']['Common Stock Equity'] == 39_452_000_000
    assert q1['cash_flow']['Cash Dividends Paid'] == -434_000_000
    assert set(BINDINGS) == {'BNY'}
    assert BINDINGS['BNY']['profile'] == 'us-gaap-bank-bny-v2'
    legacy_binding = {**deepcopy(binding), 'profile': 'us-gaap-bank-bny-v1'}
    old = build_candidate(evidence['facts'], evidence['source_capture'], legacy_binding, reports[-1], reports)
    with pytest.raises(IdentityError, match='binding changed'):
        commit_candidate(db, 'BNY', old, apply=True)
    assert commit_candidate(db, 'BNY', q2)['action'] == 'fill'
    assert db.writes == 0
    assert mapped_missing(old, legacy_binding, '2026-06-30') == []
    merged, filled, conflicts = merge_candidate(old, q2)
    assert filled == ['balance_sheet.Common Stock Equity', 'cash_flow.Cash Dividends Paid', 'income.Pretax Income']
    assert not conflicts
    assert set(mapped_missing(old, binding, '2026-06-30')) == set(filled)
    for section in ('income', 'balance_sheet', 'cash_flow'):
        assert all(merged[section][k] == v for k, v in old[section].items())


@pytest.mark.parametrize('change', ['missing_preferred', 'zero_preferred', 'wrong_date', 'ambiguous', 'missing_prior_dividend'])
def test_formula_never_turns_missing_into_zero(evidence, change):
    binding, reports = staged(evidence)
    tags = evidence['facts']['facts']['us-gaap']
    values = tags['PreferredStockValue']['units']['USD']
    selected = next(v for v in values if v['end'] == '2026-06-30')
    if change == 'missing_preferred': values.remove(selected)
    if change == 'zero_preferred': selected['val'] = 0
    if change == 'wrong_date': selected['end'] = '2026-03-31'
    if change == 'ambiguous': values.append({**selected, 'val': selected['val'] + 1})
    if change == 'missing_prior_dividend':
        tags['PaymentsOfDividends']['units']['USD'] = [v for v in tags['PaymentsOfDividends']['units']['USD'] if v['end'] != '2026-03-31']
    if change == 'ambiguous':
        with pytest.raises(IdentityError, match='Conflicting facts'):
            candidate(evidence, binding, reports)
        return
    q2 = candidate(evidence, binding, reports)
    if change == 'zero_preferred':
        assert q2['balance_sheet']['Common Stock Equity'] == 44_664_000_000
    elif change == 'missing_prior_dividend':
        assert 'Cash Dividends Paid' not in q2['cash_flow']
    else:
        assert 'Common Stock Equity' not in q2['balance_sheet']


def test_populated_zero_or_conflict_is_preserved(evidence):
    binding, reports = staged(evidence)
    q2 = candidate(evidence, binding, reports)
    old = deepcopy(q2)
    old['cash_flow']['Cash Dividends Paid'] = 0
    old['income']['Pretax Income'] = 123
    merged, filled, conflicts = merge_candidate(old, q2)
    assert not filled and len(conflicts) == 2
    assert merged['cash_flow']['Cash Dividends Paid'] == 0
    assert merged['income']['Pretax Income'] == 123


def test_staged_repair_recovery_preserves_earlier_repair(db, evidence, monkeypatch):
    from datetime import timedelta
    from scripts.inspect_ticker_identity import capture
    from security_identity import BNY_EVENT, route_path, digest
    from sec_maintenance import reviewed_repair, rollback_candidate, candidate_key
    binding, reports = staged(evidence)
    q2 = candidate(evidence, binding, reports)
    legacy_binding = {**deepcopy(binding), 'profile': 'us-gaap-bank-bny-v1'}
    prior = build_candidate(evidence['facts'], evidence['source_capture'], legacy_binding, reports[-1], reports)
    path = db.root + '/financials/2026-Q2'
    db.data[path] = deepcopy(prior)
    db.times[path] = NOW - timedelta(days=1)
    previous_audit = path + '/identity_observations/sec-prior-repair'
    db.data[previous_audit] = {'original': {'income': {}}, 'status': 'applied'}
    db.times[previous_audit] = NOW - timedelta(days=10)  # Before the fake server's September 9 commits.
    tree = capture(db, roots=[path], max_documents=200)
    plan = reviewed_repair({'project': 'yfinance-cli', 'database': '(default)', 'root_path': db.root,
                            'route_path': route_path(BNY_EVENT), 'route': db.data[route_path(BNY_EVENT)],
                            'captured_at': NOW.isoformat(), **tree}, q2)
    assert BINDINGS['BNY'] == binding  # Exercise the approved active mapping.
    db.fail_at = 2
    with pytest.raises(RuntimeError):
        commit_candidate(db, 'BNY', q2, approval=plan, apply=True, now=NOW)
    assert db.writes == 0 and db.data[path] == prior
    db.fail_at = None
    commit_candidate(db, 'BNY', q2, approval=plan, apply=True, now=NOW)
    assert db.writes == 2
    assert commit_candidate(db, 'BNY', q2, approval=plan, apply=True)['action'] == 'noop'
    assert db.writes == 2
    db.data[route_path(BNY_EVENT)]['status'] = 'paused'
    assert rollback_candidate(db, 'BNY', '2026-Q2', candidate_key(q2)) == 'rolled_back_route_still_paused'
    assert digest(db.data[path]) == digest(prior)
    assert db.data[previous_audit] == {'original': {'income': {}}, 'status': 'applied'}
