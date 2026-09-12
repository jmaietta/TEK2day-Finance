"""Pure preparation from saved captures; never connects to a database."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_ticker_migration import restore
from security_identity import digest, portable, require
from succession_migration import build_plan, public_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('capture', 'identity-capture', 'private-plan', 'manifest'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    require(not args.private_plan.resolve().is_relative_to(Path(__file__).resolve().parents[1]), 'Private plan must stay outside Git')
    require(not args.private_plan.exists() and not args.manifest.exists(), 'Do not overwrite evidence')
    captured = restore(json.loads(args.capture.read_text(encoding='utf-8')))
    targets = restore(json.loads(args.identity_capture.read_text(encoding='utf-8')))
    require(targets['project'] == captured['project'] and targets['database'] == captured['database'], 'Capture databases differ')
    require(not targets['route_before']['exists'], 'Existing route requires separate review')
    captured['records'].update(targets['records'])
    captured['collections'] = sorted(set(captured['collections'] + targets['collections']))
    plan = build_plan(captured, review_basis='September 12, 2026: current XOM common listing independently bound by reviewed successor 8-K12B, NYSE CUSIPs, dual-registrant June 10-Q and bounded Firestore metadata/tree inspection. Historical provider observations retained without assigning missing source CIKs.')
    with args.private_plan.open('x', encoding='utf-8') as handle:
        json.dump(portable(plan), handle, sort_keys=True, indent=2)
    with args.manifest.open('x', encoding='utf-8') as handle:
        json.dump(portable(public_manifest(plan)), handle, sort_keys=True, indent=2)
        handle.write('\n')
    print(json.dumps({'plan_sha256': digest(plan), 'publish_writes': len(plan['writes']),
                      'source_documents': len(plan['source_records']), 'history_writes': plan['history_writes'],
                      'conflicts': plan['conflicts']}))


if __name__ == '__main__':
    main()
