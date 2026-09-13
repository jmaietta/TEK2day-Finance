"""Prepare/check one price repair; explicit approval digest gates every write.

No scheduler execution, alias changes, or partner API requests. All evidence and
plans stay outside the repository; the public manifest contains only digests.
"""
import argparse
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_sec_repair import read, save
from security_identity import require, digest
from price_repair import build_plan, manifest, execute, control_status, BASIS


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['prepare', 'check', 'apply', 'pause', 'rollback', 'resume'])
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--capture', type=Path)
    p.add_argument('--manifest', type=Path)
    p.add_argument('--start')
    p.add_argument('--end')
    p.add_argument('--basis-rationale')
    p.add_argument('--approved-plan-sha256')
    p.add_argument('--confirm-project')
    p.add_argument('--receipt', type=Path)
    args = p.parse_args()
    require(not args.plan.resolve().is_relative_to(ROOT), 'Private plan required')
    if args.action == 'prepare':
        require(args.capture and args.manifest and args.start and args.end and args.basis_rationale, 'Capture/range/basis review/manifest required')
        require(not args.plan.exists() and not args.manifest.exists(), 'New outputs required')
        native = read(args.capture)
        plan = build_plan(native, start=args.start, end=args.end,
                          basis_review={'basis': BASIS, 'rationale': args.basis_rationale,
                                        'source_sha256': digest(native['source'])})
        save(args.plan, plan); save(args.manifest, manifest(plan))
        print({'plan_sha256': digest(plan), 'missing_bars': len(plan['writes']), 'atomic_writes': len(plan['writes']) + 1})
        return
    plan = read(args.plan)
    require(args.approved_plan_sha256 == digest(plan), 'Exact reviewed plan digest required')
    require(args.receipt and not args.receipt.exists() and not args.receipt.resolve().is_relative_to(ROOT), 'New private receipt required')
    write = args.action != 'check'
    if write:
        require(args.confirm_project == plan['project'] and os.getenv('TEK2DAY_ALLOW_PRICE_REPAIR') == '1', 'Explicit live write gate required')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project=plan['project'], database=plan['database'], credentials=Credentials(token))
    original = db._firestore_api.commit
    def fenced(request=None, **kwargs):
        if not write:
            require(isinstance(request, dict) and not request.get('writes'), 'Nonempty commit blocked')
        return original(request=request, **kwargs)
    with patch.object(db._firestore_api, 'commit', side_effect=fenced):
        if args.action in {'pause', 'resume'}:
            result = control_status(db, plan, 'paused' if args.action == 'pause' else 'active', apply=write)
        else:
            result = execute(db, plan, apply=write, rollback=args.action == 'rollback')
    from datetime import datetime, timezone
    receipt = {'action': args.action, 'outcome': result, 'plan_sha256': digest(plan),
               'completed_at': datetime.now(timezone.utc).isoformat(), 'database_writes_enabled': write}
    save(args.receipt, receipt)
    print(receipt)


if __name__ == '__main__':
    main()
