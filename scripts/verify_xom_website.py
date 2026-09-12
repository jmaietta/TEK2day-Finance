"""Bounded first-party XOM checks; raw responses remain outside Git."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from security_identity import require, portable


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--before', type=Path)
    args = parser.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(Path(__file__).resolve().parents[1]),
            'Use a new private output path')
    before = json.loads(args.before.read_text(encoding='utf-8')) if args.before else None
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'responses': {}, 'checks': []}
    session = requests.Session()
    base = 'https://finance.tek2dayholdings.com'
    def fetch(key, path, command=None):
        require(path in {'/api/ticker/XOM', '/api/prices/XOM', '/api/estimates/XOM', '/api/command'}, 'Not an approved first-party path')
        response = (session.post(base + path, json={'command': command, 'width': 120}, timeout=60)
                    if command else session.get(base + path, params={'limit': 2000} if '/prices/' in path else None, timeout=60))
        body = response.json()
        result['responses'][key] = {'status': response.status_code, 'body': body,
                                    'sha256': hashlib.sha256(response.content).hexdigest(),
                                    'retrieved_at': datetime.now(timezone.utc).isoformat()}
        require(response.status_code == 200, f'{key}: HTTP {response.status_code}')
        return body
    try:
        meta = fetch('metadata', '/api/ticker/XOM')
        require(meta['symbol'] == 'XOM', 'Wrong metadata ticker')
        require(str(meta['cik']).zfill(10) == ('0002115436' if before else '0000034088'), 'Wrong metadata stage')
        result['checks'].append('metadata_current_stage')
        for kind in ('inc', 'bal', 'cf', 'filings', 'summary'):
            body = fetch(kind, '/api/command', '/XOM' + ('' if kind == 'summary' else ' ' + kind))
            require(body.get('symbol') == 'XOM' and body.get('data'), kind + ': missing XOM data')
            if before and kind in ('inc', 'bal', 'cf'):
                require(body['data'] == before['responses'][kind]['body']['data'], 'Financial view changed: ' + kind)
            if kind == 'filings':
                data = body['data']
                matches = [f for f in data['filings'] if f['accession'] == '0000034088-26-000093']
                require(len(matches) == 1 and matches[0]['submission_ciks'] == ['0000034088', '0002115436'], 'Joint filing duplicated or unbound')
                require(any(f['source_cik'] == '0002115436' for f in data['filings']), 'Successor filings absent')
                require('/data/34088/' in matches[0]['url'], 'Joint report archive URL changed')
                require(not data.get('stale'), 'Filing refresh served stale cache')
            result['checks'].append(kind + ':pass')
            print(result['checks'][-1], flush=True)
        estimates = fetch('estimates', '/api/estimates/XOM')
        require(len(estimates) == 14, 'Estimate history disconnected')
        if before:
            require(estimates == before['responses']['estimates']['body'], 'Estimate observations changed')
        prices = fetch('prices', '/api/prices/XOM')
        require(len(prices) >= 1328 and prices[0]['time'] == '2021-05-27', 'Price history disconnected')
        if before:
            # Last daily bar may include a fresh live quote; native verification
            # compares every stored row and timestamp separately.
            cutoff = '2026-09-11'
            require([r for r in prices if r['time'] < cutoff] ==
                    [r for r in before['responses']['prices']['body'] if r['time'] < cutoff], 'Older chart history changed')
        result['checks'].extend(['estimates:pass', 'prices:pass'])
        if before:
            repeat = fetch('filings_repeat', '/api/command', '/XOM filings')
            require(repeat['data']['filings'] == result['responses']['filings']['body']['data']['filings'], 'Repeated filing reads differ')
            result['checks'].append('filings_repeat:pass')
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
    finally:
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(portable(result), handle, indent=2, sort_keys=True, allow_nan=False)
    print(json.dumps({'checks': result['checks'], 'completed_at': result['completed_at'],
                      'scope': 'First-party website only; current Kilby live partner acceptance remains separate'}))


if __name__ == '__main__':
    main()
