"""Bounded first-party website checks; never calls the partner API.

Commands here only read finance data. Raw response evidence stays outside Git.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from security_identity import BNY_EVENT, portable, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() or args.output.resolve().is_relative_to(ROOT):
        parser.error('Use a new private output path outside the repository')
    before = json.loads(args.before.read_text(encoding='utf-8'))
    expected_income = deepcopy(before['responses']['BK']['body']['data'])
    expected_income['symbol'] = 'BNY'
    session = requests.Session()
    base = 'https://finance.tek2dayholdings.com'
    result = {'started_at': datetime.now(timezone.utc).isoformat(), 'responses': {}, 'checks': []}
    def request(key, path, command=None):
        require(path in {'/api/command', '/api/ticker/BK', '/api/ticker/BNY', '/api/prices/BK', '/api/prices/BNY'}, 'non-first-party path')
        response = (session.post(base + path, json={'command': command, 'width': 120}, timeout=60) if command else
                    session.get(base + path, params={'limit': 2000} if '/prices/' in path else None, timeout=60))
        body = response.json()
        result['responses'][key] = {'status': response.status_code, 'body': body,
                                    'sha256': hashlib.sha256(response.content).hexdigest(),
                                    'retrieved_at': datetime.now(timezone.utc).isoformat()}
        require(response.status_code == 200, f'{key} HTTP {response.status_code}')
        return body
    try:
        for symbol in ('BK', 'BNY'):
            for kind in ('inc', 'bal', 'cf', 'summary'):
                command = '/' + symbol + ('' if kind == 'summary' else ' ' + kind)
                body = request(symbol + ':' + kind, '/api/command', command)
                require(body.get('symbol') == 'BNY' and body.get('kind') == kind and body.get('data'), 'canonical command data missing')
                if kind == 'inc':
                    require(body['data'] == expected_income, 'income history changed')
                if kind == 'summary':
                    require(body['data']['overview']['symbol'] == 'BNY', 'summary symbol mismatch')
                    require(body['data'].get('estimates'), 'estimates not connected')
                result['checks'].append(symbol + ':' + kind + ':pass')
                print(result['checks'][-1], flush=True)
            meta = request(symbol + ':metadata', '/api/ticker/' + symbol)
            require(meta.get('symbol') == 'BNY' and meta.get('security_id') == BNY_EVENT['security_id'], 'metadata identity mismatch')
            rows = request(symbol + ':prices', '/api/prices/' + symbol)
            stored = [r for r in rows if r['time'] <= '2026-07-17']
            require(len(stored) == 1282, 'stored price history not connected')
            require(any(r['time'] > '2026-07-17' for r in rows), 'live chart point unavailable')
            result['checks'].extend([symbol + ':metadata:pass', symbol + ':prices:pass'])
        for kind in ('inc', 'bal', 'cf', 'metadata'):
            require(result['responses']['BK:' + kind]['body'] == result['responses']['BNY:' + kind]['body'],
                    'old/new first-party reads differ: ' + kind)
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
    finally:
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(portable(result), handle, sort_keys=True, indent=2, allow_nan=False)
    print(json.dumps({'checks': result['checks'], 'completed_at': result['completed_at'],
                      'scope': 'first-party website only; live Kilby acceptance remains separate'}))


if __name__ == '__main__':
    main()
