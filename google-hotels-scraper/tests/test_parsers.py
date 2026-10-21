"""Hotels parser and input tests against real Google pages saved on 7 Oct 2026."""

import gzip
from datetime import date
from pathlib import Path

import pytest

from src.main import InputError, keep, read_input
from src.parsers import (
    CONSENT_HEADERS,
    REQUIRED_FIELDS,
    build_qs,
    build_ts,
    entity_id_from_url,
    is_blocked,
    is_consent_page,
    parse_entity,
    parse_search,
    search_place,
    search_url,
)

FIX = Path(__file__).parent / 'fixtures'
TODAY = date(2026, 10, 7)


def page(name: str) -> str:
    return gzip.decompress((FIX / name).read_bytes()).decode('utf-8')


def test_search_page_rows_are_complete_and_echo_the_stay():
    rows = parse_search(page('search_paris_usd_p1.html.gz'))
    assert len(rows) == 18
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert row[field] not in (None, ''), field
        assert (row['checkInDate'], row['checkOutDate'], row['nights']) == ('2026-11-12', '2026-11-14', 2)
        assert row['adults'] == 2 and row['children'] == 0
        assert row['currency'] == 'USD' and row['pricePerNight'] > 0
        assert row['url'].startswith('https://www.google.com/travel/hotels/entity/')
    assert len({r['hotelId'] for r in rows}) == 18


def test_second_page_is_different_hotels():
    p1 = {r['hotelId'] for r in parse_search(page('search_paris_usd_p1.html.gz'))}
    p2 = {r['hotelId'] for r in parse_search(page('search_paris_usd_p2.html.gz'))}
    assert len(p2) == 20 and len(p2 - p1) >= 15


def test_any_currency_children_and_class_filter():
    rows = parse_search(page('search_jakarta_idr_4star_wifi_family.html.gz'))
    assert rows and all(r['hotelClass'] == 4 for r in rows)
    assert all(r['children'] == 1 and r['childAges'] == [7] for r in rows)
    priced = [r for r in rows if r['available']]
    assert priced and all(r['currency'] == 'IDR' for r in priced)


def test_entity_page_offers_belong_to_the_requested_hotel():
    first = parse_search(page('search_paris_usd_p1.html.gz'))[0]
    hotel, offers = parse_entity(page('entity_prices_usd.html.gz'), first['hotelId'])
    assert hotel['hotelId'] == first['hotelId'] and hotel['name'] == first['name']
    assert len(offers) >= 5
    assert all(o['provider'] and o['pricePerNight'] > 0 for o in offers)
    assert len({o['provider'] for o in offers}) == len(offers)
    assert parse_entity(page('entity_prices_usd.html.gz'), 'not-this-hotel') == (None, [])


def test_old_inr_page_still_parses():
    rows = parse_search(page('search_hotels_in_paris_inr.html.gz'))
    assert len(rows) == 20 and all(r['currency'] == 'INR' for r in rows if r['available'])


def test_blocked_page_detection():
    assert is_blocked('<html><head></head><body><form id="captcha-form"></form></body></html>')
    assert not is_blocked(page('search_paris_usd_p1.html.gz'))


def test_ts_encodes_like_googles_ui():
    # Captured from Google's own UI for 22 to 24 Oct 2026, 2 adults (the location part is carried by `q` instead).
    ts = build_ts(date(2026, 10, 22), date(2026, 10, 24), 2, [], 'USD')
    assert ts.startswith('CAESCgoCCAMKAggDEAA')
    assert build_qs(0) == 'CAE4DQ' and build_qs(18) == 'CAASBENCST04DUgA'  # both as Google's page sends them
    assert 'qs=' in search_url('hotels in Rome', ts, 18, 'en', 'it')


