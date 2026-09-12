"""Explicit registrant succession, independent of the simple-rename registry.

An issuer, a security, and a consolidated reporting series are distinct. This
review does not turn CIK matching into an enrollment or history-joining rule.
XOM keeps its existing storage tree. Only a separately approved publication
attaches the current identity; legacy observations are never relabelled.
"""
from copy import deepcopy
from datetime import date
import re

from security_identity import digest, require

XOM_SUCCESSION = {
    'event_id': 'xom-common-succession-2026-07-01',
    'event_type': 'registrant_succession',
    'transaction_type': 'redomiciliation_holding_company_merger',
    'symbol': 'XOM', 'effective_on': '2026-07-01', 'effective_at': None,
    'date_precision': 'day', 'exchange_ratio': {'old': 1, 'new': 1},
    'series_id': 'series-729a487e-3130-48a7-a446-73835a2d7ab7',
    'from': {
        'issuer_id': 'issuer-47242a1f-d4bf-48f6-a3e9-5fa758b92e1d',
        'security_id': 'security-6e3f3ca5-e4e2-42b6-8e39-c4fbe7bcb50a',
        'name': 'Exxon Mobil Corporation', 'jurisdiction': 'New Jersey',
        'cik': '0000034088', 'symbol': 'XOM', 'cusip': '30231G102',
        'share_class': 'common_stock_no_par', 'instrument_type': 'common_stock',
        'exchange_mic': 'XNYS', 'currency': 'USD', 'adr_ratio': None,
    },
    'to': {
        'issuer_id': 'issuer-ec8207be-6bfc-4c90-90a9-c8294cb63ee2',
        'security_id': 'security-9d0ba47f-20be-4c92-ae82-ce6080a1d329',
        'name': 'ExxonMobil Holdings Corporation', 'jurisdiction': 'Texas',
        'cik': '0002115436', 'symbol': 'XOM', 'cusip': '30233Q108',
        'share_class': 'common_stock_0.001_par', 'instrument_type': 'common_stock',
        'exchange_mic': 'XNYS', 'currency': 'USD', 'adr_ratio': None,
    },
    'sources': [{
        'document': 'Successor 8-K12B, Explanatory Note and Items 1.01/2.01',
        'url': 'https://www.sec.gov/Archives/edgar/data/2115436/000119312526291990/d71068d8k12b.htm',
        'published_on': '2026-07-01', 'accepted_at': '2026-07-01T16:36:49.000Z',
        'passage': 'The Redomiciliation Merger became effective on July 1, 2026',
        'findings': 'Successor common-stock registration; one-for-one exchange; old company remains primary note obligor. July 2 trading was expected, not independently observed.',
    }, {
        'document': 'NYSE Form 25 removal notice',
        'url': 'https://www.sec.gov/Archives/edgar/data/34088/000087666126000593/ruleprovisionnotice.htm',
        'published_on': '2026-07-02', 'accepted_at': '2026-07-02T14:30:54.000Z',
        'passage': 'OLD XOM, CUSIP: 30231G102',
        'findings': 'New common-stock CUSIP 30233Q108. July 13 removal applies only to old common class.',
    }, {
        'document': 'June 2026 10-Q, Explanatory Note and Note 1',
        'url': 'https://investor.exxonmobil.com/sec-filings/all-sec-filings/content/0000034088-26-000093/xom-20260630.htm',
        'published_on': '2026-08-03', 'accepted_at': '2026-08-03T18:55:38.000Z',
        'passage': "The Redomiciliation Merger did not change the Corporation's consolidated business, operations, assets, liabilities, or financial reporting basis.",
        'findings': 'Group headed by old registrant through June 30 and new thereafter. June report separately filed by both; same accession appears in both submissions.',
    }],
    'financial_continuity': {
        'basis': 'same_consolidated_group_us_gaap',
        'predecessor_period_end_through': '2026-06-30',
        'successor_period_end_from': '2026-07-01',
        'overlap': {'form': '10-Q', 'period_end': '2026-06-30',
                    'accession': '0000034088-26-000093',
                    'primary_document': 'xom-20260630.htm',
                    'source_cik': '0000034088', 'filed_on': '2026-08-03'},
    },
    'currency_evidence': 'USD observed in bounded XOM Firestore metadata September 12, 2026; not asserted by the merger announcement.',
    'price_continuity': 'Existing provider series retained without assigning all historical prices to the successor; actual first-trade instant unverified.',
    'estimates_continuity': 'Existing observations retained; target horizons and post-results revisions require separate verification.',
}


