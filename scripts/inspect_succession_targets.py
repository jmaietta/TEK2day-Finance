"""Read only the reviewed XOM identity destinations using maintenance access."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from registrant_succession import XOM_SUCCESSION, identity_documents
from security_identity import route_path
from scripts.inspect_ticker_identity import capture, encode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('Use a new private path outside Git')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    tree = capture(db, roots=sorted(identity_documents(XOM_SUCCESSION)), max_documents=50)
    route = db.document(route_path(XOM_SUCCESSION)).get()
    out = {'project': db.project, 'database': db._database, **tree,
           'captured_at': datetime.now(timezone.utc).isoformat(),
           'route_before': {'exists': route.exists, 'data': route.to_dict(), 'update_time': route.update_time}}
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(out, handle, default=encode, sort_keys=True, indent=2)
    print(json.dumps({'output': str(args.output), 'destinations': len(tree['records']),
                      'populated': sum(s['exists'] for s in tree['records'].values()), 'route_exists': route.exists}))


if __name__ == '__main__':
    main()
