"""Reviewed, bounded daily-history repairs. No discovery or I/O on import.

Only absent dates are inserted. Existing provider revisions are recorded in the
private plan; selection never infers continuity from a ticker, name, or CIK.
One transaction publishes the complete repair and its immutable source audit.
"""
from copy import deepcopy
from datetime import date
import math
import json

from security_identity import digest, require, event_for, route_path, resolve_state, portable
from sec_enrollment import binding_for

BASIS = 'yahoo_no_dividend_adjustment'
FIELDS = ('open', 'high', 'low', 'close', 'volume')


def day(value):
    require(isinstance(value, str) and date.fromisoformat(value).isoformat() == value, 'Invalid exchange date')
    return value


def bar(row):
    result = {'date': day(row['date'])}
    for field in FIELDS:
        value = row[field.title()]
        require(isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value), 'Incomplete daily bar')
        require(value >= 0 and (field == 'volume' or value > 0), 'Invalid price/volume')
        require(field != 'volume' or int(value) == value, 'Fractional volume')
        result[field] = int(value) if field == 'volume' else round(value, 4)
    require(result['low'] <= min(result['open'], result['close']) <= max(result['open'], result['close']) <= result['high'], 'Invalid OHLC ordering')
    return result


def build_plan(capture, *, start, end, basis_review):
    day(start); day(end)
    require(start < end and (date.fromisoformat(end) - date.fromisoformat(start)).days <= 100, 'Review at most 100 calendar days')
    require(capture['project'] == 'yfinance-cli' and capture['database'] == '(default)', 'Wrong database')
    symbol, root, control_path = (capture[k] for k in ('symbol', 'root_path', 'control_path'))
    event, binding = event_for(symbol), binding_for(symbol)
    require(bool(event) != bool(binding), 'Security must have exactly one reviewed relationship')
    identity = event or binding
    require(digest(identity) == digest(capture['identity']), 'Reviewed security changed')
    records = capture['tree']['records']
    require(len(records) <= 8000 and root in records and control_path in records, 'Incomplete native capture')
    require(all(p == control_path or p == root or p.startswith(root + '/') for p in records), 'Unrelated captured record')
    state = records[control_path]['data']
    require(state and state.get('status') == 'active', 'Inactive control')
    if event:
        require(symbol == event['to']['symbol'] and control_path == route_path(event), 'Use reviewed current ticker')
        require(resolve_state(symbol, event, state, write=True)[1] == root, 'Route target changed')
        require(start >= event['effective_on'], 'Pre-rename backfill needs separate source-symbol review')
        currency, exchange = event['to']['currency'], event['to']['exchange_mic']
        venues = {'XNYS': {'NYQ', 'NYSE', 'XNYS'}}.get(exchange, {exchange})
    else:
        from sec_enrollment import check_metadata
        # Ordinary securities must use their already-published exact control.
        require(control_path == binding['control_path'] and root == binding['root_path'], 'Enrollment target changed')
        require(state.get('binding_sha256') == digest(binding) and
                all(state.get(k) == binding[k] for k in ('root_path', 'issuer_id', 'security_id')), 'Enrollment control changed')
        check_metadata(records[root]['data'], binding)
        currency = binding['identity_guard']['currency']
        venues = {binding['identity_guard']['exchange']}
    metadata = records[root]['data'] or {}
    require(metadata.get('symbol') == symbol and not metadata.get('renamed_to') and not metadata.get('identity_retired'), 'Retired/missing root')
    if event:
        require(all(metadata.get(k) == event[k] for k in ('issuer_id', 'security_id')), 'Security identity mismatch')
    source = capture['source']
    provider = source['metadata']
    require(source['symbol'] == symbol and provider.get('symbol') == symbol.replace('.', '-') and
            provider.get('currency') == currency and provider.get('exchangeName') in venues and
            provider.get('instrumentType') == 'EQUITY', 'Provider security/currency/venue mismatch')
    require(source['auto_adjust'] is False and source['back_adjust'] is False and source['repair'] is False
            and source['interval'] == '1d' and source['start'] <= start < end <= source['end_exclusive'], 'Wrong source basis/range')
    require(basis_review.get('basis') == BASIS and basis_review.get('rationale') and
            basis_review.get('source_sha256') == digest(source), 'Explicit frozen price-basis review required')
    require(all(r.get('Stock Splits') == 0 for r in source['rows']), 'Split-adjusted history requires separate share-basis review')
    supplied = {day(r['date']): r for r in source['rows']}
    require(len(supplied) == len(source['rows']) and len(supplied) <= 1600, 'Duplicate/oversized source dates')
    require(all(source['start'] <= d < source['end_exclusive'] for d in supplied), 'Source date outside requested range')
    selected = {d: bar(r) for d, r in supplied.items() if start <= d < end}
    require(selected, 'No source observations')
    inserts, conflicts, anchors = [], [], []
    for d, row in sorted(selected.items()):
        path = root + '/prices/' + d
        original = records.get(path, {'exists': False, 'data': None, 'update_time': None})
        if original['exists']:
            old = original['data']
            require(old.get('date') == d and old.get('symbol') == symbol, 'Stored date/security mismatch')
            anchors.append(path)
            # Close agreement across the entire repair window is a conservative
            # basis gate, not evidence that every revised OHLC/volume is wrong.
            require(isinstance(old.get('close'), (int, float)) and math.isfinite(old['close']) and
                    abs(old['close'] - row['close']) <= 0.00011, 'Ambiguous window basis; explicit reconciliation required')
            for f in FIELDS:
                if old.get(f) != row[f]:
                    conflicts.append({'path': path, 'field': f, 'stored': old.get(f), 'provider': row[f],
                                      'selection': 'retain_existing_observation'})
            continue
        require(not any(p.startswith(path + '/') for p in records), 'Missing parent has existing descendants; review separately')
        inserts.append({'path': path, 'data': dict(row, symbol=symbol, fetched_at=source['retrieved_at'],
                       price_basis=BASIS, price_repair={'source_sha256': digest(source), 'security_id': identity['security_id']})})
    require(2 <= len(anchors) and 1 <= len(inserts) <= 90, 'Require overlapping evidence and 1-90 missing bars')
    gaps = [w['data']['date'] for w in inserts]
    require(min(records[p]['data']['date'] for p in anchors) < min(gaps) and
            max(records[p]['data']['date'] for p in anchors) > max(gaps), 'Missing dates need stored boundary observations')
    return {'format': 'price-gap-repair-v1', 'project': capture['project'], 'database': capture['database'],
            'symbol': symbol, 'root_path': root, 'control_path': control_path, 'control': deepcopy(state),
            'identity': deepcopy(identity), 'capture': deepcopy(capture), 'start': start, 'end_exclusive': end,
            'basis_review': deepcopy(basis_review), 'writes': inserts, 'anchors': anchors, 'conflicts': conflicts}