def validate_succession(event):
    require(event.get('event_type') == 'registrant_succession' and
            event.get('transaction_type') == 'redomiciliation_holding_company_merger',
            'Unreviewed succession type; not a general merger, spinoff or delisting handler')
    require(event.get('sources') and event.get('series_id'), 'Missing reviewed succession evidence')
    a, b = event['from'], event['to']
    require(a['symbol'] == b['symbol'] == event['symbol'], 'Changed or reused ticker needs separate review')
    for key in ('issuer_id', 'security_id', 'cik', 'cusip'):
        require(a.get(key) and b.get(key) and a[key] != b[key], 'Distinct successor identities required')
    for side in (a, b):
        require(re.fullmatch(r'\d{10}', side['cik']) is not None and
                side['instrument_type'] == 'common_stock' and side['adr_ratio'] is None,
                'Invalid registrant or unsupported ADR/share class')
        require(side['share_class'].startswith('common_stock_') and
                side['exchange_mic'] == 'XNYS' and side['currency'] == 'USD',
                'Unreviewed security/listing basis')
    require(event['exchange_ratio'] == {'old': 1, 'new': 1}, 'Non-unit conversion requires separate basis review')
    require(event.get('date_precision') == 'day' and event.get('effective_at') is None,
            'Do not invent a precise effective timestamp')
    cutoff = event['financial_continuity']
    require(cutoff['basis'] == 'same_consolidated_group_us_gaap' and
            date.fromisoformat(cutoff['predecessor_period_end_through']) < date.fromisoformat(event['effective_on'])
            == date.fromisoformat(cutoff['successor_period_end_from']), 'Invalid reporting continuity boundaries')
    return event


REVIEWED_SUCCESSIONS = (XOM_SUCCESSION,)


def succession_for(symbol, events=None):
    events = REVIEWED_SUCCESSIONS if events is None else events
    found = [validate_succession(e) for e in events if e.get('symbol') == symbol]
    require(len(found) <= 1, 'Ambiguous successor chain or reused ticker')
    return deepcopy(found[0]) if found else None


def identity_documents(event):
    """Immutable entity/relationship records, separate from ticker storage keys."""
    validate_succession(event)
    out = {}
    for side in (event['from'], event['to']):
        out['issuers/' + side['issuer_id']] = {k: side[k] for k in ('issuer_id', 'name', 'jurisdiction', 'cik')}
        out['securities/' + side['security_id']] = deepcopy(side)
    out['registrant_relationships/' + event['event_id']] = deepcopy(event)
    out['reporting_series/' + event['series_id']] = {
        'series_id': event['series_id'], 'event_id': event['event_id'],
        'issuer_ids': [event[s]['issuer_id'] for s in ('from', 'to')],
        'financial_continuity': deepcopy(event['financial_continuity']),
        'historical_source_identity': 'Retain supplied original CIK/accession; missing provenance stays unknown',
    }
    return out


def financial_registrant(event, period_end):
    date.fromisoformat(period_end)
    return event['from'] if period_end <= event['financial_continuity']['predecessor_period_end_through'] else event['to']


def checked_period_observation(event, data):
    """Validate supplied identity without inventing CIKs for Yahoo observations."""
    side = financial_registrant(event, data['period_end'])
    for k in ('cik', 'issuer_id', 'security_id'):
        if data.get(k) is not None:
            held = str(data[k]).zfill(10) if k == 'cik' else data[k]
            require(held == side[k], 'Financial source identity crosses the reviewed reporting boundary')
    require(data.get('currency', 'USD') == 'USD', 'Financial currency changed')


