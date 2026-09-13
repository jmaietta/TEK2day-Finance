"""Generic existing-security enrollment and guarded maintenance, no alias joins."""
from copy import deepcopy

from security_identity import digest, require


def binding_for(symbol):
    from sec_mapping import BINDINGS
    b = BINDINGS.get(symbol)
    return b if b and b.get('storage_kind') == 'existing_symbol' else None


def check_metadata(meta, binding):
    require(meta and meta.get('active') is not False, 'Inactive/missing enrolled metadata')
    require(not any(meta.get(k) for k in ('renamed_to', 'identity_retired', 'security_resolution')),
            'Enrolled security acquired unreviewed continuity metadata')
    for key, expected in binding['identity_guard'].items():
        actual = str(meta.get(key)).zfill(10) if key == 'cik' else meta.get(key)
        require(actual == expected, 'Enrolled identity changed: ' + key)
    for key in ('issuer_id', 'security_id'):
        require(meta.get(key) in (None, binding[key]), 'Conflicting canonical identity')


def check_financial_input(symbol, period, incoming):
    """Check supplied source identity before a gap merge discards its metadata."""
    b = binding_for(symbol)
    if b is None:
        return
    from identity_storage import financial_frequency
    require(incoming.get('period') == period and incoming.get('symbol', symbol) == symbol,
            'Incoming enrolled financial period/security mismatch')
    financial_frequency(incoming, period)
    for key in ('cik', 'currency', 'issuer_id', 'security_id'):
        if incoming.get(key) is not None:
            actual = str(incoming[key]).zfill(10) if key == 'cik' else incoming[key]
            require(actual == b[key], 'Incoming enrolled financial identity changed: ' + key)


def context(db, symbol, *, write=False, transaction=None):
    binding = binding_for(symbol)
    require(binding is not None, 'Missing generic SEC enrollment')
    control = db.document(binding['control_path']).get(transaction=transaction).to_dict() or {}
    require(control.get('binding_sha256') == digest(binding) and control.get('root_path') == binding['root_path'],
            'Enrollment control missing or changed')
    require(all(control.get(k) == binding[k] for k in ('issuer_id', 'security_id')), 'Enrollment identity changed')
    require(control.get('status') in {'active', 'paused'}, 'Enrollment is not published')
    require(not write or control['status'] == 'active', 'Enrolled maintenance is paused')
    root = db.document(binding['root_path'])
    meta = root.get(transaction=transaction).to_dict()
    check_metadata(meta, binding)
    return symbol, root, None, control


def activation_plan(binding, metadata_snapshot, control_snapshot=None):
    """Pure exact one-control plan. No historical copy or metadata rewrite."""
    from sec_catalog import validate_binding
    from sec_catalog import validate_profile
    from sec_mapping import PROFILES, COMMON
    validate_binding(binding)
    validate_profile(binding, PROFILES, COMMON)
    check_metadata(metadata_snapshot['data'], binding)
    require(metadata_snapshot['exists'], 'Enrollment target missing')
    require(not control_snapshot or not control_snapshot.get('exists'), 'Enrollment already has a control')
    control = {'status': 'active', 'binding_sha256': digest(binding), 'root_path': binding['root_path'],
               'issuer_id': binding['issuer_id'], 'security_id': binding['security_id']}
    return {'schema': 1, 'binding': deepcopy(binding), 'metadata': deepcopy(metadata_snapshot),
            'control_before': deepcopy(control_snapshot),
            'writes': [{'path': binding['control_path'], 'data': control}],
            'rollback': 'Pause control; restore applied financial plans before retiring enrollment. Keep control/audits.'}


