"""The input form never carries a stale value, and the prefill still works 30 days from now."""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import src.main as main_module
from src.main import resolve_input

SCHEMA = json.loads((Path(__file__).parent.parent / '.actor' / 'input_schema.json').read_text(encoding='utf-8'))
DATE_LIKE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}')


def prefill_input() -> dict:
    out = {}
    for name, prop in SCHEMA['properties'].items():
        if 'prefill' in prop:
            out[name] = prop['prefill']
        elif 'default' in prop:
            out[name] = prop['default']
    return out


def test_no_prefill_or_default_contains_a_date():
    for name, prop in SCHEMA['properties'].items():
        for key in ('prefill', 'default'):
            if key in prop:
                assert not DATE_LIKE.search(json.dumps(prop[key])), f'{name}.{key} holds a fixed date'


def test_date_window_is_computed_at_run_time_with_the_clock_30_days_ahead(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(days=30)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(main_module, 'datetime', FrozenDatetime)
    p = resolve_input(prefill_input())
    assert p['dateTo'] == future.date()
    assert p['dateFrom'] == future.date() - timedelta(days=30)
