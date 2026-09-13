"""Pure cohort review plans. Shared translation and exact writes for any catalog."""
from copy import deepcopy

from sec_catalog import validate_binding, validate_profile
from sec_enrollment import activation_plan
from sec_fallback import filings_from, build_candidate
from sec_maintenance import make_plan, reviewed_repair
from sec_mapping import PROFILES, COMMON
from security_identity import digest, require


def prepare(bindings, captures, sources, now):
    require(1 <= len(bindings) <= 500, 'Review a bounded cohort of 1-500 securities')
    require(len({b['symbol'] for b in bindings}) == len(bindings)
            and len({b['security_id'] for b in bindings}) == len(bindings), 'Ambiguous cohort identities')
    items, blocked = [], []
    for binding in sorted(bindings, key=lambda b: b['symbol']):
        validate_binding(binding)
        validate_profile(binding, PROFILES, COMMON)
        symbol = binding['symbol']
        native, evidence = deepcopy(captures[symbol]), sources[symbol]
        require(native['project'] == 'yfinance-cli' and native['database'] == '(default)'
                and native['root_path'] == binding['root_path'] and native.get('financial_tree_complete'),
                'Incomplete or incorrect scoped native capture')
        control = native.get('control')
        enrollment = None
        if not control or not control.get('exists'):
            enrollment = activation_plan(binding, native['metadata'], control)
            state = enrollment['writes'][0]['data']
        else:
            state = control['data']
            require(state.get('status') == 'active' and state.get('binding_sha256') == digest(binding),
                    'Existing enrollment changed')
        native.update(route=state, route_path=binding['control_path'])
        reports, notices = filings_from(evidence['submissions'], binding, now)
        repairs, observations = [], []
        for report in reports:
            try:
                candidate = build_candidate(evidence['facts'], evidence['receipt'], binding, report, reports)
                path = binding['root_path'] + '/financials/' + candidate['period']
                native['records'].setdefault(path, {'exists': False, 'data': None, 'update_time': None})
                proposal = make_plan(path, native['records'][path]['data'], candidate)
                observations.append({'period': candidate['period'], 'status': proposal['action'],
                                     'filled_fields': proposal['filled_fields'], 'conflicts': proposal['conflicts'],
                                     'candidate_sha256': digest(candidate)})
                if proposal['action'] == 'fill':
                    repairs.append(reviewed_repair(native, candidate, binding=binding))
            except (ValueError, KeyError, TypeError) as exc:
                observations.append({'reportDate': report['reportDate'], 'status': 'review', 'reason': str(exc)})
        issues = [n for n in notices if n.get('status') == 'review'] + [o for o in observations if o['status'] == 'review']
        if issues:
            blocked.append(symbol)
        items.append({'symbol': symbol, 'binding': binding, 'activation': enrollment,
                      'repairs': repairs, 'observations': observations, 'notices': notices,
                      'source_sha256': digest(evidence), 'native_sha256': digest(captures[symbol])})
    return {'schema': 1, 'status': 'PREPARED; NO WRITES', 'prepared_at': now.isoformat(), 'items': items,
            'blocked_symbols': blocked,
            'activation_write_count': sum(item['activation'] is not None for item in items),
            'financial_write_count': sum(len(p['write_templates']) for i in items for p in i['repairs']),
            'rollout': 'Approve catalog/control publication and exact repairs separately; resume each item by its digest.'}


def public_manifest(cohort):
    from scripts.run_sec_repair import public_manifest as financial_manifest
    def public_observation(observation):
        row = deepcopy(observation)
        if 'conflicts' in row:
            row['conflicts'] = [{'field': c['field'], 'selected': c['selected'],
                                  'existing_sha256': digest(c['existing']), 'sec_sha256': digest(c['sec'])}
                                 for c in row['conflicts']]
        return row
    return {'schema': 1, 'cohort_sha256': digest(cohort), 'status': cohort['status'],
            'prepared_at': cohort['prepared_at'], 'blocked_symbols': cohort['blocked_symbols'],
            'activation_write_count': cohort['activation_write_count'], 'financial_write_count': cohort['financial_write_count'],
            'items': [{'symbol': i['symbol'], 'binding_sha256': digest(i['binding']),
                       'source_sha256': i['source_sha256'], 'native_sha256': i['native_sha256'],
                       'activation': None if i['activation'] is None else {
                           'plan_sha256': digest(i['activation']),
                           'writes': [{'path': w['path'], 'sha256': digest(w['data'])} for w in i['activation']['writes']],
                           'rollback': i['activation']['rollback']},
                       'repairs': [financial_manifest(p) for p in i['repairs']],
                       'observations': [public_observation(o) for o in i['observations']],
                       'notices': i['notices']} for i in cohort['items']]}
