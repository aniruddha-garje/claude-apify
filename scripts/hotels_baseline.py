"""Hotels baseline: how much do two browser sessions agree, and how does the Actor compare with both.

Usage:
  python scripts/hotels_baseline.py compare
  python scripts/hotels_baseline.py list [--run-id ID]
  python scripts/hotels_baseline.py named [--run-id ID]

Reads two Playwright captures of the same 8-city golden inputs, taken at least 10 minutes apart from this computer
(reports/audit/hotels-baseline/sessionA.json and sessionB.json). `compare` scores session B against A and A against B.
`list` runs the Actor's search mode (or reads a finished run) and scores it against the union of both sessions.
`named` runs the Actor's named-hotel mode on the 158 golden hotels and scores recall and price against the browser.
Expected values come only from the browser captures and golden.json, never from Actor output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit  # noqa: E402

ROOT = audit.ROOT
BASE = ROOT / 'reports' / 'audit' / 'hotels-baseline'
GOLDEN = ROOT / 'google-hotels-scraper' / 'tests' / 'golden' / 'golden.json'


def price(card: dict) -> int | None:
    return int(re.sub(r'[^\d]', '', card['priceText'])) if card.get('priceText') else None


def session(name: str) -> dict:
    cap = json.loads((BASE / f'session{name}.json').read_text(encoding='utf-8'))
    out = {}
    for city in cap['captures']:
        for card in city['cards']:
            out[(city['city'], card['hotelId'])] = {'name': card['name'], 'price': price(card),
                                                    'capturedAt': city['capturedAt']}
    return {'capturedAt': [c['capturedAt'] for c in cap['captures']], 'hotels': out,
            'checkIn': cap['captures'][0]['checkIn'], 'checkOut': cap['captures'][0]['checkOut']}


def per_city(a: dict, b: dict) -> dict:
    cities = sorted({c for c, _ in a} | {c for c, _ in b})
    return {c: {'A': sum(1 for k in a if k[0] == c), 'B': sum(1 for k in b if k[0] == c),
                'both': sum(1 for k in a if k[0] == c and k in b)} for c in cities}


def compare() -> dict:
    a, b = session('A'), session('B')
    ha, hb = a['hotels'], b['hotels']
    both = [k for k in ha if k in hb]
    priced = [k for k in both if ha[k]['price'] and hb[k]['price']]
    same10 = [k for k in priced if abs(ha[k]['price'] - hb[k]['price']) / ha[k]['price'] <= 0.10]
    result = {
        'sessionA': {'from': min(a['capturedAt']), 'to': max(a['capturedAt']), 'hotels': len(ha)},
        'sessionB': {'from': min(b['capturedAt']), 'to': max(b['capturedAt']), 'hotels': len(hb)},
        'bRecallOfA': {'found': len(both), 'of': len(ha), 'rate': round(len(both) / len(ha), 4),
                       'ci95': audit.wilson(len(both), len(ha))},
        'aRecallOfB': {'found': len(both), 'of': len(hb), 'rate': round(len(both) / len(hb), 4),
                       'ci95': audit.wilson(len(both), len(hb))},
        'union': len(set(ha) | set(hb)),
        'priceSameWithin10': {'matched': len(same10), 'of': len(priced), 'ci95': audit.wilson(len(same10), len(priced))},
        'perCity': per_city(ha, hb),
        'onlyInA': [f'{c}/{ha[(c, h)]["name"]}' for c, h in ha if (c, h) not in hb],
        'onlyInB': [f'{c}/{hb[(c, h)]["name"]}' for c, h in hb if (c, h) not in ha],
    }
    return result


def union_golden() -> dict:
    """Golden set made of every hotel either browser session showed; price from the later session when it has one."""
    a, b = session('A'), session('B')
    g = json.loads(GOLDEN.read_text(encoding='utf-8'))
    stay = {'checkInDate': a['checkIn'], 'checkOutDate': b['checkOut'], 'nights': 2, 'adults': 2, 'children': 0,
            'country': 'IN', 'currency': 'INR'}
    items = []
    for key in sorted(set(a['hotels']) | set(b['hotels'])):
        h = b['hotels'].get(key) or a['hotels'][key]
        p = h['price'] if key in b['hotels'] else a['hotels'][key]['price']
        items.append({'hotelId': key[1], 'city': key[0], 'stay': stay,
                      'expected': {'name': h['name'], 'pricePerNight': p, 'available': p is not None},
                      'inA': key in a['hotels'], 'inB': key in b['hotels']})
    return {'auditInputs': g['auditInputs'], 'items': items}


def run_or_read(run_id: str | None, run_input: dict) -> str:
    return run_id or audit.run_actor(audit.ACTORS['hotels'], run_input)


def list_audit(run_id: str | None) -> dict:
    u = union_golden()
    rid = run_or_read(run_id, {**u['auditInputs'][0], 'maxHotelsPerQuery': 100})
    rows, run = audit.items_of(rid)
    r = audit.audit_hotels(u, rows)
    found = {(row['hotelId']) for row in rows if row.get('type', 'hotel') == 'hotel'}
    for side in ('inA', 'inB'):
        sub = [i for i in u['items'] if i[side]]
        k = sum(1 for i in sub if i['hotelId'] in found)
        r[f'recallOf{side[2]}'] = {'found': k, 'of': len(sub), 'rate': round(k / len(sub), 4),
                                   'ci95': audit.wilson(k, len(sub))}
    r.update({'runId': rid, 'rows': len(rows), 'usageUsd': run.get('usageTotalUsd'), 'unionSize': len(u['items'])})
    return r


def named_audit(run_id: str | None) -> dict:
    g = json.loads(GOLDEN.read_text(encoding='utf-8'))
    stay = g['items'][0]['stay']
    run_input = {'hotelUrls': [f'https://www.google.com/travel/hotels/entity/{i["hotelId"]}' for i in g['items']],
                 'checkInFrom': stay['checkInDate'], 'checkInTo': stay['checkInDate'], 'nights': stay['nights'],
                 'adults': 2, 'children': 0, 'currency': 'INR', 'country': 'IN', 'includeUnavailable': True,
                 'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL']}}
    rid = run_or_read(run_id, run_input)
    rows, run = audit.items_of(rid)
    a, b = session('A'), session('B')
    r = audit.audit_hotels(g, rows)  # recall, fields, price vs the golden capture (05:27 UTC)
    # Price against the latest browser price seen for the same hotel (session B, else A, else golden).
    index = {row['hotelId']: row for row in rows if row.get('type', 'hotel') == 'hotel'}
    fresh, within, misses = 0, 0, []
    for item in g['items']:
        key = (item['city'], item['hotelId'])
        src = b['hotels'].get(key) or a['hotels'].get(key)
        exp = src['price'] if src else item['expected']['pricePerNight']
        row = index.get(item['hotelId'])
        if exp is None or row is None:
            continue
        fresh += 1
        act = row.get('pricePerNight')
        ok = act is not None and abs(act - exp) / exp <= 0.10
        within += ok
        if not ok:
            misses.append({'item': f'{item["city"]}/{item["expected"]["name"]}', 'expected': exp, 'actual': act,
                            'source': 'session B' if key in b['hotels'] else 'session A' if src else 'golden'})
    r['priceWithin10Latest'] = {'matched': within, 'of': fresh, 'rate': round(within / fresh, 4) if fresh else None,
                                'ci95': audit.wilson(within, fresh), 'mismatches': misses}
    r.update({'runId': rid, 'rows': len(rows), 'usageUsd': run.get('usageTotalUsd')})
    return r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('what', choices=['compare', 'list', 'named'])
    ap.add_argument('--run-id')
    args = ap.parse_args()
    result = {'compare': compare, 'list': lambda: list_audit(args.run_id),
              'named': lambda: named_audit(args.run_id)}[args.what]()
    out = BASE / f'{args.what}.json'
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding='utf-8')
    brief = {k: v for k, v in result.items() if k not in ('prices', 'mismatches', 'fieldAccuracy', 'onlyInA', 'onlyInB')}
    print(json.dumps(brief, indent=1, ensure_ascii=False))
    print('saved', out)


if __name__ == '__main__':
    main()
