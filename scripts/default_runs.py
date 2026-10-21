"""Run each Actor once on the Apify platform with the input a new user sees in Console, and check the result.

Usage: python scripts/default_runs.py [reviews hotels ads]

The input is each field's `prefill` from the Actor's input schema (falling back to `default`), which is what the
Console form shows on first open. A run passes when it SUCCEEDED, saved at least one result, and its run time was
under 60 seconds. Results are saved to reports/launch/default-runs-<UTC time>.json.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit  # noqa: E402

ROOT = audit.ROOT
MAX_SECONDS = 60


def console_input(actor: str) -> dict:
    schema = json.loads((ROOT / actor / '.actor' / 'input_schema.json').read_text(encoding='utf-8'))
    out = {}
    for name, field in schema['properties'].items():
        if 'prefill' in field:
            out[name] = field['prefill']
        elif 'default' in field:
            out[name] = field['default']
    return out


def main() -> None:
    which = sys.argv[1:] or ['reviews', 'ads', 'hotels']
    results = []
    for w in which:
        actor = audit.ACTORS[w]
        rid = audit.run_actor(actor, console_input(actor))
        items, run = audit.items_of(rid)
        secs = (run.get('stats') or {}).get('runTimeSecs')
        ok = run['status'] == 'SUCCEEDED' and len(items) > 0 and secs is not None and secs < MAX_SECONDS
        results.append({'actor': actor, 'runId': rid, 'build': run.get('buildNumber'), 'status': run['status'],
                        'statusMessage': run.get('statusMessage'), 'results': len(items),
                        'runTimeSecs': secs, 'usageUsd': run.get('usageTotalUsd'), 'pass': ok})
        print(json.dumps(results[-1]))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%MZ')
    out = ROOT / 'reports' / 'launch' / f'default-runs-{stamp}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=1), encoding='utf-8')
    print('saved', out)
    sys.exit(0 if all(r['pass'] for r in results) else 1)


if __name__ == '__main__':
    main()