def select_reporting_observations(event, observations):
    """One coherent selected observation per exact period; never fieldwise union.

    Used by offline SEC adapters before enrollment. Full source observations,
    including restatements and complementary candidates, remain in the result.
    A duplicate accession alone does not override differing financial values.
    """
    validate_succession(event)
    require(len(observations) <= 64, 'Reporting observation bound exceeded')
    groups = {}
    for obs in observations:
        require(obs.get('series_id') == event['series_id'] and
                obs.get('basis') == event['financial_continuity']['basis'] and
                obs.get('currency') == 'USD', 'Unreviewed financial series/basis')
        require(all(obs.get(k) for k in ('cik', 'issuer_id', 'security_id')),
                'Missing original source identity; do not join by period alone')
        start, end, freq = obs['period_start'], obs['period_end'], obs['freq']
        require(date.fromisoformat(start) <= date.fromisoformat(end) and freq in {'Q', 'FY'}, 'Invalid reporting period')
        duration = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        low, high = (70, 105) if freq == 'Q' else (330, 400)
        require(low <= duration <= high, 'Transitional or cumulative duration needs separate review')
        require(obs.get('accession') and obs.get('source_url') and obs.get('form') in {'10-Q', '10-K', '10-Q/A', '10-K/A'},
                'Missing filing provenance')
        require(obs['form'].replace('/A', '') == ('10-Q' if freq == 'Q' else '10-K'), 'Frequency/form mismatch')
        overlap = event['financial_continuity']['overlap']
        if (obs['accession'], end, obs['form']) == (overlap['accession'], overlap['period_end'], overlap['form']):
            # Both registrants file this reviewed report. Filing registrant is
            # distinct from the issuer that headed the reported consolidated group.
            sides = [event[s] for s in ('from', 'to') if event[s]['cik'] == obs.get('cik')]
            require(len(sides) == 1 and all(obs.get(k) == sides[0][k] for k in ('issuer_id', 'security_id')),
                    'Unreviewed joint filing source identity')
            canonical_url = (f"https://www.sec.gov/Archives/edgar/data/{int(overlap['source_cik'])}/"
                             + overlap['accession'].replace('-', '') + '/' + overlap['primary_document'])
            require(obs['source_url'] in {canonical_url, event['sources'][2]['url']}, 'Unreviewed joint source document')
        else:
            checked_period_observation(event, obs)
        groups.setdefault((start, end, freq), []).append(deepcopy(obs))
    selected, review = [], []
    for period, rows in sorted(groups.items()):
        # Exact duplicate source observations can differ only in capture metadata.
        overlap = event['financial_continuity']['overlap']
        joint = all((r['accession'], r['period_end'], r['form']) ==
                    (overlap['accession'], overlap['period_end'], overlap['form']) for r in rows)
        ignored = {'captured_at', 'submission_ciks'}
        if joint:
            ignored |= {'cik', 'issuer_id', 'security_id', 'source_url'}
        signatures = {digest({k: v for k, v in r.items() if k not in ignored}) for r in rows}
        if len(signatures) == 1 and not rows[0]['form'].endswith('/A'):
            chosen = sorted(rows, key=lambda r: (r['cik'] != event['from']['cik'], digest(r)))[0]
            selected.append({**chosen, 'reporting_issuer_id': financial_registrant(event, chosen['period_end'])['issuer_id']})
        else:
            review.append({'period': period, 'reason': 'conflicting_or_restatement_observations', 'observations': rows})
    # Overlapping durations for one end/frequency cannot both enter TTM.
    collisions = {(r['period_end'], r['freq']) for r in selected
                  if sum((q['period_end'], q['freq']) == (r['period_end'], r['freq']) for q in selected) > 1}
    for key in sorted(collisions):
        review.append({'period_end': key[0], 'freq': key[1], 'reason': 'ambiguous_reporting_duration'})
    selected = [r for r in selected if (r['period_end'], r['freq']) not in collisions]
    return {'selected': selected, 'review': review, 'observations': deepcopy(observations)}
