"""Bounded, source-preserving SEC lookup for explicitly reviewed successions."""
from copy import deepcopy
from datetime import date
import re
import time

from registrant_succession import succession_for, validate_succession
from sec_fallback import SecClient
from security_identity import digest, require

_CACHE = {}
TTL, STALE_LIMIT = 3600, 86400


def assemble(event, submissions):
    validate_succession(event)
    ciks = {event[s]['cik'] for s in ('from', 'to')}
    require(set(submissions) == ciks, 'Both reviewed registrant responses required')
    gathered = {}
    overlap = event['financial_continuity']['overlap']
    scope = []
    for side in ('from', 'to'):
        cik = event[side]['cik']
        body = submissions[cik]
        require(str(body.get('cik')).zfill(10) == cik, 'SEC response registrant mismatch')
        symbols, exchanges = body.get('tickers'), body.get('exchanges')
        if side == 'to':
            require(symbols == [event['symbol']] and exchanges == ['NYSE'], 'Successor listing changed or ambiguous')
        else:
            # Empty predecessor listing is expected; a newly reused listing is not.
            require(symbols == [] and exchanges == [], 'Predecessor listing changed; review required')
        recent = body.get('filings', {}).get('recent', {})
        keys = ('accessionNumber', 'filingDate', 'reportDate', 'form', 'primaryDocument')
        require(all(isinstance(recent.get(k), list) for k in keys) and
                len({len(recent[k]) for k in keys}) == 1 and len(recent['form']) <= 5000,
                'Malformed/oversized SEC submissions')
        if body.get('filings', {}).get('files'):
            scope.append(cik + ': recent submissions only; older archives not scanned')
        for i, form in enumerate(recent['form']):
            accn, filed, end, _, primary = (recent[k][i] for k in keys)
            date.fromisoformat(filed)
            if side == 'from' and filed >= event['effective_on']:
                if form not in {'10-Q', '10-K', '10-Q/A', '10-K/A'} or not end or end > overlap['period_end']:
                    continue  # continuing predecessor debt/entity filings are not new common-stock filings
            require(re.fullmatch(r'\d{10}-\d{2}-\d{6}', accn) is not None and
                    re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', primary) is not None
                    and all(p not in {'.', '..'} for p in primary.split('/')), 'Invalid SEC source path')
            source_cik = cik
            if accn == overlap['accession']:
                require((form, end, primary, filed) == (overlap['form'], overlap['period_end'],
                        overlap['primary_document'], overlap['filed_on']), 'Reviewed joint filing changed')
                source_cik = overlap['source_cik']
            row = {'date': filed, 'form': form, 'report_date': end, 'accession': accn,
                   'primary_document': primary, 'source_cik': source_cik,
                   'url': f'https://www.sec.gov/Archives/edgar/data/{int(source_cik)}/{accn.replace("-", "")}/{primary}'}
            if accn in gathered:
                previous = gathered[accn]
                require(previous['filing'] == row, 'Conflicting SEC accession observations; do not collapse')
                previous['submission_ciks'].append(cik)
            else:
                descs = recent.get('primaryDocDescription', [])
                gathered[accn] = {'filing': row, 'submission_ciks': [cik],
                                  'description': descs[i] if i < len(descs) else form}
    result = [{**r['filing'], 'description': r['description'],
               'submission_ciks': sorted(r['submission_ciks'])} for r in gathered.values()]
    result.sort(key=lambda r: (r['date'], r['accession']), reverse=True)
    return {'type': 'filings', 'symbol': event['symbol'], 'title': 'Recent SEC Filings',
            'source': 'SEC EDGAR', 'filings': result[:15], 'scope': scope,
            'note': 'Reviewed predecessor and successor registrants; shared accession displayed once. Filing list is not a financial completeness check.'}


def filings_payload(symbol, *, client=None, now=None):
    event = succession_for(symbol)
    require(event is not None, 'Unreviewed succession')
    require(date.today().isoformat() > event['effective_on'], 'Succession not unambiguously effective')
    now = time.time() if now is None else now
    key = (symbol, digest(event))
    cached = _CACHE.get(key)
    if cached and now - cached[0] < TTL:
        return deepcopy(cached[1])
    try:
        client = client or SecClient()
        bodies, receipts = {}, {}
        for side in ('from', 'to'):
            cik = event[side]['cik']
            bodies[cik], receipts[cik] = client.get(f'submissions/CIK{cik}.json')
        result = assemble(event, bodies)
        result['source_captures'] = receipts
        _CACHE[key] = (now, deepcopy(result))
        return result
    except Exception:
        if cached and 0 <= now - cached[0] <= STALE_LIMIT:
            result = deepcopy(cached[1])
            result['stale'] = True
            result['note'] += ' SEC refresh failed; showing the previously captured filing list.'
            return result
        raise
