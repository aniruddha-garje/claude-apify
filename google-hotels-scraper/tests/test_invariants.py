"""Invariant rules: real parsed hotels and offers pass, and every rule catches the problem it guards against."""

import gzip
from datetime import date
from pathlib import Path

import pytest

from src.invariants import check_rows
from src.main import _fill, _offer_row, read_input
from src.parsers import parse_entity, parse_search
from src.run_checks import compare, profile

FIX = Path(__file__).parent / 'fixtures'
P = read_input({'queries': ['hotels in Paris'], 'checkInFrom': '2026-11-12', 'nights': 2, 'currency': 'USD'},
               date(2026, 10, 7))


def page(name):
    return gzip.decompress((FIX / name).read_bytes()).decode('utf-8')


def real_rows():
    rows = parse_search(page('search_paris_usd_p1.html.gz'))
    for r in rows:
        _fill(r, P, 'hotels in Paris')
    hotel, offers = parse_entity(page('entity_prices_usd.html.gz'), rows[0]['hotelId'])
    _fill(hotel, P, 'hotels in Paris')
    return rows + [_offer_row(hotel, o) for o in offers]


def check(rows, **kw):
    args = {'nights': 2, 'adults': 2, 'child_ages': [], 'currency': 'USD', 'check_in_dates': [date(2026, 11, 12)],
            'max_hotels': 50, **kw}
    return check_rows(rows, **args)


def test_every_saved_real_page_passes_all_rules():
    assert check(real_rows()) == []


@pytest.mark.parametrize(('mutate', 'expected'), [
    (lambda rows: rows + [rows[0]], 'duplicate hotel'),
    (lambda rows: [{**rows[0], 'nights': 3}] + rows[1:], 'is not check-out minus check-in'),
    (lambda rows: [{**rows[0], 'checkOutDate': '2026-11-15'}] + rows[1:], 'is not check-out minus check-in'),
    (lambda rows: [{**rows[0], 'currency': 'EUR'}] + rows[1:], 'differs from the input'),
    (lambda rows: [{**rows[0], 'adults': 3}] + rows[1:], 'adults 3'),
    (lambda rows: [{**rows[0], 'checkInDate': '2026-11-20', 'checkOutDate': '2026-11-22'}] + rows[1:], 'was not requested'),
    (lambda rows: [{**rows[0], 'pricePerNight': 0}] + rows[1:], 'not a positive number'),
    (lambda rows: [{**rows[0], 'rating': 7}] + rows[1:], 'rating 7'),
    (lambda rows: [{**rows[0], 'type': None}] + rows[1:], 'no valid type'),
])
def test_each_rule_catches_its_problem(mutate, expected):
    problems = check(mutate(real_rows()))
    assert any(expected in p for p in problems), problems


def test_cap_rule():
    assert any('above maxHotelsPerQuery' in p for p in check(real_rows(), max_hotels=5))


def test_canary_profile_and_compare():
    rows = real_rows()
    base = profile(rows, ['pricePerNight', 'hotelClass'], 'type')
    assert compare(base, base, 5) == []
    gone = [{**r, 'pricePerNight': None} for r in rows]
    assert any('pricePerNight' in d for d in compare(profile(gone, ['pricePerNight', 'hotelClass'], 'type'), base, 5))


def test_canary_alerts_when_hotels_per_search_drop_more_than_20_percent():
    rows = real_rows()
    base = profile(rows, ['pricePerNight'], 'type', 'query')
    hotels = [r for r in rows if r['type'] == 'hotel']
    offers = [r for r in rows if r['type'] == 'offer']
    keep_85 = hotels[: int(len(hotels) * 0.85) + 1] + offers  # a 15 percent drop: no alert
    assert not any('returned' in d for d in compare(profile(keep_85, ['pricePerNight'], 'type', 'query'), base, 5))
    keep_70 = hotels[: int(len(hotels) * 0.7)] + offers  # a 30 percent drop: alert
    drift = compare(profile(keep_70, ['pricePerNight'], 'type', 'query'), base, 100)
    assert any('query=hotels in Paris returned' in d for d in drift)


def test_fewer_missing_fields_is_not_drift():
    """Canary run J3MVimtp32Xg6WCHp: hotelClass empty for 15% of hotels against a 30% baseline was flagged as drift."""
    def rows(missing: int) -> list[dict]:
        return [{'pricePerNight': 100, 'hotelClass': None if i < missing else 4, 'type': 'hotel'} for i in range(20)]
    base = profile(rows(6), ['pricePerNight', 'hotelClass'], 'type')
    assert compare(profile(rows(3), ['pricePerNight', 'hotelClass'], 'type'), base, 15) == []
    assert compare(profile(rows(10), ['pricePerNight', 'hotelClass'], 'type'), base, 15) == ['null rate of hotelClass rose 30.0% -> 50.0%']
