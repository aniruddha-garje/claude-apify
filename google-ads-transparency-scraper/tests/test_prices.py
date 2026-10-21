"""The price file holds exactly the events and prices Aniruddha approved on 8 Oct 2026, and nothing else.

Approved: ad $0.0015, asset $0.003 per event. Apify's own synthetic events
(apify-default-dataset-item, apify-actor-start) must never be priced here or charged by our code; test_events.py
proves the code charges only the events in this file.
"""

import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
APPROVED = {"ad": 0.0015, "asset": 0.003}


def test_price_file_matches_the_approved_prices_exactly():
    ppe = json.loads((ROOT / '.actor' / 'pay_per_event.json').read_text(encoding='utf-8'))
    assert {name: e['eventPriceUsd'] for name, e in ppe.items()} == APPROVED


def test_no_synthetic_event_is_priced_or_named_in_code():
    ppe = json.loads((ROOT / '.actor' / 'pay_per_event.json').read_text(encoding='utf-8'))
    assert not any(name.startswith('apify-') for name in ppe)
    for src in (ROOT / 'src').glob('*.py'):
        text = src.read_text(encoding='utf-8')
        assert 'apify-default-dataset-item' not in text and 'apify-actor-start' not in text, src.name
