"""Accuracy audit: run an Actor on its golden inputs on the Apify platform and compare with the golden set.

Usage:
  python scripts/audit.py reviews|hotels|ads [--run-id ID ...] [--label before|after]

Without --run-id it starts new platform runs with the golden inputs (through the Apify CLI and your `apify login`).
Expected values come only from tests/golden/golden.json, which was read from the live sites in a browser. Prints
recall, exact-field accuracy per field, the hotel price-within-10-percent rate and every mismatch with both values,
and saves the same to reports/audit/<actor>-<label>.json.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTORS = {'reviews': 'app-store-play-reviews-scraper', 'hotels': 'google-hotels-scraper',
          'ads': 'google-ads-transparency-scraper'}
GATES = {'exact': 0.98, 'price10': 0.90, 'recall': 0.95}
# Hotels: two browser sessions 12 minutes apart agreed on only 87.2% of hotels (reports/audit/hotels-baseline,
# 8 Oct 2026), so Google's own list moves. Per Aniruddha's rule the recall gate is that baseline minus 3 points,
# and data accuracy on the hotels returned must stay at 100%.
ACTOR_GATES = {'hotels': {'recall': 0.842, 'exact': 1.0}}
IST = timezone(timedelta(hours=5, minutes=30))


def cli(*args: str) -> str:
    env = {**os.environ, 'APIFY_CLI_DISABLE_TELEMETRY': '1'}
    exe = 'apify.cmd' if os.name == 'nt' else 'apify'
    p = subprocess.run([exe, *args], capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
    if p.returncode != 0:
        raise RuntimeError(f'apify {" ".join(args)} failed: {p.stderr[-1500:] or p.stdout[-1500:]}')
    return p.stdout + p.stderr


def cli_json(*args: str):
    out = cli(*args)
    starts = [i for i in (out.find('{'), out.find('[')) if i >= 0]
    value, _ = json.JSONDecoder().raw_decode(out[min(starts):])
    return value


def run_actor(actor: str, run_input: dict) -> str:
    """Start the run through the API and poll it. `apify call` crashed on 11 Oct 2026 ("unsettled top-level await")
    while a long Reviews run was still going, so the CLI is only used for the token here."""
    import time
    import urllib.request
    tmp = ROOT / 'reports' / 'tmp' / f'audit-input-{actor}.json'
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(run_input), encoding='utf-8')
    user = re.search(r'username:\s*(\S+)', cli('info')).group(1)
    token = cli('auth', 'token').strip().split()[-1]

    def api(method: str, path: str, body=None) -> dict:
        req = urllib.request.Request(f'https://api.apify.com/v2{path}', method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'})
        return json.load(urllib.request.urlopen(req, timeout=120))['data']

    run = api('POST', f'/acts/{user}~{actor}/runs?timeout=1800', run_input)  # the Actor's own default memory
    deadline = time.time() + 2400
    while run['status'] in ('READY', 'RUNNING') and time.time() < deadline:
        run = api('GET', f'/actor-runs/{run["id"]}?waitForFinish=60')
    print(f'run {run["id"]}: {run["status"]}', flush=True)
    return run['id']


def items_of(run_id: str) -> tuple[list[dict], dict]:
    run = cli_json('runs', 'info', run_id, '--json')
    items = cli_json('datasets', 'get-items', run['defaultDatasetId'], '--format', 'json')
    return items, run


def squash(s) -> str:
    return re.sub(r'\s+', '', s or '')


def utc_date(iso: str | None) -> str | None:
    return iso[:10] if iso else None


def ist_date(iso: str | None) -> str | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace('Z', '+00:00')).astimezone(IST).date().isoformat()


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    """95 percent Wilson score interval for k successes out of n."""
    if not n:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5) / (1 + z * z / n)
    return [round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4)]


class Tally:
    def __init__(self) -> None:
        self.fields: dict[str, list[int]] = {}
        self.mismatches: list[dict] = []

    def check(self, item_key: str, field: str, expected, actual, ok: bool) -> None:
        f = self.fields.setdefault(field, [0, 0])
        f[1] += 1
        if ok:
            f[0] += 1
        else:
            self.mismatches.append({'item': item_key, 'field': field, 'expected': expected, 'actual': actual})

    def accuracy(self) -> dict:
        return {k: {'matched': v[0], 'total': v[1], 'rate': round(v[0] / v[1], 4) if v[1] else None,
                    'ci95': wilson(v[0], v[1])} for k, v in self.fields.items()}

    def overall(self) -> float | None:
        m = sum(v[0] for v in self.fields.values())
        t = sum(v[1] for v in self.fields.values())
        return round(m / t, 4) if t else None

    def overall_ci(self) -> list[float] | None:
        return wilson(sum(v[0] for v in self.fields.values()), sum(v[1] for v in self.fields.values()))


def audit_reviews(golden: dict, rows: list[dict]) -> dict:
    index = {(r['store'], r['appId'], r['country'], str(r['reviewId'])): r for r in rows}
    t = Tally()
    found = 0
    for g in golden['items']:
        key = (g['store'], g['appId'], g['country'], g['reviewId'])
        name = '/'.join(key)
        row = index.get(key)
        if row is None:
            t.mismatches.append({'item': name, 'field': 'RECALL', 'expected': 'present', 'actual': 'missing'})
            continue
        found += 1
        e = g['expected']
        t.check(name, 'rating', e['rating'], row['rating'], e['rating'] == row['rating'])
        # Both stores display the UTC calendar date (checked 8 Oct 2026: every Play review written after 18:30 UTC
        # showed the UTC date, not the date in India where the browser ran).
        actual_date = utc_date(row['date'])
        t.check(name, 'date', e['date'], actual_date, e['date'] == actual_date)
        t.check(name, 'text', e['text'][:80], (row['text'] or '')[:80], squash(e['text']) == squash(row['text']))
        if g['store'] == 'appStore':
            t.check(name, 'title', e['title'], row['title'], e['title'] == row['title'])
    return {'recall': round(found / len(golden['items']), 4), 'found': found, 'golden': len(golden['items']),
            'recallCi95': wilson(found, len(golden['items'])), 'exactOverallCi95': t.overall_ci(),
            'fieldAccuracy': t.accuracy(), 'exactOverall': t.overall(), 'mismatches': t.mismatches}


def audit_hotels(golden: dict, rows: list[dict]) -> dict:
    index = {(r['hotelId'], r['checkInDate']): r for r in rows if r.get('type', 'hotel') == 'hotel'}
    t = Tally()
    found = within = priced = 0
    price_rows = []
    for g in golden['items']:
        key = (g['hotelId'], g['stay']['checkInDate'])
        name = f'{g["city"]}/{g["expected"]["name"]}'
        row = index.get(key)
        if row is None:
            t.mismatches.append({'item': name, 'field': 'RECALL', 'expected': 'present', 'actual': 'missing'})
            continue
        found += 1
        t.check(name, 'name', g['expected']['name'], row['name'], g['expected']['name'] == row['name'])
        for f in ('checkInDate', 'checkOutDate', 'nights', 'adults', 'children', 'currency', 'country'):
            t.check(name, f, g['stay'][f], row.get(f), g['stay'][f] == row.get(f))
        exp_avail = g['expected'].get('available', True)
        t.check(name, 'available', exp_avail, row.get('available'), exp_avail == row.get('available'))
        exp, act = g['expected']['pricePerNight'], row.get('pricePerNight')
        if exp is None:
            continue
        priced += 1
        ok = act is not None and abs(act - exp) / exp <= 0.10
        within += ok
        price_rows.append({'item': name, 'expected': exp, 'actual': act,
                           'diffPct': round((act - exp) / exp * 100, 1) if act is not None else None})
        if not ok:
            t.mismatches.append({'item': name, 'field': 'pricePerNight (10%)', 'expected': exp, 'actual': act})
    return {'recall': round(found / len(golden['items']), 4), 'found': found, 'golden': len(golden['items']),
            'recallCi95': wilson(found, len(golden['items'])), 'exactOverallCi95': t.overall_ci(),
            'fieldAccuracy': t.accuracy(), 'exactOverall': t.overall(),
            'priceWithin10': round(within / priced, 4) if priced else None, 'priceWithin10ci95': wilson(within, priced),
            'pricedHotels': priced, 'prices': price_rows,
            'mismatches': t.mismatches}


def audit_ads(golden: dict, rows: list[dict]) -> dict:
    index = {r['creativeId']: r for r in rows}
    t = Tally()
    found = 0
    for g in golden['items']:
        name = f'{g["advertiserId"]}/{g["creativeId"]}'
        row = index.get(g['creativeId'])
        if row is None:
            t.mismatches.append({'item': name, 'field': 'RECALL', 'expected': 'present', 'actual': 'missing'})
            continue
        found += 1
        e = g['expected']
        t.check(name, 'advertiserId', g['advertiserId'], row['advertiserId'], g['advertiserId'] == row['advertiserId'])
        t.check(name, 'advertiserName', e['advertiserName'], row['advertiserName'],
                e['advertiserName'] == row['advertiserName'])
        t.check(name, 'format', e['format'], row['format'], e['format'] == row['format'])
        actual = row.get('lastShownDate') or utc_date(row['lastShown'])  # builds before 0.1.7 had no lastShownDate
        t.check(name, 'lastShownDate', e['lastShownDate'], actual, e['lastShownDate'] == actual)
    return {'recall': round(found / len(golden['items']), 4), 'found': found, 'golden': len(golden['items']),
            'recallCi95': wilson(found, len(golden['items'])), 'exactOverallCi95': t.overall_ci(),
            'fieldAccuracy': t.accuracy(), 'exactOverall': t.overall(), 'mismatches': t.mismatches}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('which', choices=ACTORS)
    ap.add_argument('--run-id', action='append', default=[])
    ap.add_argument('--label', default='latest')
    args = ap.parse_args()
    actor = ACTORS[args.which]
    golden = json.loads((ROOT / actor / 'tests' / 'golden' / 'golden.json').read_text(encoding='utf-8'))
    run_ids = args.run_id
    if not run_ids:
        inputs = golden.get('auditInputs') or [golden['auditInput']]
        run_ids = [run_actor(actor, i) for i in inputs]
    rows, runs = [], []
    for rid in run_ids:
        items, run = items_of(rid)
        rows += items
        runs.append({'runId': rid, 'status': run['status'], 'buildNumber': run.get('buildNumber'),
                     'items': len(items), 'statusMessage': run.get('statusMessage')})
    result = {'reviews': audit_reviews, 'hotels': audit_hotels, 'ads': audit_ads}[args.which](golden, rows)
    g = {**GATES, **ACTOR_GATES.get(args.which, {})}
    gates = {f'recall>={g["recall"]:.1%}': result['recall'] >= g['recall'],
             f'exactFields>={g["exact"]:.0%}': (result['exactOverall'] or 0) >= g['exact']}
    if args.which == 'hotels':
        gates['price within 10% for >=90%'] = (result['priceWithin10'] or 0) >= g['price10']
    report = {'actor': actor, 'label': args.label, 'auditedAt': datetime.now(timezone.utc).isoformat(),
              'runs': runs, **result, 'gates': gates}
    out = ROOT / 'reports' / 'audit' / f'{args.which}-{args.label}.json'
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'== {actor} ({args.label}) runs: {", ".join(r["runId"] for r in runs)}')
    def ci(x):
        return f' (95% CI {x[0]:.1%} to {x[1]:.1%})' if x else ''
    print(f'recall: {result["found"]}/{result["golden"]} = {result["recall"]:.1%}{ci(result["recallCi95"])}')
    for f, v in result['fieldAccuracy'].items():
        print(f'  {f}: {v["matched"]}/{v["total"]} = {v["rate"]:.1%}{ci(v["ci95"])}')
    print(f'exact-field accuracy overall: {result["exactOverall"]:.1%}{ci(result["exactOverallCi95"])}')
    if args.which == 'hotels':
        print(f'price within 10%: {result["priceWithin10"]:.1%} of {result["pricedHotels"]} priced hotels'
              f'{ci(result["priceWithin10ci95"])}')
    print('mismatches:' if result['mismatches'] else 'mismatches: none')
    for m in result['mismatches']:
        print(f'  {m["item"]} | {m["field"]} | expected: {m["expected"]!r} | actual: {m["actual"]!r}')
    print('gates:', json.dumps(gates))


if __name__ == '__main__':
    sys.exit(main())
