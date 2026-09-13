"""Offline parent-job regressions: an earlier repair must count on later runs."""
from copy import deepcopy

import pytest

from test_sec_fallback import db, evidence, translated, NOW
import sec_fallback
import sec_maintenance
import pull_quarterly_financials as job
from sec_mapping import BINDINGS


@pytest.mark.parametrize('failure', ['none', 'annual', 'write', 'amendment', 'missing', 'observe'])
def test_parent_job_uses_stored_coverage_without_hiding_real_failures(db, evidence, monkeypatch, failure):
    reports, _ = sec_fallback.filings_from(evidence['submissions'], BINDINGS['BNY'], NOW)
    for report in reports:
        doc = sec_fallback.build_candidate(evidence['facts'], evidence['source_capture'], BINDINGS['BNY'], report, reports)
        db.data[db.root + '/financials/' + doc['period']] = doc
    if failure == 'missing':
        db.data[db.root + '/financials/2026-Q2']['income']['Total Revenue'] = None
    if failure == 'amendment':
        evidence['submissions']['filings']['recent']['form'][0] = '10-Q/A'
    class Client:
        def get(self, path):
            if failure == 'missing' and not path.startswith('submissions'):
                raise RuntimeError('synthetic SEC outage prevents repairing the remaining gap')
            return (evidence['submissions'] if path.startswith('submissions') else evidence['facts'], evidence['source_capture'])
    monkeypatch.setattr(sec_maintenance, 'SecClient', Client)
    monkeypatch.setenv('TRANCHE_COUNT', '1')
    monkeypatch.setenv('TRANCHE_INDEX', '0')
    monkeypatch.setenv('SEC_FALLBACK_MODE', 'observe' if failure == 'observe' else 'apply')
    monkeypatch.setattr(job.time, 'sleep', lambda _: None)
    monkeypatch.setattr(job.storage, 'list_active_tickers', lambda: ['BNY'])
    stub = {'symbol':'BNY', 'period':'2026-Q2', 'period_end':'2026-06-30', 'freq':'Q',
            'income':{'Diluted EPS':2.45}, 'balance_sheet':{}, 'cash_flow':{}}
    monkeypatch.setattr(job.fetchers, 'fetch_financials', lambda _: [deepcopy(stub)])
    annual = {'symbol':'BNY', 'period':'2025-FY', 'period_end':'2025-12-31', 'freq':'FY',
              'income':{'Total Revenue':123}, 'balance_sheet':{}, 'cash_flow':{}}
    monkeypatch.setattr(job.fetchers, 'fetch_annual_financials', lambda _: [] if failure == 'annual' else [deepcopy(annual)])
    monkeypatch.setattr(job.storage, 'write_financials', lambda *a, **k: None)
    if failure == 'write':
        monkeypatch.setattr(job, 'firestore_write_with_retry', lambda *a: False)
    monkeypatch.setattr(job, '_review', lambda *a: 0)
    monkeypatch.setattr(job, '_records', [])
    before = deepcopy(db.data)
    if failure == 'none':
        job.main()  # Previously raised BNY:reported_period_incomplete.
    else:
        with pytest.raises(RuntimeError, match='maintenance incomplete'):
            job.main()
    assert db.data == before and db.writes == 0
