"""Audit every input field and every input error message of the three Actors.

Usage: python scripts/audit_inputs.py [git revision]   (default: the working tree)

A field passes when it has a title, a description and an explicit `example` (the sample value Apify shows on the Store
page). An error message passes when it names the bad value or the rule that was broken and shows a valid
example (contains "xample", or "for example"). Prints one line per failure and a count per Actor.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ACTORS = ['app-store-play-reviews-scraper', 'google-ads-transparency-scraper', 'google-hotels-scraper']


def read(path: str, rev: str | None) -> str:
    if rev is None:
        return (ROOT / path).read_text(encoding='utf-8')
    return subprocess.run(['git', 'show', f'{rev}:{path}'], cwd=ROOT, capture_output=True, text=True,
                          encoding='utf-8', check=True).stdout


def has_example(field: dict) -> bool:
    return 'example' in field


def error_messages(source: str) -> list[str]:
    """The text of every `raise InputError(...)` (f-strings shown with {...} placeholders)."""
    tree = ast.parse(source)
    consts = {}  # module-level messages held in a constant, such as NAMED_MODE_OFF = ('...')
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                consts[node.targets[0].id] = repr(ast.literal_eval(node.value))
            except ValueError:
                pass
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            fn = node.exc.func
            if getattr(fn, 'id', None) == 'InputError' and node.exc.args:
                arg = node.exc.args[0]
                out.append(consts.get(arg.id, arg.id) if isinstance(arg, ast.Name) else ast.unparse(arg))
    return out


def audit(rev: str | None) -> dict:
    report = {}
    for actor in ACTORS:
        schema = json.loads(read(f'{actor}/.actor/input_schema.json', rev))
        fields = []
        for name, f in schema['properties'].items():
            missing = [k for k, ok in (('title', bool(f.get('title'))), ('description', bool(f.get('description'))),
                                       ('example', has_example(f))) if not ok]
            if missing:
                fields.append({'field': name, 'missing': missing})
        errors = []
        for msg in error_messages(read(f'{actor}/src/main.py', rev)):
            if 'xample' not in msg and 'for example' not in msg:
                errors.append(msg)
        report[actor] = {'fields': len(schema['properties']), 'fieldFailures': fields,
                         'errorMessages': len(error_messages(read(f'{actor}/src/main.py', rev))), 'errorFailures': errors}
    return report


def main() -> None:
    rev = sys.argv[1] if len(sys.argv) > 1 else None
    report = audit(rev)
    for actor, r in report.items():
        print(f'== {actor}: {len(r["fieldFailures"])} of {r["fields"]} fields fail, '
              f'{len(r["errorFailures"])} of {r["errorMessages"]} error messages fail')
        for f in r['fieldFailures']:
            print(f'  field {f["field"]}: missing {", ".join(f["missing"])}')
        for e in r['errorFailures']:
            print(f'  error: {e}')
    out = ROOT / 'reports' / 'fixes' / f'input-audit-{rev or "working"}.json'
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    main()
