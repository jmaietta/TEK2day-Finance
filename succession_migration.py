"""Bounded metadata/identity publication; history remains in place, never copied.

Preparation is pure. Live methods require an explicit reviewed plan and the
same deployed transaction fences as daily/weekly/quarterly maintenance.
"""
from copy import deepcopy

from registrant_succession import XOM_SUCCESSION, identity_documents, validate_succession
from security_identity import digest, require, route_path


def capture_roots(event=XOM_SUCCESSION):
    return ['tickers/' + event['symbol'], *sorted(identity_documents(event))]


def build_plan(capture, *, event=XOM_SUCCESSION, review_basis):
    validate_succession(event)
    require(capture['project'] == 'yfinance-cli' and capture['database'] == '(default)', 'Wrong database')
    records, collections = capture['records'], capture['collections']
    root = 'tickers/' + event['symbol']
    expected_roots = capture_roots(event)
    require(review_basis and all(p in records for p in expected_roots), 'Missing independent record review/capture')
    require(all(any(p == r or p.startswith(r + '/') for r in expected_roots) and len(p.split('/')) % 2 == 0
                for p in records), 'Out-of-scope source document')
    require(all(c.startswith(root + '/') for c in collections), 'Existing identity subcollections require separate review')
    before = records[root]['data']
    require(records[root]['exists'] and before.get('symbol') == event['symbol'] and
            str(before.get('cik')).zfill(10) in {event['from']['cik'], event['to']['cik']} and
            before.get('currency') == event['to']['currency'] and before.get('exchange') in {'NYQ', 'NYSE', 'XNYS'},
            'Captured root conflicts with reviewed listing')
    require(not any(before.get(k) for k in ('security_id', 'issuer_id', 'renamed_to', 'identity_retired',
                                          'security_resolution', 'identity_event_sha256')), 'Existing identity requires independent reconciliation')
    for p in expected_roots[1:]:
        require(not records[p]['exists'], 'Identity destination already populated; do not overwrite')
    for p, snap in records.items():
        if p.startswith(root + '/') and snap['exists']:
            data = snap['data']
            # Original observations are not relabelled. A preexisting supplied
            # identity needs independent per-observation review before publish.
            require(not any(data.get(k) is not None for k in ('issuer_id', 'security_id', 'cik')),
                    'Historical identity requires per-observation binding review')
            require(data.get('symbol', event['symbol']) == event['symbol'], 'Nested ticker conflict')
    seed = {'event': event, 'records': records, 'collections': collections, 'review_basis': review_basis}
    generation = digest(seed)
    selected = deepcopy(before)
    selected.update({k: event['to'][k] for k in ('issuer_id', 'security_id', 'cik', 'name')})
    selected.update(reporting_series_id=event['series_id'], identity_event_sha256=digest(event),
                    identity_scope='current_listing_only')
    audit_path = f'succession_migrations/{generation}'
    state = {'event_sha256': digest(event), 'generation': generation, 'target_path': root, 'status': 'active'}
    values = identity_documents(event)
    values[root] = selected
    values[audit_path] = {'original_path': root, 'original_metadata': deepcopy(records[root]),
                         'source_tree_sha256': digest({'records': records, 'collections': collections}),
                         'selected_metadata_sha256': digest(selected), 'event_sha256': digest(event),
                         'review_basis': review_basis, 'nested_policy': 'All historical documents and descendants remain in place, unchanged'}
    values[route_path(event)] = state
    return {'format': 'registrant-succession-v1', 'project': capture['project'], 'database': capture['database'],
            'event': deepcopy(event), 'generation': generation, 'source_records': deepcopy(records),
            'source_collections': deepcopy(collections), 'source_tree_sha256': values[audit_path]['source_tree_sha256'],
            'review_basis': review_basis, 'root_path': root, 'route_path': route_path(event), 'route': state,
            'writes': [{'path': p, 'data': d, 'sha256': digest(d)} for p, d in sorted(values.items())],
            'conflicts': [{'path': root, 'field': k, 'before_sha256': digest(before.get(k)),
                           'after_sha256': digest(selected[k]), 'selection': 'reviewed_current_listing_only'}
                          for k in ('cik', 'name') if before.get(k) != selected[k]],
            'rollback': {'restore_metadata': root, 'route_status': 'rolled_back',
                         'retain_identity_and_audit_records': True, 'refuse_changed_history_or_metadata': True},
            'history_writes': 0, 'financial_value_writes': 0, 'sec_fallback_enrollment': False}


def validate_plan(plan):
    rebuilt = build_plan({'project': plan['project'], 'database': plan['database'], 'records': plan['source_records'],
                          'collections': plan['source_collections']}, event=plan['event'], review_basis=plan['review_basis'])
    require(digest(rebuilt) == digest(plan), 'Plan altered')
    return plan


