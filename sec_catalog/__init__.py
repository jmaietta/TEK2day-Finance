"""Reviewed enrollment data, separate from the accounting engine and tickers.

The packaged catalog is empty until a cohort's controls/rollout are approved.
Adding a cohort changes JSON data, not per-company application code.
"""
import json
from pathlib import Path
import re
from uuid import UUID

from security_identity import require


def validate_binding(binding):
    symbol = binding.get('symbol', '')
    require(re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,14}', symbol) is not None, 'Invalid enrollment symbol')
    require(binding.get('storage_kind') == 'existing_symbol', 'Enrollment cannot redirect storage')
    require(binding.get('root_path') == 'tickers/' + symbol, 'Enrollment cannot join another ticker tree')
    require(re.fullmatch(r'\d{10}', binding.get('cik', '')) is not None, 'Invalid registrant CIK')
    for key in ('issuer_id', 'security_id'):
        require(str(UUID(binding[key])) == binding[key], 'Enrollment IDs must be opaque UUIDs')
    require(binding['issuer_id'] != binding['security_id'], 'Issuer and security are distinct identities')
    require(binding.get('control_path') == 'sec_bindings/' + binding['security_id'], 'Invalid enrollment control path')
    require(binding.get('currency') == 'USD' and binding.get('is_adr') is False,
            'Unsupported currency/ADR requires a different reviewed profile')
    require(binding.get('share_class') and binding.get('exchange'), 'Security/class review missing')
    require(binding.get('event_type') == 'existing_security', 'Corporate action requires continuity review')
    guard = binding.get('identity_guard') or {}
    require(guard.get('symbol') == symbol and guard.get('cik') == binding['cik']
            and guard.get('currency') == binding['currency'] and guard.get('exchange'), 'Incomplete identity guard')
    from datetime import date
    require(date.fromisoformat(binding['periods_from']) <= date.fromisoformat(binding['periods_through']),
            'Invalid reviewed reporting interval')
    review = binding.get('identity_review') or {}
    require(review.get('reviewed_at') and review.get('security_description') and review.get('reporting_basis'),
            'Explicit security/reporting basis review required')
    sources = review.get('sources') or []
    require(sources and binding.get('evidence') == [s.get('url') for s in sources], 'Evidence index mismatch')
    for source in sources:
        require(str(source.get('url', '')).startswith('https://') and source.get('passage')
                and source.get('publication_date') and source.get('effective_date'), 'Dated primary evidence required')
        date.fromisoformat(source['publication_date'])
        date.fromisoformat(source['effective_date'])
    return binding


def load_catalog(path=None):
    path = Path(path) if path else Path(__file__).with_name('enrollments.json')
    require(path.stat().st_size <= 10_000_000, 'Enrollment catalog exceeds size bound')
    data = json.loads(path.read_text(encoding='utf-8'))
    require(data.get('schema') == 1 and isinstance(data.get('bindings'), list), 'Invalid enrollment catalog')
    require(len(data['bindings']) <= 10000, 'Enrollment catalog exceeds record bound')
    result, securities, roots, registrants = {}, set(), set(), {}
    for row in data['bindings']:
        b = validate_binding(row)
        require(b['symbol'] not in result and b['security_id'] not in securities and b['root_path'] not in roots,
                'Ambiguous enrollment: duplicate symbol, security or storage root')
        require(registrants.get(b['issuer_id'], b['cik']) == b['cik'],
                'Changed registrant requires separate succession review')
        registrants[b['issuer_id']] = b['cik']
        result[b['symbol']] = b
        securities.add(b['security_id']); roots.add(b['root_path'])
    return result


def validate_profile(binding, profiles, common):
    from security_identity import digest
    require(binding.get('profile') == 'us-gaap-industrial-v1',
            'Sector-specific enrollment needs a separately reviewed reusable profile')
    require(binding.get('profile_sha256') == digest({'common': common, 'profile': profiles[binding['profile']]}),
            'Reviewed accounting definitions changed')
    require(binding.get('cash_flow_policy') in {'direct_only', 'contiguous_ytd'}, 'Unreviewed cash-flow policy')
    require(not binding.get('cash_bridges') and not binding.get('per_share_periods'),
            'Per-share adjustments and manual bridges require separate basis review')
