"""Bounded resumable cohort processing inside the existing financial job."""
import os
import time
import logging
from security_identity import digest, require

STATE_PATH = 'maintenance_state/sec_fallback_cohort'


def run_batch(db, symbols, process, *, mode, limit=None, seconds=None, clock=time.monotonic):
    limit = int(os.getenv('SEC_FALLBACK_MAX_SYMBOLS', '500')) if limit is None else limit
    seconds = float(os.getenv('SEC_FALLBACK_SECONDS', '900')) if seconds is None else seconds
    require(1 <= limit <= 10000 and 1 <= seconds <= 10800, 'Invalid SEC work budget')
    symbols = sorted(set(symbols))
    require(len(symbols) <= 10000 and mode in {'observe', 'apply'}, 'Invalid SEC cohort')
    ref = db.document(STATE_PATH)
    snapshot = ref.get()
    state = snapshot.to_dict() or {}
    after = state.get('after', '')
    ordered = [s for s in symbols if s > after] + [s for s in symbols if s <= after]
    unresolved = set(state.get('failed_symbols') or []) & set(symbols)
    results, failures, processed = [], [], []
    started = clock()
    for symbol in ordered:
        if len(processed) >= limit or clock() - started >= seconds:
            break
        rows, errors = process(symbol)
        results.extend(rows); failures.extend(errors); processed.append(symbol)
        if errors: unresolved.add(symbol)
        else: unresolved.discard(symbol)
        if mode == 'apply':
            from google.cloud import firestore
            next_state = {'schema': 1, 'after': symbol, 'symbols_sha256': digest(symbols),
                          'failed_symbols': sorted(unresolved)}
            @firestore.transactional
            def advance(tx):
                current = ref.get(transaction=tx)
                require(digest(current.to_dict() or {}) == digest(state), 'Concurrent cohort checkpoint; retry job')
                tx.set(ref, next_state)
            advance(db.transaction())
            state = next_state
    deferred = len(ordered) - len(processed)
    results.append({'status': 'cohort_progress', 'processed': len(processed), 'deferred': deferred,
                    'unresolved': len(unresolved), 'mode': mode, 'after': processed[-1] if processed else after})
    logging.getLogger('ydp.sec_fallback').info('SEC cohort progress %s', results[-1])
    # Capacity shortfalls and earlier unresolved symbols cannot turn green just
    # because this slice succeeded. Existing job alerts expose the backlog.
    if deferred: failures.append('sec_cohort:deferred:' + str(deferred))
    if unresolved: failures.append('sec_cohort:unresolved:' + str(len(unresolved)))
    return results, failures
