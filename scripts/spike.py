"""Run one Actor on the Apify platform and measure it against the launch gates.

Usage: python scripts/spike.py <actor-folder> <input.json> <label> [--memory 1024]

Drives the Apify CLI, which uses the login from `apify login` (no token is read or printed). Writes
reports/spike/<actor>-<label>.json with the measured numbers and <actor>-<label>-sample.csv
with 20 rows for a hand check.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRICE_PER_GB = 8.0
PLANNED_PRICE_PER_1000 = {  # first paid event, from each Actor's pay_per_event.json, per 1,000 results
    'google-hotels-scraper': 3.0,
    'google-ads-transparency-scraper': 2.0,
    'app-store-play-reviews-scraper': 0.5,
}


def cli(*args: str, capture: bool = True) -> str:
    env = {**os.environ, 'APIFY_CLI_DISABLE_TELEMETRY': '1'}
    exe = 'apify.cmd' if os.name == 'nt' else 'apify'
    proc = subprocess.run([exe, *args], capture_output=True, text=True, encoding='utf-8', errors='replace', env=env)
    if proc.returncode != 0:
        raise RuntimeError(f'apify {" ".join(args)} failed: {proc.stderr[-2000:] or proc.stdout[-2000:]}')
    return proc.stdout + proc.stderr


def cli_json(*args: str):
    out = cli(*args)
    starts = [i for i in (out.find('{'), out.find('[')) if i >= 0]
    value, _ = json.JSONDecoder().raw_decode(out[min(starts):])
    return value


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('actor')
    ap.add_argument('input')
    ap.add_argument('label')
    ap.add_argument('--memory', type=int, default=1024)
    ap.add_argument('--timeout', type=int, default=3600)
    ap.add_argument('--run-id', help='Measure an existing run instead of starting a new one')
    args = ap.parse_args()

    username = re.search(r'username:\s*(\S+)', cli('info')).group(1)
    actor_id = f'{username}/{args.actor}'
    run_input = json.loads(Path(args.input).read_text(encoding='utf-8'))

    out = ROOT / 'reports' / 'spike'
    out.mkdir(parents=True, exist_ok=True)
    if args.run_id:
        run_id = args.run_id
    else:
        print(f'Starting {actor_id} ({args.label})...', flush=True)
        log = cli('call', actor_id, '-f', args.input, '-m', str(args.memory), '-t', str(args.timeout))
        run_id = re.search(r'Run ID:\s*(\w+)', log).group(1)
        (out / f'{args.actor}-{args.label}.log').write_text(log, encoding='utf-8')
    run = cli_json('runs', 'info', run_id, '--json')

    stats = None
    for key in ('RUN_STATS', 'RUN_SUMMARY'):
        try:
            raw = cli('kvs', 'get-value', run['defaultKeyValueStoreId'], key)
            stats, _ = json.JSONDecoder().raw_decode(raw[raw.index('{'):])
            break
        except (RuntimeError, ValueError):
            continue
    items = cli_json('datasets', 'get-items', run['defaultDatasetId'], '--format', 'json')

    schema = json.loads((ROOT / args.actor / '.actor' / 'dataset_schema.json').read_text(encoding='utf-8'))
    required = schema['fields']['required']
    primary = [r for r in items if r.get('type', 'hotel') == 'hotel'] if args.actor == 'google-hotels-scraper' else items
    filled = {f: sum(1 for r in primary if r.get(f) not in (None, '', [])) for f in required}
    cells = len(primary) * len(required)
    completeness = sum(filled.values()) / cells if cells else None

    usage = run.get('usage') or {}
    resi_gb = float(usage.get('PROXY_RESIDENTIAL_TRANSFER_GBYTES') or 0)
    net = run.get('stats') or {}
    net_bytes = float(net.get('netRxBytes') or 0) + float(net.get('netTxBytes') or 0)
    bytes_used = resi_gb * 1024 ** 3 if resi_gb > 0 else net_bytes
    basis = 'residential proxy transfer billed by Apify' if resi_gb > 0 else 'container network in+out (upper bound)'
    n = len(items)
    kb_per_result = bytes_used / 1024 / n if n else None
    cost_per_1000 = kb_per_result * 1000 / 1048576 * PRICE_PER_GB if kb_per_result is not None else None
    planned = PLANNED_PRICE_PER_1000[args.actor]
    req = (stats or {}).get('requests') or {}

    report = {
        'actor': actor_id,
        'label': args.label,
        'runId': run['id'],
        'status': run['status'],
        'runUrl': run.get('consoleUrl') or f'https://console.apify.com/view/runs/{run["id"]}',
        'input': run_input,
        'results': n,
        'primaryResults': len(primary),
        'attempts': req.get('attempts'),
        'attemptSuccessRate': req.get('attemptSuccessRate'),
        'requestSuccessRate': req.get('requestSuccessRate'),
        'topErrors': req.get('topErrors'),
        'bytesMeasured': bytes_used,
        'bytesBasis': basis,
        'residentialGb': resi_gb,
        'kbPerResult': round(kb_per_result, 2) if kb_per_result is not None else None,
        'proxyCostPer1000AtResidentialRate': round(cost_per_1000, 4) if cost_per_1000 is not None else None,
        'plannedPricePer1000': planned,
        'costShareOfPrice': round(cost_per_1000 / planned, 4) if cost_per_1000 is not None else None,
        'requiredFields': required,
        'fieldCompleteness': round(completeness, 4) if completeness is not None else None,
        'filledPerField': filled,
        'platformUsageUsd': run.get('usageTotalUsd'),
        'durationSecs': (net.get('runTimeSecs') or None),
        'gates': {
            'successRate>=0.95': (req.get('attemptSuccessRate') or 0) >= 0.95,
            'costShare<=0.35': cost_per_1000 is not None and cost_per_1000 / planned <= 0.35,
            'completeness>=0.98': (completeness or 0) >= 0.98,
        },
        'runStats': stats,
    }
    (out / f'{args.actor}-{args.label}.json').write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    sample = primary[:: max(1, len(primary) // 20)][:20]
    if sample:
        fields = list(dict.fromkeys(k for r in sample for k in r))
        with open(out / f'{args.actor}-{args.label}-sample.csv', 'w', newline='', encoding='utf-8-sig') as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for r in sample:
                w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in r.items()})
    summary = {k: report[k] for k in ('status', 'results', 'attempts', 'attemptSuccessRate', 'kbPerResult', 'bytesBasis',
                                      'proxyCostPer1000AtResidentialRate', 'costShareOfPrice', 'fieldCompleteness',
                                      'platformUsageUsd', 'gates', 'runUrl')}
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    sys.exit(main())
