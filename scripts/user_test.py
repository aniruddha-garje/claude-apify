"""Test the published Actors the way a new user would, from a second Apify account used only for testing.

Before running: in a terminal, run `apify login` and sign in to the SECOND (test) account, then run
  python scripts/user_test.py            (all three, in publish order)
  python scripts/user_test.py reviews    (one Actor)
When done, run `apify login` again with your main account. The token never leaves this computer.

For each Actor it checks:
1. The public Store page opens (HTTP 200) and shows the Actor's title.
2. The Actor is public and its pay-per-event prices match the approved price table exactly, with no other paid event.
3. A run with the default input (the Console form's prefill) succeeds, saves results and finishes in under 60 seconds.
4. The charged events match the results saved: one `review`, `ad` or `hotel` event per row of that type, no other event,
   and the charge equals count x approved price.
Writes reports/launch/user-test-<UTC time>.md. The test account pays the event prices of these runs (cents).
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
API = 'https://api.apify.com/v2'
OWNER = 'garje'
MAX_SECONDS = 60
# Approved by Aniruddha on 8 Oct 2026 (Ads and Reviews 09:45 UTC, Hotels 10:21 UTC). USD per event.
ACTORS = {
    'reviews': ('app-store-play-reviews-scraper', {'review': 0.00012}, 'review'),
    'ads': ('google-ads-transparency-scraper', {'ad': 0.0015, 'asset': 0.003}, 'ad'),
    'hotels': ('google-hotels-scraper', {'hotel': 0.00199, 'offer': 0.001}, 'hotel'),
}
FREE_SYNTHETIC = ('apify-default-dataset-item', 'apify-actor-start')


def token() -> str:
    exe = 'apify.cmd' if sys.platform == 'win32' else 'apify'
    out = subprocess.run([exe, 'auth', 'token'], capture_output=True, text=True).stdout.strip().split()
    if not out:
        sys.exit('Not logged in: run `apify login` with the test account first.')
    return out[-1]


def call(path: str, tok: str, method: str = 'GET', body: dict | None = None) -> dict:
    req = urllib.request.Request(f'{API}{path}', method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Bearer {tok}', 'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=600))['data']


def default_input(actor_name: str) -> dict:
    """The Console form's first values (prefill, else default), read from this repo's input schema, which is the
    schema of the published build; a test account cannot read the owner's build record."""
    schema = json.loads((ROOT / actor_name / '.actor' / 'input_schema.json').read_text(encoding='utf-8'))
    out = {}
    for name, field in (schema.get('properties') or {}).items():
        if 'prefill' in field:
            out[name] = field['prefill']
        elif 'default' in field:
            out[name] = field['default']
    return out


def check(key: str, tok: str, me: str) -> list[tuple[str, bool, str]]:
    name, prices, main_event = ACTORS[key]
    rows: list[tuple[str, bool, str]] = []
    page_url = f'https://apify.com/{OWNER}/{name}'
    try:
        html = urllib.request.urlopen(urllib.request.Request(page_url, headers={'User-Agent': 'Mozilla/5.0'}),
                                      timeout=60).read().decode('utf-8', 'replace')
        actor = call(f'/acts/{OWNER}~{name}', tok)
        rows.append(('Public page opens and shows the title', actor['title'] in html, page_url))
    except Exception as exc:  # noqa: BLE001
        return [('Public page opens', False, f'{page_url}: {exc}')]
    rows.append(('Actor is public', bool(actor.get('isPublic')), f'isPublic={actor.get("isPublic")}'))
    info = (actor.get('pricingInfos') or [{}])[-1]
    events = (info.get('pricingPerEvent') or {}).get('actorChargeEvents') or {}
    paid = {k: e.get('eventPriceUsd') for k, e in events.items() if (e.get('eventPriceUsd') or 0) > 0}
    rows.append(('Prices match the approved table, no other paid event', paid == prices,
                 f'model {info.get("pricingModel")}, priced {paid}, approved {prices}'))

    run = call(f'/acts/{OWNER}~{name}/runs?waitForFinish=300', tok, 'POST', default_input(name))
    while run['status'] in ('READY', 'RUNNING'):
        run = call(f'/actor-runs/{run["id"]}?waitForFinish=60', tok)
    items = json.load(urllib.request.urlopen(urllib.request.Request(  # dataset items come as a bare list
        f'{API}/datasets/{run["defaultDatasetId"]}/items?clean=true&limit=1000',
        headers={'Authorization': f'Bearer {tok}'}), timeout=120))
    secs = (run.get('stats') or {}).get('runTimeSecs') or 0
    rows.append(('Default run succeeds', run['status'] == 'SUCCEEDED', f'run {run["id"]} by {me}, build {run.get("buildNumber")}'))
    rows.append(('Results are not empty', len(items) > 0, f'{len(items)} rows'))
    rows.append((f'Finishes in under {MAX_SECONDS} seconds', secs < MAX_SECONDS, f'{secs:.1f} s'))

    charged = run.get('chargedEventCounts') or {}
    main_rows = sum(1 for i in items if i.get('type', main_event) == main_event)
    rows.append((f'One {main_event} event per {main_event} row', charged.get(main_event) == main_rows,
                 f'charged {charged.get(main_event)}, rows {main_rows}'))
    others = {k: v for k, v in charged.items() if v and k not in prices}
    rows.append(('No other event charged', not others, f'other charged events: {others or "none"}'))
    expected = round(sum(prices[k] * v for k, v in charged.items() if k in prices), 6)
    billed = run.get('usageTotalUsd')
    rows.append(('Charge equals count x approved price', expected == round(main_rows * prices[main_event], 6),
                 f'expected ${expected}; run usageTotalUsd for the test account ${billed}'))
    return rows


def main() -> None:
    keys = sys.argv[1:] or ['reviews', 'ads', 'hotels']
    tok = token()
    me = call('/users/me', tok)['username']
    if me == OWNER:
        print('Warning: logged in as the owner account; run this from the second (test) account to see user charges.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%MZ')
    lines = [f'# User test from a second account ({stamp})', '', f'Account: {me}', '',
             '| Actor | Check | Result | Detail |', '|---|---|---|---|']
    ok_all = True
    for k in keys:
        for check_name, ok, detail in check(k, tok, me):
            ok_all &= ok
            lines.append(f'| {ACTORS[k][0]} | {check_name} | {"PASS" if ok else "FAIL"} | {detail} |')
            print(ACTORS[k][0], check_name, 'PASS' if ok else 'FAIL', detail)
    lines += ['', 'All checks passed.' if ok_all else 'At least one check failed; see the rows marked FAIL.']
    out = ROOT / 'reports' / 'launch' / f'user-test-{stamp}.md'
    out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print('saved', out)
    sys.exit(0 if ok_all else 1)


if __name__ == '__main__':
    main()
