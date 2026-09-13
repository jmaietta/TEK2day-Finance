"""Read-only, bounded existing-ticker financial and SEC evidence capture.

This discovers evidence, not enrollment authority. No partner API calls.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.inspect_ticker_identity import capture
from scripts.run_sec_repair import save
from sec_fallback import SecClient
from security_identity import digest, require, event_for
from registrant_succession import succession_for


def capture_symbol(db, symbol, client):
    require(re.fullmatch(r'[A-Z0-9][A-Z0-9.-]{0,14}', symbol) is not None, 'Invalid symbol')
    require(not event_for(symbol) and not succession_for(symbol), 'Use the existing corporate-action capture')
    root = db.document('tickers/' + symbol)
    snap = root.get(timeout=30)
    meta = snap.to_dict() or {}
    require(snap.exists and not meta.get('renamed_to') and not meta.get('identity_retired'), 'Missing/retired ticker')
    cik = str(meta.get('cik')).zfill(10)
    require(re.fullmatch(r'\d{10}', cik) is not None, 'Missing valid registrant CIK')
    refs = list(root.collection('financials').list_documents(page_size=100, timeout=30))
    require(len(refs) <= 100, 'Financial period bound exceeded')
    tree = capture(db, roots=[r.path for r in refs], max_documents=2000) if refs else {'records': {}, 'collections': []}
    require(root.get(timeout=30).update_time == snap.update_time, 'Metadata changed during capture')
    submissions, submission_receipt = client.get('submissions/CIK' + cik + '.json')
    facts, receipt = client.get('api/xbrl/companyfacts/CIK' + cik + '.json')
    native = {'project': 'yfinance-cli', 'database': '(default)', 'root_path': root.path,
              'captured_at': datetime.now(timezone.utc).isoformat(), 'financial_tree_complete': True,
              'metadata': {'exists': snap.exists, 'data': meta, 'update_time': snap.update_time}, **tree}
    from sec_enrollment import binding_for
    binding = binding_for(symbol)
    if binding:
        control = db.document(binding['control_path']).get(timeout=30)
        native['control'] = {'exists': control.exists, 'data': control.to_dict(), 'update_time': control.update_time}
    return native, {'submissions': submissions, 'submission_receipt': submission_receipt,
                    'facts': facts, 'receipt': receipt}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbols', nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    require(1 <= len(set(args.symbols)) <= 20, 'Capture 1-20 named securities')
    require(not args.output.resolve().is_relative_to(ROOT) and not args.output.exists(), 'New private output required')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    captures, sources = {}, {}
    client = SecClient()
    with patch.object(db._firestore_api, 'commit', side_effect=AssertionError('Read-only capture')):
        for symbol in sorted(set(args.symbols)):
            captures[symbol], sources[symbol] = capture_symbol(db, symbol, client)
    bundle = {'schema': 1, 'as_of': datetime.now(timezone.utc).isoformat(), 'captures': captures, 'sources': sources}
    save(args.output, bundle)
    print({'bundle_sha256': digest(bundle), 'symbols': sorted(captures),
           'financial_documents': {s: len(c['records']) for s, c in captures.items()}})


if __name__ == '__main__':
    main()
