"""Create (or update) one drift-canary task per Actor and a daily schedule that runs all three.

Uses the login from `apify login` through `apify auth token`; the token is passed to the API client in memory and is
never printed or saved. Optional: --webhook URL adds a webhook that posts to that URL whenever a canary run fails or
times out (an email service, Slack or Zapier hook). Run: python scripts/setup_canaries.py [--webhook URL] [--run-now]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from apify_client import ApifyClient

ROOT = Path(__file__).resolve().parent.parent
CANARIES = {
    'app-store-play-reviews-scraper': {
        'name': 'canary-reviews',
        'input': {'apps': ['310633997', 'com.whatsapp'], 'countries': ['us'], 'maxReviewsPerApp': 50,
                  'canary': {'baselineStore': 'canary-baseline-reviews', 'thresholdPoints': 5},
                  'proxyConfiguration': {'useApifyProxy': True}},
    },
    'google-hotels-scraper': {
        'name': 'canary-hotels',
        'input': {'queries': ['hotels in Paris'], 'nights': 2, 'adults': 2, 'currency': 'USD', 'country': 'US',
                  'maxHotelsPerQuery': 20,
                  'canary': {'baselineStore': 'canary-baseline-hotels', 'thresholdPoints': 5},
                  'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL']}},
    },
    'google-ads-transparency-scraper': {
        'name': 'canary-ads',
        # 200 ads and a 10-point threshold: on 30 ads, normal daily rotation of Nike's newest ads (2 ads moving from
        # image-only to text) tripped a 5-point alarm on 8 Oct 2026 with no fault in the data.
        'input': {'domains': ['nike.com'], 'region': 'US', 'maxAdsPerAdvertiser': 100, 'maxAdsPerDomain': 200,
                  'canary': {'baselineStore': 'canary-baseline-ads', 'thresholdPoints': 10},
                  'proxyConfiguration': {'useApifyProxy': True}},
    },
}


def token() -> str:
    exe = 'apify.cmd' if os.name == 'nt' else 'apify'
    out = subprocess.run([exe, 'auth', 'token'], capture_output=True, text=True,
                         env={**os.environ, 'APIFY_CLI_DISABLE_TELEMETRY': '1'}).stdout
    return out.strip().split()[-1]


def as_dict(obj):
    return obj.model_dump(by_alias=True) if hasattr(obj, 'model_dump') else dict(obj)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--webhook')
    ap.add_argument('--run-now', action='store_true')
    args = ap.parse_args()
    client = ApifyClient(token())
    me = as_dict(client.user('me').get())
    created = {}
    tasks = {as_dict(t)['name']: as_dict(t) for t in client.tasks().list().items}
    for actor, spec in CANARIES.items():
        act = as_dict(client.actor(f'{me["username"]}/{actor}').get())
        if spec['name'] in tasks:
            task_id = tasks[spec['name']]['id']
            client.task(task_id).update(task_input=spec['input'], memory_mbytes=1024)
        else:
            task_id = as_dict(client.tasks().create(actor_id=act['id'], name=spec['name'], task_input=spec['input'],
                                                    memory_mbytes=1024))['id']
        created[spec['name']] = task_id
        if args.webhook:
            client.webhooks().create(event_types=['ACTOR.RUN.FAILED', 'ACTOR.RUN.TIMED_OUT'], request_url=args.webhook,
                                     actor_task_id=task_id, idempotency_key=f'canary-alert-{spec["name"]}')
    schedules = {as_dict(s)['name']: as_dict(s) for s in client.schedules().list().items}
    actions = [{'type': 'RUN_ACTOR_TASK', 'actorTaskId': tid} for tid in created.values()]
    if 'daily-drift-canaries' in schedules:
        sid = schedules['daily-drift-canaries']['id']
        client.schedule(sid).update(cron_expression='0 6 * * *', is_enabled=True, actions=actions, timezone='UTC')
    else:
        sid = as_dict(client.schedules().create(cron_expression='0 6 * * *', is_enabled=True, is_exclusive=True,
                                                name='daily-drift-canaries', actions=actions, timezone='UTC',
                                                description='Daily drift canary for the three Actors'))['id']
    runs = {}
    if args.run_now:
        for name, tid in created.items():
            run = as_dict(client.task(tid).call())
            runs[name] = {'runId': run['id'], 'status': run['status'], 'statusMessage': run.get('statusMessage')}
    result = {'tasks': created, 'scheduleId': sid, 'cron': '0 6 * * * (06:00 UTC daily)', 'webhook': bool(args.webhook),
              'firstRuns': runs}
    (ROOT / 'reports' / 'audit' / 'canaries.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
