"""Bounded read-only NVDA financial comparison requested September 10, 2026.

No partner request, Firestore write, job execution or account change. Raw
observations are saved outside the repository; stdout contains coverage only.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from security_identity import digest, portable
from scripts.inspect_ticker_identity import capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--tranche-only', action='store_true', help='Read only NVDA membership and job configuration; no Yahoo fetch')
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(ROOT):
        parser.error('Use a new private path outside the repository')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token',
                            '--account=jmaietta@ceorater.com'], check=True,
                           capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    import fetchers
    import identity_storage
    import proposals
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    started = datetime.now(timezone.utc).isoformat()
    _, root, _, _ = identity_storage.context(db, 'NVDA')
    metadata = root.get(timeout=30)
    if args.tranche_only:
        from google.cloud.firestore_v1.base_query import FieldFilter
        # Server-side count returns no other issuer's metadata. This matches
        # the job's sorted active-symbol index while the reviewed route is dormant.
        preceding = db.collection('tickers').where(filter=FieldFilter('active', '==', True)).where(
            filter=FieldFilter('__name__', '<', db.document('tickers/NVDA'))).count().get(timeout=30)[0][0].value
        configured = json.loads(subprocess.run([
            'gcloud.cmd', 'run', 'jobs', 'describe', 'quarterly-financial-pull',
            '--region=us-central1', '--project=yfinance-cli', '--account=jmaietta@ceorater.com', '--format=json'
        ], check=True, capture_output=True, text=True).stdout)
        container = configured['spec']['template']['spec']['template']['spec']['containers'][0]
        selected_env = {e['name']: e.get('value') for e in container.get('env', [])
                        if e['name'] in {'TRANCHE_COUNT', 'TRANCHE_INDEX', 'REVIEW_ENABLED'}}
        count = int(selected_env.get('TRANCHE_COUNT') or 6)
        today = datetime.now(timezone.utc)
        scheduled_index = int(selected_env.get('TRANCHE_INDEX') or today.weekday()) % count
        result = {'project': db.project, 'symbol': 'NVDA', 'captured_at': today.isoformat(),
                  'active': (metadata.to_dict() or {}).get('active'), 'preceding_active_count': preceding,
                  'tranche_count': count, 'nvda_current_tranche': preceding % count,
                  'today_tranche': scheduled_index, 'selected_job_environment': selected_env,
                  'limitation': 'Current membership only; sorted-index tranches can change when the active universe changes.'}
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(result, handle, sort_keys=True, indent=2)
        print(json.dumps(result, indent=2))
        return
    refs = []
    for ref in root.collection('financials').list_documents(page_size=100, timeout=30):
        refs.append(ref.path)
        if len(refs) > 100:
            raise ValueError('Financial document bound exceeded')
    tree = capture(db, roots=refs, max_documents=200) if refs else {'records': {}, 'collections': []}
    stored_at = datetime.now(timezone.utc).isoformat()
    provider = fetchers.fetch_financials('NVDA')
    result = {'project': db.project, 'database': '(default)', 'symbol': 'NVDA', 'started_at': started,
              'stored_captured_at': stored_at, 'provider_retrieved_at': datetime.now(timezone.utc).isoformat(),
              'metadata': {'path': root.path, 'data': metadata.to_dict(), 'update_time': metadata.update_time},
              'stored': tree, 'provider_quarterly': provider}
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(portable(result), handle, sort_keys=True, indent=2, allow_nan=False)
    def coverage(doc):
        return {'period': doc.get('period'), 'period_end': doc.get('period_end'),
                'fetched_at': doc.get('fetched_at'), 'stub': proposals.is_stub(doc),
                'finite_fields': {k: sum(proposals.finite(v) for v in (doc.get(k) or {}).values())
                                  for k in ('income', 'balance_sheet', 'cash_flow')}}
    print(json.dumps({'capture_sha256': digest(result),
                      'stored': [dict(path=p, **coverage(s['data'])) for p, s in tree['records'].items()
                                 if s['exists'] and p.count('/') == root.path.count('/') + 2],
                      'provider_quarterly': [coverage(d) for d in provider or []]}, indent=2))


if __name__ == '__main__':
    main()
