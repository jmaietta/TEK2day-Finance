"""Synthetic legacy-quarter fixtures; real security routing, no network."""
from copy import deepcopy

import pytest

from test_sec_fallback import db
from identity_storage import guarded_write
from security_identity import IdentityError, digest


@pytest.mark.parametrize('frequency', ['absent', None, 'Q'])
@pytest.mark.parametrize('revised', [False, True])
def test_legacy_quarter_preserves_selection_and_revision(db, frequency, revised):
    original = {'symbol': 'BNY', 'period': '2026-Q1', 'period_end': '2026-03-31',
                'income': {'Total Revenue': 100, 'Tax Provision': 0}, 'fetched_at': 'earlier'}
    if frequency != 'absent':
        original['freq'] = frequency
    path = db.root + '/financials/2026-Q1'
    db.data[path] = deepcopy(original)
    incoming = {**deepcopy(original), 'freq': 'Q', 'fetched_at': 'later'}
    if revised:
        incoming['income']['Total Revenue'] = 101
    for _ in range(2):
        assert guarded_write(db, 'BNY', [('/financials/2026-Q1', incoming)], write_once=True)
    assert db.data[path] == original  # No metadata normalization or selected-value overwrite.
    audits = {p: v for p, v in db.data.items() if p.startswith(path + '/')}
    assert audits == ({path + '/identity_observations/' + digest(incoming): incoming} if revised else {})
    if not revised:
        assert db.writes == 0
    assert not any(p.startswith('tickers/') for p in db.data)


@pytest.mark.parametrize('change', ['annual', 'empty', 'period', 'end', 'old_annual', 'key', 'missing_annual'])
def test_actual_period_changes_and_ambiguous_frequency_fail(db, change):
    old = {'symbol': 'BNY', 'period': '2026-Q1', 'period_end': '2026-03-31', 'income': {}}
    incoming = {**old, 'freq': 'Q'}
    key = '2026-Q1'
    if change == 'annual': incoming['freq'] = 'FY'
    if change == 'empty': incoming['freq'] = ''
    if change == 'period': incoming['period'] = '2026-Q2'
    if change == 'end': incoming['period_end'] = '2026-06-30'
    if change == 'old_annual': old['freq'] = 'FY'
    if change == 'key': key = old['period'] = incoming['period'] = '2026-Q5'
    if change == 'missing_annual':
        key = old['period'] = incoming['period'] = '2025-FY'
        incoming['freq'] = 'FY'
    db.data[db.root + '/financials/' + key] = old
    before = deepcopy(db.data)
    with pytest.raises(IdentityError):
        guarded_write(db, 'BNY', [('/financials/' + key, incoming)], write_once=True)
    assert db.data == before and db.writes == 0
