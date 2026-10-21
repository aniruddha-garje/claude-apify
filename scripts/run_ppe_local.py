"""Run one Actor locally with pay-per-event test mode, using the prices in its .actor/pay_per_event.json.

Usage: python scripts/run_ppe_local.py <actor-folder> [max_total_charge_usd]
Then reads the local charging log and checks that charged counts match the rows in the dataset.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
actor = root / sys.argv[1]
max_charge = sys.argv[2] if len(sys.argv) > 2 else None

events = json.loads((actor / '.actor' / 'pay_per_event.json').read_text())
# On the platform the synthetic 'apify-default-dataset-item' event costs nothing unless it is priced in Console.
# Locally the SDK prices unknown events at $1, so declare it at $0 to mirror the platform.
test_events = {**events, 'apify-default-dataset-item': {'eventTitle': 'Dataset item', 'eventPriceUsd': 0}}
pricing = {'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents': test_events}}

env = dict(os.environ)
env['ACTOR_TEST_PAY_PER_EVENT'] = 'true'
env['APIFY_ACTOR_PRICING_INFO'] = json.dumps(pricing)
# An empty {} is read as 'not set', so list each event with a zero count.
env['APIFY_CHARGED_ACTOR_EVENT_COUNTS'] = json.dumps({name: 0 for name in test_events})
env['APIFY_CLI_DISABLE_TELEMETRY'] = '1'
env['PYTHONIOENCODING'] = 'utf-8'
env['PATH'] = str(root / '.venv' / 'Scripts') + os.pathsep + str(root / '.venv' / 'bin') + os.pathsep + env['PATH']
if max_charge:
    env['ACTOR_MAX_TOTAL_CHARGE_USD'] = max_charge

import shutil
shutil.rmtree(actor / 'storage' / 'datasets' / 'charging-log', ignore_errors=True)
apify = 'apify.cmd' if os.name == 'nt' else 'apify'
proc = subprocess.run([apify, 'run', '--purge'], cwd=actor, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace')
log = proc.stdout + proc.stderr
print('\n'.join(line for line in log.splitlines() if 'WARN' in line or 'ERROR' in line or 'Delivered' in line or 'Exiting' in line))

storage = actor / 'storage' / 'datasets'
rows = [p for p in (storage / 'default').glob('*.json') if p.name != '__metadata__.json']
charges = []
for path in (storage / 'charging-log').glob('*.json') if (storage / 'charging-log').exists() else []:
    if path.name != '__metadata__.json':
        charges.append(json.loads(path.read_text(encoding='utf-8')))
by_event: dict[str, int] = {}
total = 0.0
for c in charges:
    by_event[c['event_name']] = by_event.get(c['event_name'], 0) + c['charged_count']
    price = events.get(c['event_name'], {}).get('eventPriceUsd', 0)
    total += c['charged_count'] * price
print(json.dumps({'exitCode': proc.returncode, 'datasetRows': len(rows), 'chargedEvents': by_event,
                  'chargedUsd': round(total, 6), 'maxTotalChargeUsd': max_charge}, indent=1))