def validate(plan):
    expected = build_plan(plan['capture'], start=plan['start'], end=plan['end_exclusive'], basis_review=plan['basis_review'])
    require(digest(plan) == digest(expected), 'Plan altered')
    require(len(json.dumps(portable(audit_value(plan))).encode()) < 850000, 'Audit exceeds conservative document size bound')


def audit_path(plan):
    return 'price_repairs/' + digest(plan)


def manifest(plan):
    validate(plan)
    return {'status': 'PREPARED; NOT EXECUTED', 'plan_sha256': digest(plan), 'symbol': plan['symbol'],
            'project': plan['project'], 'database': plan['database'], 'basis_review': plan['basis_review'],
            'native_tree_sha256': digest(plan['capture']['tree']), 'source_sha256': digest(plan['capture']['source']),
            'native_document_count': len(plan['capture']['tree']['records']),
            'native_collections': plan['capture']['tree']['collections'], 'control_path': plan['control_path'],
            'control_sha256': digest(plan['control']), 'missing_dates': [w['data']['date'] for w in plan['writes']],
            'proposed_writes': [{'path': w['path'], 'sha256': digest(w['data']), 'operation': 'create only if absent'} for w in plan['writes']] +
                               [{'path': audit_path(plan), 'sha256': digest(audit_value(plan)), 'operation': 'atomic immutable source/selection audit'}],
            'atomic_write_count': len(plan['writes']) + 1, 'overlap_count': len(plan['anchors']),
            'conflicts': [{k: v for k, v in dict(c, stored_sha256=digest(c['stored']), provider_sha256=digest(c['provider'])).items()
                          if k not in {'stored', 'provider'}} for c in plan['conflicts']],
            'rollback': {'pause_control': plan['control_path'], 'delete_only_unchanged_created_paths': [w['path'] for w in plan['writes']],
                         'retain_audit': audit_path(plan), 'refuse_any_descendants_or_revisions': True,
                         'replay_after_rollback': 'held; never silently reapply'},
            'scope': 'Missing daily bars only. Existing prices, metadata, financials, estimates and aliases retained.'}


