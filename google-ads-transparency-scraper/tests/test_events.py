"""Every event the code can charge is one priced in .actor/pay_per_event.json, and nothing else charges.

The check reads the source code itself, so a new charge added anywhere fails this test until it is priced.
"""

import ast
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
PRICED = set(json.loads((ROOT / '.actor' / 'pay_per_event.json').read_text(encoding='utf-8')))
SOURCES = {p.name: ast.parse(p.read_text(encoding='utf-8')) for p in (ROOT / 'src').glob('*.py')}


def _calls(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _name(call) -> str:
    f = call.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', '')


def test_every_delivery_event_is_priced():
    events = set()
    for tree in SOURCES.values():
        for call in _calls(tree):
            if _name(call) == 'Delivery':
                arg = call.args[0]
                assert isinstance(arg, ast.Constant), 'Delivery event names must be literal strings'
                events.add(arg.value)
    assert events, 'no Delivery events found'
    assert events <= PRICED, f'unpriced events: {events - PRICED}'
    assert events == PRICED, f'priced but never charged: {PRICED - events}'


def test_charge_is_called_only_inside_delivery_with_its_own_event():
    for fname, tree in SOURCES.items():
        for call in _calls(tree):
            if _name(call) != 'charge':
                continue
            assert fname == 'delivery.py', f'{fname} calls charge() directly'
            kw = {k.arg: k.value for k in call.keywords}
            target = kw['event_name']
            assert isinstance(target, ast.Attribute) and target.attr == 'event_name'


def test_push_data_never_charges_an_event_by_itself():
    for fname, tree in SOURCES.items():
        for call in _calls(tree):
            if _name(call) == 'push_data':
                assert fname == 'delivery.py', f'{fname} pushes rows outside the delivery module'
                assert not any(k.arg == 'charged_event_name' for k in call.keywords)
