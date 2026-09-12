"""Execute one separately approved XOM identity plan, after guarded deployment."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_ticker_migration import restore
from scripts.inspect_ticker_identity import capture
from security_identity import digest, require
from succession_migration import SuccessionMigration, capture_roots, validate_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['stage', 'publish', 'rollback'])
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--approved-plan-sha256', required=True)
    parser.add_argument('--confirm-project', required=True)
    parser.add_argument('--writers-deployed-and-drained', action='store_true')
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    plan = restore(json.loads(args.plan.read_text(encoding='utf-8')))
    validate_plan(plan)
    require(digest(plan) == args.approved_plan_sha256 and args.confirm_project == plan['project'], 'Approval/project mismatch')
    require(args.writers_deployed_and_drained and os.getenv('TEK2DAY_ALLOW_IDENTITY_WRITES') == '1', 'Guarded deployment/drain and explicit write enablement required')
    require(not args.receipt.exists(), 'Use a new receipt')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project=plan['project'], database=plan['database'], credentials=Credentials(token))
    migration = SuccessionMigration(db, plan, lambda: capture(db, roots=capture_roots(plan['event']), max_documents=6000))
    outcome = getattr(migration, args.action)()
    receipt = {'action': args.action, 'outcome': outcome, 'plan_sha256': digest(plan),
               'completed_at': datetime.now(timezone.utc).isoformat(),
               'route': db.document(plan['route_path']).get().to_dict()}
    with args.receipt.open('x', encoding='utf-8') as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
    print(json.dumps(receipt))


if __name__ == '__main__':
    main()
