"""Pre-launch fixes (8 Oct 2026): relative check-in dates, unrecognised places, and free no-price rows."""

import gzip
import json
from datetime import date
from pathlib import Path

import pytest
from apify import Actor

from src.delivery import Delivery
from src.main import InputError, _deliver_hotels, place_matches, read_input
from src.parsers import parse_search, search_place

FIX = Path(__file__).parent / 'fixtures'
TODAY = date(2026, 10, 8)


def html(name: str) -> str:
    return gzip.decompress((FIX / name).read_bytes()).decode('utf-8')


# (a) relative check-in dates, so a scheduled run never goes stale

def test_days_ahead_moves_with_the_run_date():
    cfg = {'queries': ['hotels in Paris'], 'checkInFromDaysAhead': 7, 'checkInToDaysAhead': 9}
    assert read_input(cfg, TODAY)['dates'] == [date(2026, 10, 15), date(2026, 10, 16), date(2026, 10, 17)]
    assert read_input(cfg, date(2027, 1, 1))['dates'][0] == date(2027, 1, 8)  # the same input a quarter later


def test_days_ahead_zero_is_today_and_to_defaults_to_from():
    assert read_input({'queries': ['x'], 'checkInFromDaysAhead': 0}, TODAY)['dates'] == [TODAY]


@pytest.mark.parametrize('cfg, says', [
    ({'checkInFrom': '2026-10-20', 'checkInFromDaysAhead': 7}, 'not both'),
    ({'checkInTo': '2026-10-20', 'checkInToDaysAhead': 7}, 'not both'),
    ({'checkInFromDaysAhead': 400}, 'between 0 (today) and 365'),
    ({'checkInFromDaysAhead': 'soon'}, 'whole number'),
    ({'checkInFromDaysAhead': 10, 'checkInToDaysAhead': 5}, 'is before the first'),
])
def test_bad_offsets_are_rejected_with_an_example(cfg, says):
    with pytest.raises(InputError) as exc:
        read_input({'queries': ['hotels in Paris'], **cfg}, TODAY)
    assert says in str(exc.value) and 'xample' in str(exc.value)


def test_a_past_fixed_date_points_to_the_offset_field():
    with pytest.raises(InputError) as exc:
        read_input({'queries': ['x'], 'checkInFrom': '2026-10-01'}, TODAY)
    assert 'checkInFromDaysAhead' in str(exc.value) and '2026-10-15' in str(exc.value)


# (b) a search Google does not recognise

def test_unrecognised_search_falls_back_to_an_unrelated_place():
    page = html('search_unrecognised_qwzxvbnmplk.html.gz')  # real page for "hotels in Qwzxvbnmplk", 8 Oct 2026
    assert len(parse_search(page)) == 20  # Google still returns hotels...
    place = search_place(page)
    assert place[1] == 'United States'  # ...for a place unrelated to the search
    assert not place_matches('hotels in Qwzxvbnmplk', place[1])


def test_real_resolved_places_match_their_searches():
    assert place_matches('hotels in Paris', search_place(html('search_paris_usd_p1.html.gz'))[1])
    for query, place in [('hotels in Roma', 'Rome'), ('Le Bristol Paris', 'Le Bristol Paris'),
                         ('hotels near Eiffel Tower', 'Eiffel Tower'), ('Springfield Massachusetts hotels', 'Springfield')]:
        assert place_matches(query, place)  # places Google resolved on the platform on 8 Oct 2026


# (c) hotels without a price are delivered free

PRICING = {'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents': {
    'hotel': {'eventTitle': 'Hotel price', 'eventPriceUsd': 0.003},
    'apify-default-dataset-item': {'eventTitle': 'Dataset item', 'eventPriceUsd': 0},
}}}


