"""Transactional maintenance fencing for same-ticker registrant succession.

Existing prices, estimates and financial observations stay at their original
paths. Current metadata identity never propagates into historical observations.
"""
from copy import deepcopy
from datetime import date

from registrant_succession import checked_period_observation, identity_documents, succession_for
from security_identity import digest, require, route_path
from ticker_migration import reconcile


def context(db, symbol, *, write=False, transaction=None):
    event = succession_for(symbol)
    require(event is not None, 'Unreviewed succession route')
    ref = db.document('tickers/' + symbol)
    state = db.document(route_path(event)).get(transaction=transaction).to_dict() or {}
    target = ref.get(transaction=transaction).to_dict() or {}
    require(not target.get('renamed_to') and not target.get('identity_retired')
            and target.get('security_resolution') is None, 'Unresolved succession alias')
    if state:
        require(state.get('event_sha256') == digest(event) and state.get('target_path') == ref.path
                and state.get('status') in {'staging', 'active', 'paused', 'rolled_back'}, 'Invalid succession route')
        if write:
            require(state['status'] in {'active', 'rolled_back'}, 'Succession maintenance paused')
        if state['status'] in {'active', 'paused'}:
            require(date.today().isoformat() > event['effective_on'], 'Succession not effective yet')
            require(target.get('symbol') == symbol and
                    all(target.get(k) == event['to'][k] for k in ('issuer_id', 'security_id', 'cik')) and
                    target.get('reporting_series_id') == event['series_id'] and
                    target.get('identity_event_sha256') == digest(event), 'Missing or mismatched succession target')
            for path, expected in identity_documents(event).items():
                held = db.document(path).get(transaction=transaction)
                require(held.exists and digest(held.to_dict()) == digest(expected),
                        'Missing or changed issuer/security/relationship target')
    if not state or state.get('status') in {'staging', 'rolled_back'}:
        require(not target.get('identity_event_sha256') and not target.get('security_id'), 'Published identity lost its route')
    return symbol, ref, event, state


def guarded_write(db, symbol, entries, *, merge=False, write_once=False, expected=None):
    from google.cloud import firestore

    @firestore.transactional
    def commit(tx):
        _, root, event, state = context(db, symbol, write=True, transaction=tx)
        held = []
        for suffix, incoming in entries:
            parts = suffix.split('/')
            require(not suffix or (len(parts) == 3 and parts[0] == '' and
                    parts[1] in {'prices', 'financials', 'estimates'} and parts[2]), 'Invalid succession maintenance path')
            held.append((suffix, deepcopy(incoming), db.document(root.path + suffix).get(transaction=tx)))
        for suffix, data, snap in held:
            old = snap.to_dict()
            require(data.get('symbol', symbol) == symbol and not data.get('renamed_to')
                    and not data.get('identity_retired') and data.get('security_resolution') is None,
                    'Unreviewed maintenance symbol/alias')
            if expected is not None:
                require(digest(old) == digest(expected), 'Concurrent financial edit; retry review')
            if suffix:
                section, key = suffix.split('/')[1:]
                require(data.get('period' if section == 'financials' else 'date') == key, 'Maintenance period/date mismatch')
                if section == 'financials':
                    checked_period_observation(event, data)
                    if old:
                        inferred = 'FY' if key.endswith('-FY') else 'Q'
                        require(old.get('period') == data.get('period') and old.get('period_end') == data.get('period_end')
                                and (old.get('freq') or inferred) == (data.get('freq') or inferred), 'Reporting period identity changed')
                        if not write_once:
                            for block in ('income', 'balance_sheet', 'cash_flow'):
                                from ticker_migration import missing
                                require(all(missing(v) or digest(v) == digest((data.get(block) or {}).get(k))
                                            for k, v in (old.get(block) or {}).items()), 'Populated financial observation changed; review restatement')
                else:
                    # This identity review has not verified per-observation
                    # security assignments for market history or forecast basis.
                    require(not any(data.get(k) is not None for k in ('issuer_id', 'security_id', 'cik')),
                            'Market observation identity requires separate security/date review')
            elif state.get('status') == 'active':
                current = event['to']
                for k in ('issuer_id', 'security_id', 'cik', 'currency', 'cusip', 'share_class'):
                    if data.get(k) is not None:
                        value = str(data[k]).zfill(10) if k == 'cik' else data[k]
                        require(value == current[k], 'Maintenance attempted to replace reviewed current identity')
                if data.get('exchange') is not None:
                    require(data['exchange'] in {'NYQ', 'NYSE', 'XNYS'}, 'Listing venue changed')
                for k, value in {'reporting_series_id': event['series_id'], 'identity_event_sha256': digest(event)}.items():
                    require(data.get(k, value) == value, 'Maintenance relationship changed')
                data.update({k: current[k] for k in ('issuer_id', 'security_id', 'cik')})
                data.update(reporting_series_id=event['series_id'], identity_event_sha256=digest(event),
                            identity_scope='current_listing_only')
            else:
                require(not any(data.get(k) is not None for k in ('issuer_id', 'security_id', 'identity_event_sha256',
                                                                 'reporting_series_id')),
                        'Identity publication requires an approved active route')
            ref = db.document(root.path + suffix)
            if write_once and snap.exists:
                if digest({k: v for k, v in old.items() if k != 'fetched_at'}) != digest({k: v for k, v in data.items() if k != 'fetched_at'}):
                    tx.set(ref.collection('identity_observations').document(digest(data)), data)
                continue
            if snap.exists:
                tx.set(ref.collection('identity_observations').document(digest(old)), old)
            if merge and old:
                data = reconcile(old, data)
            tx.set(ref, data, merge=merge)
    commit(db.transaction())
    return True
