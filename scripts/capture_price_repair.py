"""Read-only, bounded price evidence for one already reviewed security.

Captures the entire routed tree, including nested observations, and one explicit
Yahoo daily interval. No partner endpoint or write is used.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.inspect_ticker_identity import capture
from scripts.run_sec_repair import save
from security_identity import digest, require, event_for, route_path
from sec_enrollment import binding_for
from identity_storage import context


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--symbol', required=True)
    p.add_argument('--start', required=True)
    p.add_argument('--end', required=True, help='Exclusive exchange-local date')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    start, end = (datetime.strptime(x, '%Y-%m-%d').date() for x in (args.start, args.end))
    require(0 < (end - start).days <= 2200, 'Capture at most six years')
    require(not args.output.exists() and not args.output.resolve().is_relative_to(ROOT), 'New private output required')
    event, binding = event_for(args.symbol), binding_for(args.symbol)
    require(bool(event) != bool(binding), 'Exactly one reviewed security relationship required')
    require(args.symbol == (event['to']['symbol'] if event else binding['symbol']), 'Use current canonical ticker')
    control_path = route_path(event) if event else binding['control_path']
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    with patch.object(db._firestore_api, 'commit', side_effect=AssertionError('Read-only capture')):
        canonical, root, _, state = context(db, args.symbol, write=True)
        print('Capturing reviewed tree and every nested subcollection', flush=True)
        tree = capture(db, roots=[root.path, control_path], max_documents=8000, max_depth=8)
        require(digest(db.document(control_path).get().to_dict()) == digest(state), 'Control changed during capture')
    import yfinance as yf
    ticker = yf.Ticker(canonical.replace('.', '-'))
    history = ticker.history(start=args.start, end=args.end, interval='1d', auto_adjust=False,
                             back_adjust=False, actions=True, repair=False, keepna=True, raise_errors=True)
    require(not history.empty and len(history) <= 1600, 'Empty/oversized provider result')
    meta = ticker.get_history_metadata()
    rows = [dict(date=idx.date().isoformat(), **{str(k): float(v) for k, v in row.items()})
            for idx, row in history.iterrows()]
    source = {'provider': 'Yahoo Finance', 'symbol': canonical, 'start': args.start, 'end_exclusive': args.end,
              'auto_adjust': False, 'back_adjust': False, 'repair': False, 'interval': '1d',
              'retrieved_at': datetime.now(timezone.utc).isoformat(), 'yfinance_version': yf.__version__,
              'metadata': {k: meta.get(k) for k in ('symbol', 'currency', 'exchangeName', 'instrumentType', 'exchangeTimezoneName')},
              'rows': rows}
    result = {'project': db.project, 'database': '(default)', 'symbol': canonical, 'root_path': root.path,
              'control_path': control_path, 'identity': event or binding, 'tree': tree, 'source': source,
              'captured_at': datetime.now(timezone.utc).isoformat()}
    save(args.output, result)
    print({'capture_sha256': digest(result), 'documents': len(tree['records']), 'provider_rows': len(rows)})


if __name__ == '__main__':
    main()
