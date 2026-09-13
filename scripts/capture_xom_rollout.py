"""Read-only deployed commit/image and writer-drain verification (XOM default)."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.capture_identity_rollout import read
from security_identity import require, digest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--commit', default='e9e28afd0536c5d8bc1ee552dee6449466fdbb11',
                        help='Expected source commit; can verify subsequent SEC repair guard builds')
    args = parser.parse_args()
    require(not args.output.exists(), 'Use a new receipt path')
    sha = args.commit
    import re
    require(re.fullmatch(r'[0-9a-f]{40}', sha) is not None, 'Expected full source commit required')
    registry = 'us-central1-docker.pkg.dev/yfinance-cli/tek2day/'
    receipt = {'checked_at': datetime.now(timezone.utc).isoformat(), 'commit': sha,
               'project': 'yfinance-cli', 'region': 'us-central1', 'images': {}, 'jobs': {}, 'executions': {}}
    service = read(['run', 'services', 'describe', 'tek2day-api', '--region=us-central1'],
                   'status.traffic,status.latestReadyRevisionName,spec.template.spec.containers[0].image')
    receipt['api'] = service
    revision = service['status']['latestReadyRevisionName']
    require(service['spec']['template']['spec']['containers'][0]['image'] == registry + 'api:' + sha,
            'Serving service template is not the reviewed commit')
    traffic = service['status']['traffic']
    require(sum(t.get('percent', 0) for t in traffic if t.get('revisionName') == revision) == 100,
            'Old API revision still receives traffic')
    receipt['api_revision'] = read(['run', 'revisions', 'describe', revision, '--region=us-central1'], 'status.imageDigest')
    for image in ('api', 'daily-prices', 'weekly-estimates', 'quarterly-financials'):
        approved = read(['artifacts', 'docker', 'images', 'describe', registry + image + ':' + sha], 'image_summary.digest')
        latest = read(['artifacts', 'docker', 'images', 'describe', registry + image + ':latest'], 'image_summary.digest')
        require(approved == latest, 'Latest image tag does not match the reviewed commit')
        receipt['images'][image] = approved['image_summary']['digest']
    require(receipt['api_revision']['status']['imageDigest'].endswith('@' + receipt['images']['api']), 'Serving API digest mismatch')
    for name, image in (('daily-price-pull', 'daily-prices'), ('weekly-estimate-pull', 'weekly-estimates'),
                        ('quarterly-financial-pull', 'quarterly-financials')):
        receipt['jobs'][name] = read(['run', 'jobs', 'describe', name, '--region=us-central1'],
                                     'spec.template.spec.template.spec.containers[0].image')
        actual = receipt['jobs'][name]['spec']['template']['spec']['template']['spec']['containers'][0]['image']
        require(actual == registry + image + ':latest', 'Maintenance job does not use the reviewed latest image')
    for name in ('daily-price-pull', 'weekly-estimate-pull', 'quarterly-financial-pull', 'full-data-pull-v2'):
        executions = read(['run', 'jobs', 'executions', 'list', '--job=' + name, '--region=us-central1',
                           '--limit=1000'],
                          'metadata.name,status.startTime,status.completionTime,status.runningCount,status.conditions')
        require(len(executions) < 1000, 'Execution listing reached its bound; drain is not established')
        unfinished = [e for e in executions if not e.get('status', {}).get('completionTime')]
        receipt['executions'][name] = {'inspected_count': len(executions), 'unfinished': unfinished,
                                       'listing_sha256': digest(executions), 'latest': executions[:3]}
        require(not unfinished, 'Existing execution has not completed; wait for writer drain')
    # Return only the one nonsecret flag, never all runtime environment values.
    job = read(['run', 'jobs', 'describe', 'quarterly-financial-pull', '--region=us-central1'],
               'spec.template.spec.template.spec.containers[0].env')
    env = job['spec']['template']['spec']['template']['spec']['containers'][0]['env']
    receipt['sec_fallback_mode'] = next((e.get('value') for e in env if e['name'] == 'SEC_FALLBACK_MODE'), 'off')
    require(receipt['sec_fallback_mode'] == 'observe', 'SEC fallback mode unexpectedly changed')
    receipt['writers_drained'] = True
    receipt['completed_at'] = datetime.now(timezone.utc).isoformat()
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(receipt, handle, sort_keys=True, indent=2)
    print(json.dumps({'api_revision': revision, 'images': receipt['images'], 'writers_drained': True,
                      'sec_fallback_mode': receipt['sec_fallback_mode']}, indent=2))


if __name__ == '__main__':
    main()
