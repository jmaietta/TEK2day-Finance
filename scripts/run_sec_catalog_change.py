"""Check or publish one step of a reviewed SEC catalog change to an enrolled
security's control (sec_enrollment.change_plan): stage, promote or withdraw.

Owner, 1 Nov 2026: "fix amzn the safe way". Order:
  1. stage     before the catalog change merges: the control names both digests.
  2. merge; every deploy (website service and jobs) runs the new catalog.
  3. promote   the new digest becomes the only one.
`withdraw` undoes a stage that has not been promoted. Each step writes one
control document, fenced on its exact reviewed state, and nothing else.

    python scripts/run_sec_catalog_change.py check stage --symbol AMZN \
        --old docs/amzn-proposed-enrollment-20260913.json \
        --new docs/amzn-proposed-enrollment-20261031.json \
        --receipt C:/Users/jmaie/TEK2day-Finance-backups/amzn-stage-check.json --confirm-project yfinance-cli
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sec_catalog import load_catalog  # noqa: E402
from sec_enrollment import apply_change, change_plan  # noqa: E402
from security_identity import digest, require  # noqa: E402


def binding(path, symbol):
    found = [b for b in json.loads(Path(path).read_text(encoding='utf-8'))['bindings'] if b['symbol'] == symbol]
    require(len(found) == 1, f'{path}: exactly one {symbol} binding required')
    return found[0]


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('action', choices=['check', 'apply'])
    p.add_argument('step', choices=['stage', 'promote', 'withdraw'])
    p.add_argument('--symbol', required=True)
    p.add_argument('--old', type=Path, required=True, help='the reviewed binding the control names now')
    p.add_argument('--new', type=Path, required=True, help='the reviewed binding of the catalog change')
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--confirm-project', choices=['yfinance-cli'], required=True)
    args = p.parse_args()
    require(not args.receipt.resolve().is_relative_to(ROOT) and not args.receipt.exists(), 'New private receipt required')
    old, new = binding(args.old, args.symbol), binding(args.new, args.symbol)
    require(digest(load_catalog()[args.symbol]) == digest(new), 'This checkout does not hold the new catalog')
    if args.action == 'apply':
        require(os.getenv('TEK2DAY_ALLOW_SEC_ENROLLMENT') == '1', 'Explicit enrollment write gate required')
    # As run_sec_enrollment.py: the project owner's gcloud account, not the
    # machine's default credentials, which cannot write controls.
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project=args.confirm_project, database='(default)', credentials=Credentials(token))
    snap = db.document(old['control_path']).get()
    plan = change_plan(args.step, old, new, {'exists': snap.exists, 'data': snap.to_dict() if snap.exists else None})
    outcome = apply_change(db, plan, apply=args.action == 'apply')
    receipt = {'symbol': args.symbol, 'step': args.step, 'action': args.action, 'outcome': outcome,
               'plan_sha256': digest(plan), 'old_binding_sha256': digest(old), 'new_binding_sha256': digest(new),
               'control_path': old['control_path'], 'control_before': plan['control_before']['data'],
               'control_after': plan['writes'][0]['data'], 'rollback': plan['rollback'],
               'completed_at': datetime.now(timezone.utc).isoformat()}
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, indent=1, sort_keys=True, default=str), encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ('symbol', 'step', 'action', 'outcome', 'plan_sha256')}, indent=1))


if __name__ == '__main__':
    main()
