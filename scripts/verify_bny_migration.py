"""Read-only progress and reader verification for the approved BK/BNY plan."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_ticker_migration import restore
from security_identity import RetiredSymbol, digest, require
from ticker_migration import validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['progress', 'published'])
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--receipt', type=Path)
    args = parser.parse_args()
    if args.receipt and args.receipt.exists():
        parser.error('Use a new receipt path')
    plan = restore(json.loads(args.plan.read_text(encoding='utf-8')))
    validate_plan(plan)
    require(digest(plan) == '17f04fb3c5f56030356764b271a6fb860531be71915849798d30f019d2ab3bfd', 'unapproved plan')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    publish = plan['publish']
    route = db.document(publish['route_path']).get().to_dict() or {}
    root = db.document(publish['route']['version_path'])
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
              'plan_sha256': digest(plan), 'route': route}
    counts = {kind: root.collection(kind).count().get()[0][0].value for kind in ('financials', 'estimates', 'prices')}
    counts['audit_observations'] = db.collection('identity_migrations').document(plan['generation']).collection('observations').count().get()[0][0].value
    result['counts'] = counts
    if args.mode == 'published':
        import storage
        storage._db = db
        require(route.get('status') == 'active', 'not published')
        require(all(route.get(k) == v for k, v in publish['route'].items()), 'route differs from approved plan')
        require(storage.public_symbol('BK') == storage.public_symbol('BNY') == 'BNY', 'public resolution failed')
        try:
            storage.get_ticker_meta('BK')
        except RetiredSymbol:
            result['old_exact_symbol_read'] = 'rejected as retired'
        else:
            raise AssertionError('Old exact-symbol reader did not refuse')
        meta = storage.get_ticker_meta('BNY')
        require(digest(meta) == digest(publish['new_metadata']), 'canonical metadata changed')
        expected = {w['path']: w['sha256'] for w in plan['writes'] if w['path'].startswith(root.path + '/')}
        docs = []
        for kind in ('financials', 'estimates', 'prices'):
            docs.extend(root.collection(kind).limit(2000).stream())
        actual = {s.reference.path: digest(s.to_dict()) for s in docs}
        require(actual == expected, 'selected data differs from manifest')
        originals = {p: s for p, s in plan['source_records'].items() if p.startswith('tickers/BK/')}
        actual_originals = {}
        for kind in ('financials', 'estimates', 'prices'):
            for s in db.document('tickers/BK').collection(kind).limit(2000).stream():
                actual_originals[s.reference.path] = {'exists': s.exists, 'data': s.to_dict(), 'update_time': s.update_time}
        require(digest(actual_originals) == digest(originals), 'original history changed')
        prices = storage.get_prices_history('BNY', limit=2000)
        estimates = storage.get_estimate_history('BNY', limit=365)
        financials = [s.to_dict() for s in docs if s.reference.parent.id == 'financials']
        require(len(prices) == counts['prices'] and len(estimates) == counts['estimates'], 'reader coverage mismatch')
        require(db.document('tickers/BK').get().to_dict().get('active') is False, 'old directory remains active')
        require(digest(db.document('tickers/BNY').get().to_dict()) == digest(meta), 'canonical directory mismatch')
        result.update(original_history_unchanged=True, selected_data_matches_manifest=True,
                      public_resolution={'BK': 'BNY', 'BNY': 'BNY'},
                      metadata={'symbol': meta['symbol'], 'issuer_id': meta['issuer_id'], 'security_id': meta['security_id']},
                      stored_financial_periods=sorted(d['period'] for d in financials),
                      stored_prices_through=max(d['date'] for d in prices),
                      stored_estimates_through=max(d['date'] for d in estimates),
                      cache_key=storage.identity_cache_key('BNY'),
                      limitations=['History preserved, not refreshed', 'Financial gaps remain',
                                   'Existing estimate horizons remain unknown', 'Live Kilby acceptance is separate'])
    if args.receipt:
        with args.receipt.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, sort_keys=True, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
