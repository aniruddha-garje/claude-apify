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
    """What Console fills in for a new user: the prefill, else the default."""
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


def test_prefill_has_no_check_in_dates_so_they_are_computed_at_run_time():
    assert 'prefill' not in SCHEMA['properties']['checkInFrom'] and 'default' not in SCHEMA['properties']['checkInFrom']
    assert 'prefill' not in SCHEMA['properties']['checkInTo'] and 'default' not in SCHEMA['properties']['checkInTo']


def test_prefill_works_with_the_clock_30_days_ahead(monkeypatch):
    future = datetime.now(timezone.utc) + timedelta(days=30)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return future if tz else future.replace(tzinfo=None)

    monkeypatch.setattr(main_module, 'datetime', FrozenDatetime)
    p = resolve_input(prefill_input())  # the same call main() makes at the start of a run
    assert p['dates'] == [future.date() + timedelta(days=1)]  # tomorrow, relative to the shifted clock
    assert p['queries'] == ['hotels in Paris'] and p['maxHotels'] == 5