def execute(db, plan, *, apply=False, rollback=False):
    """Exact transaction fences; rollback requires paused guarded writers.

    Operator must verify deployment/drain and enumerate nested target trees
    before invocation. No partial state is published, even after a lost reply.
    """
    validate(plan)
    require(db.project == plan['project'] and db._database == plan['database'], 'Wrong database')
    from google.cloud import firestore
    from identity_storage import context
    from scripts.inspect_ticker_identity import capture
    expected = dict(plan['control'], status='paused') if rollback else plan['control']
    initial_control = db.document(plan['control_path']).get()
    require(digest(initial_control.to_dict()) == digest(expected), 'Control changed; rollback requires pause')
    # Re-enumerate nested children, including missing parents. Rollback is
    # performed only with the route paused, after old writers have drained.
    tree = capture(db, roots=[w['path'] for w in plan['writes']], max_documents=1000, max_depth=8)
    targets_only = {w['path'] for w in plan['writes']}

    @firestore.transactional
    def commit(tx):
        control_snap = db.document(plan['control_path']).get(transaction=tx)
        control = control_snap.to_dict()
        require(digest(control) == digest(expected) and control_snap.update_time == initial_control.update_time,
                'Control changed during nested preflight')
        canonical, root, _, _ = context(db, plan['symbol'], write=not rollback, transaction=tx)
        require(canonical == plan['symbol'] and root.path == plan['root_path'], 'Resolved identity changed')
        ref = db.document(audit_path(plan))
        audit_snap = ref.get(transaction=tx)
        audit = audit_snap.to_dict()
        targets = [(w, db.document(w['path']).get(transaction=tx)) for w in plan['writes']]
        anchors = [(p, db.document(p).get(transaction=tx)) for p in plan['anchors']]
        if audit:
            require(audit.get('plan_sha256') == digest(plan), 'Audit mismatch')
            if audit.get('status') == 'rolled_back':
                require(digest(audit) == digest(dict(audit_value(plan), status='rolled_back')), 'Rollback audit changed')
                require(all(not s.exists for _, s in targets), 'Rolled-back dates repopulated')
                return 'held_after_rollback'
            require(audit.get('status') == 'applied', 'Invalid audit state')
            require(digest(audit) == digest(audit_value(plan)), 'Source audit changed')
            require(all(s.exists and digest(s.to_dict()) == digest(w['data']) for w, s in targets), 'Applied bars changed')
            if not rollback:
                return 'noop'
            require(all(s.update_time == audit_snap.update_time for _, s in targets), 'Bars revised after repair; retain and review')
        else:
            require(not rollback, 'Repair has not been applied')
            require(all(not s.exists for _, s in targets), 'Destination date populated; re-review')
        require(not (set(tree['records']) - targets_only), 'Target acquired descendants; retain and review')
        if not rollback:
            for p, snap in anchors:
                original = plan['capture']['tree']['records'][p]
                require(digest(snap.to_dict()) == digest(original['data']) and snap.update_time == original['update_time'], 'Basis observation changed')
        if apply:
            for write, _ in targets:
                target = db.document(write['path'])
                if rollback:
                    tx.delete(target)
                else:
                    tx.set(target, write['data'])
            if rollback:
                tx.set(ref, dict(audit, status='rolled_back'))
            else:
                tx.set(ref, audit_value(plan))
        return 'rolled_back' if rollback else ('applied' if apply else 'ready')

    return commit(db.transaction())


def audit_value(plan):
    return {'status': 'applied', 'plan_sha256': digest(plan), 'source': plan['capture']['source'],
            'writes': plan['writes'], 'basis_review': plan['basis_review']}


def control_status(db, plan, status, *, apply=False):
    validate(plan)
    require(db.project == plan['project'] and db._database == plan['database'], 'Wrong database')
    require(status in {'active', 'paused'}, 'Invalid control status')
    from google.cloud import firestore
    @firestore.transactional
    def commit(tx):
        ref = db.document(plan['control_path'])
        state = ref.get(transaction=tx).to_dict() or {}
        require(state.get('status') in {'active', 'paused'} and
                digest(dict(state, status='active')) == digest(plan['control']), 'Control changed')
        if state['status'] == status:
            return 'noop'
        if apply:
            tx.set(ref, dict(state, status=status))
        return status
    return commit(db.transaction())