@pytest.fixture
def ppe_env(monkeypatch):
    monkeypatch.setenv('ACTOR_TEST_PAY_PER_EVENT', 'true')
    monkeypatch.setenv('APIFY_ACTOR_PRICING_INFO', json.dumps(PRICING))
    monkeypatch.setenv('APIFY_CHARGED_ACTOR_EVENT_COUNTS', json.dumps({'hotel': 0, 'apify-default-dataset-item': 0}))
    monkeypatch.setenv('CRAWLEE_PURGE_ON_START', 'true')


async def test_rows_without_a_price_are_delivered_but_not_charged(ppe_env):
    rows = parse_search(html('search_jakarta_idr_4star_wifi_family.html.gz'))  # real page: 9 of 18 have no price
    assert sum(not r['available'] for r in rows) == 9 and all(r['pricePerNight'] is None for r in rows if not r['available'])
    async with Actor:
        hotels = Delivery('hotel', row_id=lambda r: r['hotelId'])
        await hotels.load()
        await _deliver_hotels(hotels, rows, 'k', cap=None)
        assert Actor.get_charging_manager().get_charged_event_count('hotel') == 9
        assert hotels.charged == 9 and hotels.free == 9
        assert (await (await Actor.open_dataset()).get_data()).total == 18


async def test_free_rows_still_count_toward_the_cap(ppe_env):
    rows = parse_search(html('search_jakarta_idr_4star_wifi_family.html.gz'))
    async with Actor:
        hotels = Delivery('hotel', row_id=lambda r: r['hotelId'])
        await hotels.load()
        await _deliver_hotels(hotels, rows, 'k', cap=12)
        assert hotels.counts['k'] == 12  # the 9 priced hotels first, then 3 without a price
        assert Actor.get_charging_manager().get_charged_event_count('hotel') == 9


# Named-hotel mode is off for launch (8 Oct 2026); its code stays behind the HOTELS_NAMED_MODE flag.

HOTEL_LINK = 'https://www.google.com/travel/hotels/entity/ChgInu3Dz7bByqnYARoLL2cvMXRnNmpmNXQQAQ'


def test_hotel_links_are_rejected_at_once_with_a_search_example(monkeypatch):
    monkeypatch.delenv('HOTELS_NAMED_MODE', raising=False)
    for cfg in ({'hotelUrls': [HOTEL_LINK]}, {'queries': ['hotels in Paris'], 'hotelUrls': [HOTEL_LINK]}):
        with pytest.raises(InputError) as exc:
            read_input(cfg, TODAY)
        msg = str(exc.value)
        assert 'coming soon' in msg and 'cost nothing' in msg and 'queries ["hotels in Paris"]' in msg


def test_an_empty_hotel_list_is_fine(monkeypatch):
    monkeypatch.delenv('HOTELS_NAMED_MODE', raising=False)
    assert read_input({'queries': ['hotels in Paris'], 'hotelUrls': []}, TODAY)['entities'] == []


def test_the_flag_switches_named_mode_back_on(monkeypatch):
    monkeypatch.setenv('HOTELS_NAMED_MODE', '1')
    assert read_input({'hotelUrls': [HOTEL_LINK]}, TODAY)['entities'] == ['ChgInu3Dz7bByqnYARoLL2cvMXRnNmpmNXQQAQ']


# Offers are requested only for hotels actually delivered (fix of 9 Oct 2026).

async def test_only_delivered_hotels_are_returned_for_offers(ppe_env):
    rows = parse_search(html('search_paris_usd_p1.html.gz'))  # real page: 18 priced hotels
    async with Actor:
        hotels = Delivery('hotel', row_id=lambda r: r['hotelId'])
        await hotels.load()
        first = await _deliver_hotels(hotels, rows, 'k', cap=2)
        assert [r['hotelId'] for r in first] == [r['hotelId'] for r in rows[:2]]  # only the 2 under the cap
        again = await _deliver_hotels(hotels, rows, 'k', cap=2)
        assert again == []  # cap reached: no hotel, so no offer request either