def test_place_is_encoded_like_googles_own_url():
    # Google's own URL for Rome, 18 to 20 Oct 2026, 2 adults, INR (from the Playwright capture, 8 Oct 2026).
    google = ('CAESCgoCCAMKAggDEAAaTwoxEi0yJTB4MTMyZjYxOTZmOTkyOGViYjoweGI5MGY3NzA2OTM2NTZlMzg6BFJvbWUaABIaEhQKBwjqDxA'
              'KGBISBwjqDxAKGBQYAjICCAEqDQoJOgNJTlJiAggBGgA')
    ours = build_ts(date(2026, 10, 18), date(2026, 10, 20), 2, [], 'INR',
                    place=('0x132f6196f9928ebb:0xb90f770693656e38', 'Rome'))
    assert ours == google  # byte for byte the same request Google's own page makes


def test_search_place_comes_from_the_page():
    assert search_place(page('search_paris_usd_p1.html.gz'))[1] == 'Paris'


def test_entity_url_parsing():
    assert entity_id_from_url('https://www.google.com/travel/hotels/entity/ChkI7_q93r2s8vwRGg0vZy8xMWI2d20xeTBqEAE?q=x') == \
        'ChkI7_q93r2s8vwRGg0vZy8xMWI2d20xeTBqEAE'
    assert entity_id_from_url('https://www.google.com/maps') is None


def test_past_dates_are_rejected_before_the_run():
    with pytest.raises(InputError, match='in the past'):
        read_input({'queries': ['hotels in Paris'], 'checkInFrom': '2026-10-01'}, TODAY)


@pytest.mark.parametrize('cfg', [
    {},
    {'queries': ['x'], 'currency': 'DOLLARS'},
    {'queries': ['x'], 'filters': {'hotelClass': [1]}},
    {'queries': ['x'], 'filters': {'amenities': ['jacuzzi']}},
    {'queries': ['x'], 'filters': {'freeCancellation': True}},
    {'hotelUrls': ['https://example.com/hotel']},
    {'queries': ['x'], 'checkInFrom': '2026-11-10', 'checkInTo': '2026-11-01'},
])
def test_bad_inputs_fail_fast(cfg):
    with pytest.raises(InputError):
        read_input(cfg, TODAY)


def test_defaults_and_calendar():
    p = read_input({'queries': ['hotels in Paris'], 'checkInFrom': '2026-11-01', 'checkInTo': '2026-11-30',
                    'nights': 3, 'children': 2, 'childrenAges': [4], 'currency': 'idr'}, TODAY)
    assert len(p['dates']) == 30 and p['nights'] == 3
    assert p['childAges'] == [4, 8] and p['currency'] == 'IDR'


def test_keep_applies_price_rating_and_availability_filters():
    p = read_input({'queries': ['x'], 'filters': {'minRating': 4, 'priceMax': 100}}, TODAY)
    rows = parse_search(page('search_paris_usd_p1.html.gz'))
    kept = [r for r in rows if keep(r, p)]
    assert kept and all(r['rating'] >= 4 and r['pricePerNight'] <= 100 for r in kept)
    unpriced = {**rows[0], 'available': False, 'pricePerNight': None}
    assert not keep(unpriced, read_input({'queries': ['x']}, TODAY))
    assert keep(unpriced, read_input({'queries': ['x'], 'includeUnavailable': True}, TODAY))


def test_google_consent_page_is_recognised_and_has_no_hotels():
    """The real page a French residential IP got for "hotels in Paris" on 11 Oct 2026 (run 9UqxeSsO6GL8i3b3n)."""
    html = page('consent_page_fr_20261011.html.gz')
    assert is_consent_page(html)
    assert not parse_search(html)


def test_result_pages_are_not_mistaken_for_the_consent_page():
    for name in ('search_paris_usd_p1.html.gz', 'search_hotels_in_paris_inr.html.gz', 'entity_prices_usd.html.gz',
                 'search_jakarta_idr_4star_wifi_family.html.gz'):
        assert not is_consent_page(page(name)), name


def test_every_google_request_carries_the_reject_all_consent_cookie():
    import inspect

    import src.main as main
    src = inspect.getsource(main)
    assert CONSENT_HEADERS == {'Cookie': 'SOCS=CAI'}
    assert src.count('Request.from_url(') == src.count('headers=CONSENT_HEADERS')
