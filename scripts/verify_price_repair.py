"""Read-only exact whole-tree verification of an approved price repair."""
import argparse
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_sec_repair import read, save
from scripts.inspect_ticker_identity import capture
from security_identity import digest, require
from price_repair import validate, audit_path, audit_value, execute


def verify_exact(plan, after):
    validate(plan)
    before = plan['capture']['tree']
    expected = {w['path']: w['data'] for w in plan['writes']}
    expected[audit_path(plan)] = audit_value(plan)
    require(set(after['records']) == set(before['records']) | set(expected), 'Unexpected added/deleted document')
    require(after['collections'] == before['collections'], 'Unexpected nested collection change')
    commit_time = after['records'][audit_path(plan)]['update_time']
    for path, record in after['records'].items():
        if path in expected:
            require(record['exists'] and digest(record['data']) == digest(expected[path]), 'Unexpected applied payload: ' + path)
            require(record['update_time'] == commit_time, 'Repair was not atomic')
        else:
            require(digest(record) == digest(before['records'][path]), 'Protected observation changed: ' + path)
    return {'exact_atomic_write_count': len(expected), 'server_commit_time': commit_time,
            'protected_documents_unchanged': len(before['records']), 'selected_price_count_after':
            sum(p.startswith(plan['root_path'] + '/prices/') and p.count('/') == plan['root_path'].count('/') + 2
                and s['exists'] for p, s in after['records'].items())}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', type=Path, required=True)
    p.add_argument('--approved-plan-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]), 'New private output required')
    plan = read(args.plan)
    require(digest(plan) == args.approved_plan_sha256, 'Wrong approved plan')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project=plan['project'], database=plan['database'], credentials=Credentials(token))
    real_commit = db._firestore_api.commit
    def read_only(request=None, **kwargs):
        require(isinstance(request, dict) and not request.get('writes'), 'Nonempty commit blocked')
        return real_commit(request=request, **kwargs)
    with patch.object(db._firestore_api, 'commit', side_effect=read_only):
        tree = capture(db, roots=[plan['root_path'], plan['control_path'], audit_path(plan)], max_documents=8000)
        verified = verify_exact(plan, tree)
        require(execute(db, plan, apply=True) == 'noop', 'Repair replay was not a no-op')
    from datetime import datetime, timezone
    save(args.output, {'plan_sha256': digest(plan), 'tree': tree, 'verification': verified,
                       'checked_at': datetime.now(timezone.utc).isoformat(), 'replay_writes': 0})
    print(verified)


if __name__ == '__main__':
    main()
