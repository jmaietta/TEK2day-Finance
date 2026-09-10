"""Verify native maintenance guards while refusing every nonempty write commit.

Also reads the one NVDA July-period document requested in this session.
No job is triggered, no partner API is called, and no market-data write is allowed.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_identity import RetiredSymbol, digest, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    if args.receipt.exists():
        parser.error('Use a new receipt path')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    import storage
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    storage._db = db
    require(storage.public_symbol('BK') == 'BNY', 'migration inactive')
    ref = storage.ticker_ref('BNY').collection('financials').document('2026-Q1')
    before = ref.get()
    require(before.exists, 'expected migrated financial period missing')
    api, commits = db._firestore_api, []
    real_commit = api.commit
    def no_writes(request=None, **kwargs):
        require(isinstance(request, dict) and not request.get('writes'), 'Nonempty commit blocked by read-only verifier')
        commits.append(0)
        return real_commit(request=request, **kwargs)
    with patch.object(api, 'commit', side_effect=no_writes):
        try:
            storage.write_financials('BK', '2026-Q1', before.to_dict())
        except RetiredSymbol:
            retired_rejected = True
        else:
            raise AssertionError('Retired maintenance write accepted')
        storage.write_financials('BNY', '2026-Q1', before.to_dict())
    after = ref.get()
    require(digest(before.to_dict()) == digest(after.to_dict()) and before.update_time == after.update_time,
            'Canonical no-op changed the observation')
    require(commits == [0], 'Unexpected commit behavior')
    require(not db.document('tickers/BNY/financials/2026-Q1').get().exists, 'Legacy split record recreated')
    nvda = db.document('tickers/NVDA/financials/2026-Q3').get()
    nd = nvda.to_dict() or {}
    result = {'checked_at': datetime.now(timezone.utc).isoformat(), 'retired_write_rejected': retired_rejected,
              'canonical_financial_rerun': 'native transaction, zero writes, original observation unchanged',
              'nonempty_commits_permitted': False, 'legacy_split_record_recreated': False,
              'nvda_july': {'exists': nvda.exists, 'period_end': nd.get('period_end'), 'fetched_at': nd.get('fetched_at'),
                            'snapshot_sha256': digest(nd)},
              'limitations': 'No data refresh or job execution; scheduled maintenance still needs its own outcome check.'}
    with args.receipt.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2, sort_keys=True)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
