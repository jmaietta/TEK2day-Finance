"""Read-only first-party statement/metadata/history checks after cohort repair."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_sec_repair import read, save
from security_identity import digest, require


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort', type=Path, required=True)
    p.add_argument('--native', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    require(not args.output.exists() and not args.output.resolve().is_relative_to(ROOT), 'New private receipt required')
    cohort, native = read(args.cohort), read(args.native)
    require(native['cohort_sha256'] == digest(cohort) and native['verification']['exact_payloads'], 'Verified native execution required')
    require(all((datetime.now(timezone.utc) - t).total_seconds() > 300 for t in
                native['verification']['financial_server_commit_times'].values()), 'Wait for the financial cache TTL')
    import requests
    import app
    import terminal
    session = requests.Session()
    result = {'cohort_sha256': digest(cohort), 'checks': [], 'responses': {}, 'quotes': {},
              'started_at': datetime.now(timezone.utc).isoformat(), 'partner_api_called': False}
    base = 'https://finance.tek2dayholdings.com'
    sections = {'inc': ('income', terminal.INCOME_FIELDS, 'Income Statement'),
                'bal': ('balance_sheet', terminal.BALANCE_FIELDS, 'Balance Sheet'),
                'cf': ('cash_flow', terminal.CASHFLOW_FIELDS, 'Cash Flow')}
    def fetch(key, path, command=None):
        require(path == '/api/command' or any(path == prefix + item['symbol'] for item in cohort['items']
                for prefix in ('/api/ticker/', '/api/estimates/', '/api/prices/')), 'First-party path only')
        response = (session.post(base + path, json={'command': command, 'width': 120}, timeout=60) if command else
                    session.get(base + path, params={'limit': 2000} if path.startswith('/api/prices/') else None, timeout=60))
        require(response.status_code == 200, 'First-party request failed: ' + key)
        body = response.json()
        result['responses'][key] = {'status': response.status_code, 'body': body, 'sha256': digest(body),
                                    'retrieved_at': datetime.now(timezone.utc).isoformat()}
        return body
    try:
        for item in cohort['items']:
            symbol, root = item['symbol'], item['binding']['root_path']
            records = native['tree']['records']
            def dataset(section):
                return [r['data'] for path, r in records.items() if r['exists'] and
                        path.startswith(root + '/' + section + '/') and len(path.split('/')) == len(root.split('/')) + 2]
            financials = dataset('financials')
            with patch.object(terminal, '_all_financials', return_value=financials):
                expected = {kind: app._financial_payload(symbol, *spec) for kind, spec in sections.items()}
            for kind in sections:
                body = fetch(symbol + ':' + kind, '/api/command', '/' + symbol + ' ' + kind)
                require(body.get('symbol') == symbol and body.get('kind') == kind and digest(body.get('data')) == digest(expected[kind]),
                        'Statement differs from verified native data: ' + kind)
                require('SEC EDGAR' in body['data']['source'], 'Missing SEC attribution')
                result['checks'].append(symbol + ':' + kind + ':exact_financials')
            meta = fetch(symbol + ':metadata', '/api/ticker/' + symbol)
            require(digest(meta) == digest(records[root]['data']), 'Metadata changed')
            result['checks'].append(symbol + ':metadata:unchanged')
            estimates = fetch(symbol + ':estimates', '/api/estimates/' + symbol)
            require(sorted(map(digest, estimates)) == sorted(map(digest, dataset('estimates'))), 'Estimate observations changed')
            result['checks'].append(symbol + ':estimates:observations_and_horizons_retained')
            prices = fetch(symbol + ':prices', '/api/prices/' + symbol)
            stored = sorted(dataset('prices'), key=lambda d: d['date'])
            served = {d['time']: d for d in prices}
            for row in stored[:-1]:
                require(row['date'] in served and all(served[row['date']].get(k) == row.get(k)
                        for k in ('open', 'high', 'low', 'close', 'volume')), 'Historical chart data changed')
            result['checks'].append(symbol + ':prices:stored_history_retained')
            quote = terminal._live_quote(symbol)
            require(quote.get('price') is not None and quote.get('observed_at') and quote.get('currency') == item['binding']['currency'],
                    'Current quote observation missing')
            require(quote['date'] in served and abs(served[quote['date']]['close'] - quote['price']) < 0.001, 'Quote/chart mismatch')
            result['quotes'][symbol] = {**quote, 'retrieved_at': datetime.now(timezone.utc).isoformat()}
            result['checks'].append(symbol + ':quote:price_and_observation_time_verified')
            repeat = fetch(symbol + ':repeat', '/api/command', '/' + symbol + ' inc')
            require(repeat['data'] == expected['inc'], 'Repeated financial response changed')
            result['checks'].append(symbol + ':financial_repeat:stable')
        result['completed_at'] = datetime.now(timezone.utc).isoformat()
    finally:
        save(args.output, result)
    print({'checks': result['checks'], 'completed_at': result['completed_at'], 'receipt_sha256': digest(result)})


if __name__ == '__main__':
    main()
