"""Read-only preflight and exact cohort execution verification; private receipts.

Captures every nested collection under only the reviewed roots. No partner API.
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
from scripts.run_sec_repair import read, save, check_tree, check_sources
from sec_enrollment import activate
from sec_maintenance import candidate_writes, make_plan, commit_candidate, run_fallback
from sec_fallback import mapped_missing
from security_identity import digest, require


def verify_exact(cohort, before, after):
    """Compare all native descendants, allowing only approved exact payloads."""
    expected, financial_times = {}, {}
    for item in cohort['items']:
        for write in item['activation']['writes'] if item['activation'] else []:
            expected[write['path']] = write['data']
        for plan in item['repairs']:
            audit_path = plan['write_templates'][-1]['path']
            audit = after['records'][audit_path]['data']
            require(audit['approval_plan_sha256'] == digest(plan), 'Unexpected financial approval')
            original = before['records'].get(plan['path'], {'exists': False, 'data': None, 'update_time': None})
            require(original['update_time'] == plan['tree'][plan['path']]['update_time']
                    and digest(original['data']) == digest(plan['tree'][plan['path']]['data']),
                    'Preflight financial snapshot differs from approval')
            writes = candidate_writes(make_plan(plan['path'], plan['tree'][plan['path']]['data'], plan['candidate']),
                                      plan['route'], plan['tree'][plan['path']]['update_time'], audit['recorded_at'],
                                      approval_sha256=digest(plan))
            expected.update({w['path']: w['data'] for w in writes})
            require(after['records'][plan['path']]['update_time'] == after['records'][audit_path]['update_time'],
                    'Financial record and audit were not committed atomically')
            financial_times[plan['path']] = after['records'][audit_path]['update_time']
    require(set(after['records']) == set(before['records']) | set(expected), 'Unexpected added/deleted document')
    for path, record in after['records'].items():
        if path in expected:
            require(record['exists'] and digest(record['data']) == digest(expected[path]), 'Unexpected applied payload: ' + path)
        else:
            require(digest(record) == digest(before['records'][path]), 'Protected observation changed: ' + path)
    new_collections = set()
    for item in cohort['items']:
        root = item['binding']['root_path']
        for path in expected:
            if path.startswith(root + '/'):
                parts = path.split('/')
                new_collections.update('/'.join(parts[:i]) for i in range(len(root.split('/')) + 1, len(parts), 2))
    require(set(after['collections']) == set(before['collections']) | new_collections, 'Unexpected collection change')
    return {'exact_payloads': True, 'approved_paths': sorted(expected), 'payload_count': len(expected),
            'financial_server_commit_times': financial_times,
            'protected_document_count': len(set(before['records']) - set(expected))}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort', type=Path, required=True)
    p.add_argument('--approved-cohort-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--before', type=Path)
    args = p.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(ROOT), 'New private output required')
    cohort = read(args.cohort)
    require(digest(cohort) == args.approved_cohort_sha256 and not cohort['blocked_symbols'], 'Wrong/unresolved approved cohort')
    require(1 <= len(cohort['items']) <= 20, 'Verification cohort exceeds bound')
    token = subprocess.run(['gcloud.cmd', 'auth', 'print-access-token', '--account=jmaietta@ceorater.com'],
                           check=True, capture_output=True, text=True).stdout.strip()
    from google.cloud import firestore
    from google.oauth2.credentials import Credentials
    db = firestore.Client(project='yfinance-cli', database='(default)', credentials=Credentials(token))
    api, blocked = db._firestore_api, []
    real_commit = api.commit
    def read_only_commit(request=None, **kwargs):
        if not isinstance(request, dict) or request.get('writes'):
            blocked.append(True)
            raise AssertionError('Read-only verification forbids database writes')
        return real_commit(request=request, **kwargs)
    roots = [path for item in cohort['items'] for path in (item['binding']['root_path'], item['binding']['control_path'])]
    with patch.object(api, 'commit', side_effect=read_only_commit):
        tree = capture(db, roots=roots, max_documents=8000, max_depth=8)
        result = {'project': 'yfinance-cli', 'database': '(default)', 'cohort_sha256': digest(cohort),
                  'captured_at': datetime.now(timezone.utc).isoformat(), 'tree': tree}
        if args.before:
            before = read(args.before)
            require(before['cohort_sha256'] == digest(cohort), 'Wrong before capture')
            result['verification'] = verify_exact(cohort, before['tree'], tree)
            reruns, remaining = [], {}
            for item in cohort['items']:
                if item['activation']:
                    require(activate(db, item['activation'], apply=True) == 'noop', 'Enrollment replay wrote')
                for plan in item['repairs']:
                    outcome = commit_candidate(db, item['symbol'], plan['candidate'], apply=True, approval=plan)
                    require(outcome['action'] == 'noop', 'Repair replay is not a no-op')
                    remaining[plan['path']] = mapped_missing(tree['records'][plan['path']]['data'], item['binding'], plan['candidate']['period_end'])
                # Observe mode exercises the actual worker without publishing a
                # batch checkpoint or provider-revision audit outside the repair.
                rows, failures = run_fallback([item['symbol']], db=db, mode='observe')
                require(not failures, 'Maintenance observation failed: ' + str(failures))
                reruns.extend(rows)
            result.update(maintenance_observe=reruns, remaining_mapping_gaps=remaining,
                          replay_nonempty_commits=0, verification_mode='read-only; no scheduler execution')
        else:
            checks = []
            for item in cohort['items']:
                if item['activation']:
                    require(activate(db, item['activation']) == 'planned', 'Control already exists; use recovery verification')
                for plan in item['repairs']:
                    check_tree(db, plan)
                    checks.append({'plan_sha256': digest(plan), 'sources': check_sources(plan)})
            result['preflight'] = checks
        require(not blocked, 'A swallowed database write was attempted')
    save(args.output, result)
    print({'receipt_sha256': digest(result), 'documents': len(tree['records']),
           'verification': result.get('verification'), 'preflight_plans': len(result.get('preflight', [])),
           'remaining_mapping_gaps': result.get('remaining_mapping_gaps')})


if __name__ == '__main__':
    main()