def public_manifest(plan):
    validate_plan(plan)
    return {**{k: deepcopy(plan[k]) for k in ('format', 'project', 'database', 'generation', 'source_tree_sha256',
              'source_collections', 'root_path', 'route_path', 'route', 'conflicts', 'rollback', 'history_writes',
              'financial_value_writes', 'sec_fallback_enrollment')},
            'plan_sha256': digest(plan), 'event_sha256': digest(plan['event']),
            'source_documents': [{'path': p, 'exists': s['exists'], 'sha256': digest(s), 'update_time': s['update_time']}
                                 for p, s in sorted(plan['source_records'].items())],
            'stage_write': {'path': plan['route_path'], 'sha256': digest({**plan['route'], 'status': 'staging'})},
            'atomic_publish_writes': [{'path': w['path'], 'sha256': w['sha256']} for w in plan['writes']]}


class SuccessionMigration:
    def __init__(self, db, plan, capture_tree):
        validate_plan(plan)
        require(plan['event'] == XOM_SUCCESSION, 'Event not enrolled for this maintenance executor')
        require(db.project == plan['project'] and db._database == plan['database'], 'Wrong database')
        self.db, self.plan, self.capture_tree = db, plan, capture_tree

    def atomic(self, fn):
        from google.cloud import firestore
        return firestore.transactional(fn)(self.db.transaction())

    def held(self, tx=None):
        return self.db.document(self.plan['route_path']).get(transaction=tx).to_dict() or {}

    def owned(self, state):
        require(state.get('generation') == self.plan['generation'] and
                state.get('event_sha256') == digest(self.plan['event']), 'Another plan owns this route')

    def verify_sources(self):
        tree = self.capture_tree()
        require(digest({'records': tree['records'], 'collections': tree['collections']}) == self.plan['source_tree_sha256'],
                'Source or nested tree changed; capture and review a new plan')

    def stage(self):
        if self.held().get('status') == 'active':
            self.owned(self.held())
            return 'already_published'
        def lock(tx):
            state = self.held(tx)
            if state:
                self.owned(state)
                require(state['status'] == 'staging', 'Cannot restage completed/rolled-back migration')
            tx.set(self.db.document(self.plan['route_path']), {**self.plan['route'], 'status': 'staging'})
        self.atomic(lock)
        self.verify_sources()
        return 'staged_writers_paused'

    def publish(self):
        if self.held().get('status') == 'active':
            self.owned(self.held())
            return 'already_published'
        self.verify_sources()
        plan, db = self.plan, self.db
        def activate(tx):
            state = self.held(tx)
            self.owned(state)
            require(state['status'] == 'staging', 'Stage and drain writers first')
            snapshots = {w['path']: db.document(w['path']).get(transaction=tx) for w in plan['writes']}
            for path, snap in snapshots.items():
                if path == plan['route_path']:
                    continue
                expected = plan['source_records'].get(path, {'exists': False, 'data': None, 'update_time': None})
                require(snap.exists == expected['exists'] and digest(snap.to_dict()) == digest(expected['data'])
                        and snap.update_time == expected['update_time'], 'Publication destination changed')
            for w in plan['writes']:
                tx.set(db.document(w['path']), w['data'])
        self.atomic(activate)
        return 'published'

    def rollback(self):
        plan, db = self.plan, self.db
        def pause(tx):
            state = self.held(tx)
            self.owned(state)
            require(state['status'] in {'active', 'paused', 'staging', 'rolled_back'}, 'Invalid rollback state')
            if state['status'] == 'active':
                tx.set(db.document(plan['route_path']), {**state, 'status': 'paused'})
            return state['status']
        status = self.atomic(pause)
        if status == 'rolled_back':
            return 'already_rolled_back'
        if status != 'staging':
            tree = self.capture_tree()
            before = {p: s for p, s in plan['source_records'].items() if p.startswith(plan['root_path'] + '/')}
            after = {p: s for p, s in tree['records'].items() if p.startswith(plan['root_path'] + '/')}
            require(digest(before) == digest(after) and tree['collections'] == plan['source_collections'],
                    'Historical maintenance changed the tree; reviewed reverse plan required, route remains paused')
        def restore(tx):
            state = self.held(tx)
            self.owned(state)
            require(state['status'] in {'staging', 'paused'}, 'Rollback race')
            held = {w['path']: db.document(w['path']).get(transaction=tx) for w in plan['writes']}
            if state['status'] == 'paused':
                require(all(digest(held[w['path']].to_dict()) == w['sha256'] for w in plan['writes']
                            if w['path'] != plan['route_path']), 'Published data changed; route remains paused')
                tx.set(db.document(plan['root_path']), plan['source_records'][plan['root_path']]['data'])
            tx.set(db.document(plan['route_path']), {**state, 'status': 'rolled_back'})
        self.atomic(restore)
        return 'rolled_back'
