"""Capture a bounded public SEC evidence fixture; no database or partner access."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sec_fallback import SecClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new evidence path')
    client, result = SecClient(), {}
    for cik in ('0000034088', '0002115436'):
        body, receipt = client.get(f'submissions/CIK{cik}.json')
        recent = body['filings']['recent']
        indices = [i for i, form in enumerate(recent['form'])
                   if form in ('10-Q', '10-K', '10-Q/A', '10-K/A', '8-K12B', '25-NSE')
                   and recent['filingDate'][i] >= '2026-01-01']
        result[cik] = {k: body[k] for k in ('cik', 'name', 'tickers', 'exchanges')}
        result[cik].update(receipt=receipt, subset_scope='Selected 2026 reports; original body digest in receipt',
                           filings={'recent': {k: [v[i] for i in indices] for k, v in recent.items()},
                                    'files': body['filings'].get('files', [])})
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
    print(json.dumps({cik: {'receipt': v['receipt'], 'recent': v['filings']['recent']}
                      for cik, v in result.items()}, indent=2))


if __name__ == '__main__':
    main()
