"""One command for the full accuracy check: Playwright reads the live site, the Actor runs on the platform right
after, and the audit compares the two.

Usage: python scripts/accuracy_check.py [reviews] [hotels] [ads]   (all three when none is given)
Results go to reports/audit/<actor>-check-<UTC time>.json, and a dated summary to reports/weekly/<UTC date>.md.
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def step(*args: str) -> None:
    print('>>', ' '.join(args), flush=True)
    subprocess.run([PY, *args], cwd=ROOT, check=True)


def summary(which: list[str], stamp: str, failures: dict[str, str]) -> Path:
    lines = [f'# Weekly accuracy check, {stamp}', '',
             'Playwright read the live sites, each Actor ran on the Apify platform straight after, and the audit '
             'compared the two. Gates: recall at least 95% (Hotels 84.2%, the browser-vs-browser baseline minus 3 points), '
             'exact fields at least 98% (Hotels 100%), hotel prices within 10% for at least 90%.', '',
             '| Actor | Golden items | Recall | Exact fields | Hotel prices within 10% | Gates | Runs |',
             '|---|---|---|---|---|---|---|']
    details = []
    for w in which:
        path = ROOT / 'reports' / 'audit' / f'{w}-check-{stamp}.json'
        if w in failures or not path.exists():
            lines.append(f'| {w} | | | | | NOT RUN: {failures.get(w, "no result")} | |')
            continue
        r = json.loads(path.read_text(encoding='utf-8'))
        price = f'{r["priceWithin10"]:.1%}' if r.get('priceWithin10') is not None else 'n/a'
        ok = 'pass' if all(r['gates'].values()) else 'FAIL: ' + ', '.join(k for k, v in r['gates'].items() if not v)
        lines.append(f'| {w} | {r["golden"]} | {r["recall"]:.1%} | {r["exactOverall"]:.1%} | {price} | {ok} | '
                     + ', '.join(x['runId'] for x in r['runs']) + ' |')
        details += ['', f'## {w}: mismatches', ''] + (
            [f'- {m["item"]} | {m["field"]} | expected {m["expected"]!r} | actual {m["actual"]!r}' for m in r['mismatches']]
            or ['None.'])
    out = ROOT / 'reports' / 'weekly' / f'{stamp[:8]}.md'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join(lines + details) + '\n', encoding='utf-8')
    return out


def main() -> None:
    which = sys.argv[1:] or ['hotels', 'reviews', 'ads']  # hotels first: its prices move fastest
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%MZ')
    failures: dict[str, str] = {}
    for w in which:
        try:
            step('scripts/pw_capture.py', w)
            step('scripts/build_golden.py', w)
            step('scripts/audit.py', w, '--label', f'check-{stamp}')
        except subprocess.CalledProcessError as exc:
            failures[w] = f'step failed: {" ".join(exc.cmd[1:])}'
    print('report:', summary(which, stamp, failures))
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