def guarded_write(db, symbol, entries, *, merge=False, write_once=False, expected=None):
    binding = binding_for(symbol)
    if binding is None:
        return False
    from google.cloud import firestore
    from identity_storage import financial_frequency
    from ticker_migration import reconcile

    @firestore.transactional
    def commit(tx):
        _, root, _, _ = context(db, symbol, write=True, transaction=tx)
        held = [(suffix, incoming, db.document(root.path + suffix).get(transaction=tx)) for suffix, incoming in entries]
        for suffix, incoming, snap in held:
            require(suffix == '' or (suffix.startswith('/') and len(suffix.split('/')) == 3
                    and suffix.split('/')[1] in {'financials', 'prices', 'estimates'}
                    and suffix.split('/')[2]), 'Invalid enrolled maintenance path')
            data, old = deepcopy(incoming), snap.to_dict()
            require(data.get('symbol', symbol) == symbol, 'Enrolled write symbol mismatch')
            for identity_key in ('issuer_id', 'security_id'):
                require(data.get(identity_key) in (None, binding[identity_key]), 'Enrolled write changes canonical identity')
            for key, wanted in binding['identity_guard'].items():
                if data.get(key) is not None:
                    actual = str(data[key]).zfill(10) if key == 'cik' else data[key]
                    require(actual == wanted, 'Enrolled write changes identity: ' + key)
            if expected is not None:
                require(digest(old) == digest(expected), 'Concurrent financial edit; retry')
            if suffix:
                section, key = suffix.split('/')[1:]
                require(data.get('period' if section == 'financials' else 'date') == key, 'Enrolled period path mismatch')
                if section == 'financials':
                    financial_frequency(data, key)
                    if old:
                        require(all(old.get(k) == data.get(k) for k in ('period', 'period_end'))
                                and financial_frequency(old, key) == financial_frequency(data, key), 'Reporting period changed')
            ref = db.document(root.path + suffix)
            if snap.exists and (write_once or (suffix.startswith('/prices/') and old.get('price_repair'))):
                from identity_storage import observation_values
                prior = observation_values(old, suffix)
                revised = observation_values(data, suffix)
                if suffix.startswith('/financials/'):
                    prior['freq'] = financial_frequency(old, key)
                    revised['freq'] = financial_frequency(data, key)
                if digest(prior) != digest(revised):
                    tx.set(ref.collection('identity_observations').document(digest(data)), data)
                continue
            if snap.exists:
                tx.set(ref.collection('identity_observations').document(digest(old)), old)
            if merge and old:
                data = reconcile(old, data)
            tx.set(ref, data, merge=merge)
    commit(db.transaction())
    return True


def activate(db, plan, *, apply=False):
    """Conditional, idempotent control publication for an approved cohort item."""
    from google.cloud import firestore
    binding = plan['binding']
    expected = activation_plan(binding, plan['metadata'], plan.get('control_before'))
    require(digest(plan) == digest(expected), 'Enrollment plan changed')
    @firestore.transactional
    def publish(tx):
        root = db.document(binding['root_path']).get(transaction=tx)
        ref = db.document(binding['control_path'])
        held = ref.get(transaction=tx)
        target = plan['writes'][0]['data']
        if held.exists:
            require(digest(held.to_dict()) == digest(target), 'Existing enrollment differs; review required')
            check_metadata(root.to_dict(), binding)
            return 'noop'
        require(root.exists == plan['metadata']['exists'] and root.update_time == plan['metadata']['update_time']
                and digest(root.to_dict()) == digest(plan['metadata']['data']), 'Enrollment metadata changed since review')
        if apply:
            tx.set(ref, target)
        return 'activate' if apply else 'planned'
    return publish(db.transaction())


def control_status(db, plan, status):
    """Pause before any repair, including recovery from partial enrollment."""
    from google.cloud import firestore
    require(status in {'active', 'paused'}, 'Invalid enrollment status')
    expected = activation_plan(plan['binding'], plan['metadata'], plan.get('control_before'))
    require(digest(plan) == digest(expected), 'Enrollment plan changed')
    ref = db.document(plan['binding']['control_path'])
    target = plan['writes'][0]['data']
    @firestore.transactional
    def change(tx):
        current = ref.get(transaction=tx).to_dict()
        require(digest(current) in {digest(target), digest({**target, 'status': 'paused'})},
                'Enrollment control changed')
        if status == 'active':
            check_metadata(db.document(plan['binding']['root_path']).get(transaction=tx).to_dict(), plan['binding'])
        if current['status'] != status:
            tx.set(ref, {**target, 'status': status})
        return status
    return change(db.transaction())
