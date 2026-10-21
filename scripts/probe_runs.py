"""Start several platform runs at once and save a compact summary of each (status, rows, run stats, field null rates).

Usage: python scripts/probe_runs.py <name> '<json list of [label, actor, input]>'
Writes reports/fixes/<name>.json. Used to test fix items on the live sources before and after a change.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from apify_client import ApifyClient

ROOT = Path(__file__).resolve().parent.parent


def client() -> ApifyClient:
    out = subprocess.run(['apify.cmd' if sys.platform == 'win32' else 'apify', 'auth', 'token'],
                         capture_output=True, text=True).stdout.strip().split()
    return ApifyClient(out[-1])


def d(o):
    return o if isinstance(o, dict) else o.model_dump(by_alias=True)


def main() -> None:
    name, specs = sys.argv[1], json.loads(sys.argv[2])
    c = client()
    user = d(c.user().get())['username']
    # A spec of [label, "run:<runId>"] collects a finished run instead of starting a new one.
    started = [(s[0], {'id': s[1][4:]}) if s[1].startswith('run:') else
               (s[0], c.actor(f'{user}/{s[1]}').start(run_input=s[2])) for s in specs]  # each Actor's own default memory, as users run it
    out = []
    for label, run in started:
        run = d(c.run(d(run)['id']).wait_for_finish())
        page = c.dataset(run['defaultDatasetId']).list_items(limit=5000)
        items = list(page['items'] if isinstance(page, dict) else page.items)
        kv = c.key_value_store(run['defaultKeyValueStoreId'])
        recs = {}
        for key in ('INPUT', 'RUN_STATS', 'CHECKS', 'SEARCH_MATCHES', 'RUN_SUMMARY'):
            r = kv.get_record(key)
            if r:
                recs[key] = r['value'] if isinstance(r, dict) else r.value
        fields = sorted({k for i in items for k in i})
        nulls = {f: round(100 * sum(1 for i in items if i.get(f) in (None, [], '')) / len(items), 1) for f in fields} if items else {}
        out.append({'label': label, 'runId': run['id'], 'build': run.get('buildNumber'), 'status': run['status'],
                    'statusMessage': run.get('statusMessage'), 'rows': len(items),
                    'runTimeSecs': (run.get('stats') or {}).get('runTimeSecs'), 'usageUsd': run.get('usageTotalUsd'),
                    'usage': run.get('usage'), 'nullPercent': nulls, 'records': recs, 'sample': items[:3]})
        print(label, run['id'], run['status'], len(items), round(run.get('usageTotalUsd') or 0, 4),
              (run.get('stats') or {}).get('runTimeSecs'), run.get('statusMessage'))
    dest = ROOT / 'reports' / 'fixes' / f'{name}.json'
    dest.write_text(json.dumps(out, indent=1, ensure_ascii=False, default=str), encoding='utf-8')
    print('saved', dest)


if __name__ == '__main__':
    main()
