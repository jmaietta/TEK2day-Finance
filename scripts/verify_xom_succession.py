"""Bounded native verification of the approved XOM repair; no nonempty commits.

Capture evidence stays outside Git. No partner API or scheduler is called.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.inspect_ticker_identity import capture
from scripts.run_ticker_migration import restore
from security_identity import digest, portable, require, IdentityError, RetiredSymbol
from succession_migration import capture_roots, validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['before', 'published', 'guards'])
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]),
            'Use a new private evidence path outside Git')
    plan = restore(json.loads(args.plan.read_text(encoding='utf-8')))
    validate_plan(plan)
    require(digest(plan) == '44bf39c167760bab8847b3cca5476ff2cb66a2040f0dbbac33c0c77df438add1', 'Unapproved plan')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    import storage
    db = firestore.Client(project=plan['project'], database=plan['database'], credentials=Credentials(token))
    storage._db = db
    result = {'mode': args.mode, 'started_at': datetime.now(timezone.utc).isoformat(), 'plan_sha256': digest(plan)}
    try:
        if args.mode in {'before', 'published'}:
            tree = capture(db, roots=capture_roots(plan['event']), max_documents=6000)
            result['capture'] = tree
            result['tree_sha256'] = digest(tree)
            result['route'] = db.document(plan['route_path']).get().to_dict()
            if args.mode == 'before':
                result['source_match'] = digest(tree) == plan['source_tree_sha256']
                require(result['source_match'], 'Source tree changed; do not execute stale plan')
                require(not result['route'], 'Route already populated')
            else:
                original = {p: s for p, s in plan['source_records'].items() if p.startswith('tickers/XOM/')}
                current = {p: s for p, s in tree['records'].items() if p.startswith('tickers/XOM/')}
                require(digest(original) == digest(current), 'Original historical snapshots changed')
                require(tree['collections'] == plan['source_collections'], 'Nested collection set changed')
                result['original_history_unchanged'] = True
                for write in plan['writes']:
                    snap = db.document(write['path']).get()
                    require(snap.exists and digest(snap.to_dict()) == write['sha256'], 'Published write differs from manifest')
                result['published_writes_match'] = len(plan['writes'])
        if args.mode in {'published', 'guards'}:
            require(storage.public_symbol('XOM') == 'XOM', 'Symbol changed')
            meta = storage.get_ticker_meta('XOM')
            require(meta['cik'] == plan['event']['to']['cik'], 'Wrong current registrant')
            result['current_identity'] = {k: meta[k] for k in ('symbol', 'cik', 'issuer_id', 'security_id', 'reporting_series_id')}
            financials, estimates, prices = storage.get_all_financials('XOM'), storage.get_estimate_history('XOM'), storage.get_prices_history('XOM', limit=2000)
            result['reader_counts'] = {'financials': len(financials), 'estimates': len(estimates), 'prices': len(prices)}
            result['financial_periods'] = sorted(d['period'] for d in financials)
            result['cache_key'] = storage.identity_cache_key('XOM')
            require(result['reader_counts'] == {'financials': 13, 'estimates': 14, 'prices': 1328}, 'Reader coverage changed')
            require(storage.public_symbol('BK') == storage.public_symbol('BNY') == 'BNY', 'BNY first-party regression')
            try:
                storage.get_ticker_meta('BK')
            except RetiredSymbol:
                result['bk_exact_read'] = 'retired'
            else:
                raise AssertionError('Legacy exact symbol accepted')
        if args.mode == 'guards':
            ref = storage.ticker_ref('XOM').collection('financials').document('2026-Q2')
            before = ref.get()
            commits, api = [], db._firestore_api
            real_commit = api.commit
            def no_writes(request=None, **kwargs):
                require(isinstance(request, dict) and not request.get('writes'), 'Nonempty native commit forbidden by verifier')
                commits.append(0)
                return real_commit(request=request, **kwargs)
            with patch.object(api, 'commit', side_effect=no_writes):
                for data in ({'symbol': 'XOM', 'cik': 34088}, {'symbol': 'XOM', 'renamed_to': 'XOM'}):
                    try:
                        storage.write_ticker_meta('XOM', data)
                    except IdentityError:
                        pass
                    else:
                        raise AssertionError('Unsafe maintenance identity accepted')
                storage.write_financials('XOM', '2026-Q2', before.to_dict())
            after = ref.get()
            require(digest(before.to_dict()) == digest(after.to_dict()) and before.update_time == after.update_time
                    and commits == [0], 'No-op rerun changed a financial observation')
            result.update(native_financial_rerun='zero writes; identical values and update time',
                          old_cik_and_self_alias_writes_rejected=True, nonempty_commits_permitted=False)
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
    finally:
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(portable(result), handle, sort_keys=True, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'capture'}, indent=2))


if __name__ == '__main__':
    main()
