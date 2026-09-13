"""Check or publish approved cohort controls; financial repairs use run_sec_repair.

Controls are published before catalog deployment. Publication alone does not
enroll a security in the running worker. Never executes a full-universe job.
"""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_sec_repair import read, save
from sec_enrollment import activate, control_status
from security_identity import digest, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['check', 'apply', 'pause', 'resume'])
    p.add_argument('--cohort', type=Path, required=True)
    p.add_argument('--approved-cohort-sha256', required=True)
    p.add_argument('--receipt', type=Path, required=True)
    p.add_argument('--confirm-project', choices=['yfinance-cli'], required=True)
    args = p.parse_args()
    require(not args.receipt.resolve().is_relative_to(ROOT) and not args.receipt.exists(), 'New private receipt required')
    cohort = read(args.cohort)
    require(digest(cohort) == args.approved_cohort_sha256, 'Cohort approval digest mismatch')
    require(not cohort['blocked_symbols'], 'Resolve cohort review holds before enrollment')
    if args.action != 'check':
        require(os.getenv('TEK2DAY_ALLOW_SEC_ENROLLMENT') == '1', 'Explicit enrollment write gate required')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project=args.confirm_project, database='(default)', credentials=Credentials(token))
    outcomes = []
    if args.action in {'pause', 'resume'}:
        for item in cohort['items']:
            if item['activation']:
                outcomes.append({'symbol': item['symbol'], 'outcome': control_status(
                    db, item['activation'], 'paused' if args.action == 'pause' else 'active')})
        save(args.receipt, {'cohort_sha256': digest(cohort), 'action': args.action, 'outcomes': outcomes,
                            'completed_at': datetime.now(timezone.utc).isoformat()})
        print(outcomes)
        return
    # Preflight all items before the first publication. Each publication repeats
    # the fence atomically; interruption is resumable using the same plan.
    for item in cohort['items']:
        if item['activation']:
            activate(db, item['activation'])
    for item in cohort['items']:
        if item['activation']:
            outcomes.append({'symbol': item['symbol'], 'outcome': activate(db, item['activation'], apply=args.action == 'apply')})
    save(args.receipt, {'cohort_sha256': digest(cohort), 'action': args.action, 'outcomes': outcomes,
                        'completed_at': datetime.now(timezone.utc).isoformat()})
    print(outcomes)


if __name__ == '__main__':
    main()
