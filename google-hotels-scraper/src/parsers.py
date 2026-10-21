"""Pure helpers for Google Hotels: build search URLs and parse the JSON Google embeds in the HTML.

No network I/O happens here, so every function is tested against saved pages.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import date
from typing import Any
from urllib.parse import urlencode, urlparse

SCHEMA_VERSION = 1
HOTEL_KEY = '"397419284":'  # key of each hotel record in the embedded data (stable across pages, 7 Oct 2026)
_DS_RE = re.compile(r"AF_initDataCallback\(\{key: '(ds:\d+)'.*?data:", re.S)
_ENTITY_RE = re.compile(r'/entity/([A-Za-z0-9_\-]+)')

# Filter ids Google's own "All filters" panel puts into `ts` (read from the UI on 7 Oct 2026).
AMENITY_IDS = {
    'freeWifi': 35, 'freeBreakfast': 9, 'restaurant': 8, 'bar': 15, 'kidFriendly': 12, 'petFriendly': 19,
    'freeParking': 1, 'parking': 3, 'evCharger': 61, 'roomService': 22, 'fitnessCenter': 7, 'spa': 10,
    'pool': 6, 'indoorPool': 4, 'outdoorPool': 5, 'airConditioned': 40, 'wheelchairAccessible': 53,
    'beachAccess': 11, 'allInclusive': 52,
}

# ---------------------------------------------------------------- protobuf for the `ts` / `qs` URL params


def _varint(n: int) -> bytes:
    out = b''
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def _fv(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _fm(field: int, payload: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(payload)) + payload


def _date(d: date) -> bytes:
    return _fv(1, d.year) + _fv(2, d.month) + _fv(3, d.day)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


def build_ts(
    check_in: date,
    check_out: date,
    adults: int,
    child_ages: list[int],
    currency: str,
    hotel_classes: list[int] | None = None,
    amenity_ids: list[int] | None = None,
    place: tuple[str, str] | None = None,
) -> str:
    """Encode stay dates, occupancy, currency and server-side filters the way Google's own UI does.

    `place` is (place id, place name) as Google resolved the search, for example ('0x132f...:0xb90f...', 'Rome').
    Google's own page always sends it once it knows the place; without it the result list can differ.
    """
    guests = b''.join(_fm(1, _fv(1, 3)) for _ in range(adults))
    guests += b''.join(_fm(1, _fv(1, 2) + _fv(2, age)) for age in child_ages)
    guests += _fv(2, 1 if child_ages else 0)
    nights = (check_out - check_in).days
    dates = _fm(1, _date(check_in)) + _fm(2, _date(check_out)) + _fv(3, nights)
    prefs = b''
    if amenity_ids:
        prefs += _fm(1, b''.join(_varint(a) for a in amenity_ids))
    if hotel_classes:
        prefs += _fm(2, b''.join(_varint(c) for c in hotel_classes))
    prefs += _fm(7, currency.upper().encode())
    prefs += _fm(12, _fv(1, 1))  # flags Google's own page always sends (field meanings not public)
    location = b''
    if place:
        location = _fm(1, _fm(2, _fm(6, place[0].encode()) + _fm(7, place[1].encode())) + _fm(3, b''))
    stay = _fm(2, dates) + _fm(6, _fv(1, 1))
    root = _fv(1, 1) + _fm(2, guests) + _fm(3, location + _fm(2, stay)) + _fm(5, _fm(1, prefs) + _fm(3, b''))
    return _b64(root)


def build_qs(offset: int) -> str:
    """The `qs` parameter Google's own page sends.

    First page: CAE4DQ, which Google's page adds once it has loaded; without it the result list differs from what a
    visitor sees (checked 8 Oct 2026). Later pages: Google's own Next button sends offset 18, 36, ... in this shape.
    """
    if offset <= 0:
        return _b64(_fv(1, 1) + _fv(7, 13))
    token = base64.b64encode(_fv(1, offset)).decode()
    return _b64(_fv(1, 0) + _fm(2, token.encode()) + _fv(7, 13) + _fv(9, 0))


def search_url(query: str, ts: str, offset: int, language: str, country: str) -> str:
    params = {'q': query, 'hl': language, 'gl': country.lower(), 'ts': ts}
    params['qs'] = build_qs(offset)
    return 'https://www.google.com/travel/search?' + urlencode(params)


def entity_id_from_url(url: str) -> str | None:
    match = _ENTITY_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def entity_url(entity_id: str, ts: str | None = None, language: str = 'en', country: str = 'us') -> str:
    params = {'hl': language, 'gl': country.lower()}
    if ts:
        params['ts'] = ts
    return f'https://www.google.com/travel/hotels/entity/{entity_id}/prices?' + urlencode(params)


def public_hotel_url(entity_id: str) -> str:
    return f'https://www.google.com/travel/hotels/entity/{entity_id}'


# ---------------------------------------------------------------- parsing


def _get(node: Any, *path: int) -> Any:
    for idx in path:
        if not isinstance(node, list) or idx >= len(node) or idx < 0:
            return None
        node = node[idx]
    return node


def _iso(d: Any) -> str | None:
    if isinstance(d, list) and len(d) >= 3 and all(isinstance(x, int) for x in d[:3]):
        return f'{d[0]:04d}-{d[1]:02d}-{d[2]:02d}'
    return None


def is_blocked(html: str) -> bool:
    """Google's rate-limit or captcha page instead of results."""
    head = html[:5000].lower()
    return '/sorry/' in head or 'unusual traffic' in html[:20000].lower() or 'id="captcha-form"' in html


# Google answers addresses in the EU and nearby countries with its cookie-consent page instead of results: on 11 Oct
# 2026 a run for "hotels in Paris" with country FR got that page on every attempt and returned 0 hotels (run
# 9UqxeSsO6GL8i3b3n). This cookie carries the "Reject all" choice a visitor makes on that page, so Google shows the
# results and no tracking consent is given.
CONSENT_HEADERS = {'Cookie': 'SOCS=CAI'}


def is_consent_page(html: str) -> bool:
    """Google's cookie-consent page instead of results (result pages never link to consent.google)."""
    return 'consent.google' in html


def hotel_records(html: str) -> list[list]:
    """All hotel records embedded in a search page, in page order."""
    decoder = json.JSONDecoder()
    out = []
    pos = 0
    while True:
        idx = html.find(HOTEL_KEY, pos)
        if idx < 0:
            return out
        value, end = decoder.raw_decode(html, idx + len(HOTEL_KEY))
        out.append(value)
        pos = end


def parse_hotel(record: list) -> dict | None:
    h = _get(record, 0)
    if not isinstance(h, list) or not isinstance(_get(h, 1), str):
        return None
    entity_id = _get(h, 20)
    rate = _get(h, 6, 2) or []
    stay = _get(h, 6, 1, 4) or []
    occupancy = _get(h, 6, 1, 13) or []
    child_ages = [a[0] for a in (_get(occupancy, 1) or []) if isinstance(a, list) and a]
    nightly = _get(rate, 1, 2)
    total_display = _get(rate, 9, 0)
    hotel_class = _get(h, 3, 1)
    return {
        'schemaVersion': SCHEMA_VERSION,
        'hotelId': entity_id,
        'name': h[1],
        'url': public_hotel_url(entity_id) if entity_id else None,
        'hotelClass': hotel_class if isinstance(hotel_class, int) else None,
        'hotelClassText': _get(h, 3, 0),
        'rating': _get(h, 7, 0, 0),
        'reviewCount': _get(h, 7, 0, 1),
        'latitude': _get(h, 2, 0, 0),
        'longitude': _get(h, 2, 0, 1),
        'countryCode': _get(h, 2, 36),
        'description': _get(h, 11, 0),
        'website': _get(h, 2, 29, 2),
        'checkInTime': _get(h, 2, 17, 0),
        'checkOutTime': _get(h, 2, 17, 1),
        'checkInDate': _iso(_get(stay, 0)),
        'checkOutDate': _iso(_get(stay, 1)),
        'nights': _get(stay, 2),
        'adults': _get(occupancy, 0),
        'children': len(child_ages),
        'childAges': child_ages,
        'currency': _get(rate, 15),
        'pricePerNight': round(nightly, 2) if isinstance(nightly, (int, float)) else None,
        'pricePerNightText': _get(rate, 1, 0),
        'totalPriceText': total_display,
        'dealText': _get(rate, 7, 3, 3, 0),
        'available': isinstance(nightly, (int, float)),
    }


def search_place(html: str) -> tuple[str, str] | None:
    """The place Google resolved the search to, as (place id, name), read from the first hotel record."""
    for rec in hotel_records(html):
        node = _get(rec, 0, 6, 1, 18)
        if isinstance(node, list) and isinstance(_get(node, 2), str) and isinstance(_get(node, 1), str):
            return node[2], node[1]
    return None


def parse_search(html: str) -> list[dict]:
    rows = []
    for rec in hotel_records(html):
        row = parse_hotel(rec)
        if row:
            rows.append(row)
    return rows


def _ds_blocks(html: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    out = {}
    for m in _DS_RE.finditer(html):
        try:
            out[m.group(1)], _ = decoder.raw_decode(html, m.end())
        except json.JSONDecodeError:
            continue
    return out


def parse_entity(html: str, entity_id: str) -> tuple[dict | None, list[dict]]:
    """Parse a hotel's /prices page into (hotel row, offers) for exactly that hotel.

    The page also embeds nearby hotels, so the record is matched on its id, never on position.
    """
    for block in _ds_blocks(html).values():
        h = _get(block, 0)
        if isinstance(h, list) and isinstance(_get(h, 1), str) and _get(h, 20) == entity_id:
            hotel = parse_hotel(block)
            offers = []
            for item in _get(h, 6, 2, 21) or []:
                provider = _get(item, 0, 0)
                nightly = _get(item, 12, 4, 2)
                total = _get(item, 12, 5, 2)
                if not provider or not isinstance(nightly, (int, float)):
                    continue
                offers.append({
                    'provider': provider,
                    'pricePerNight': round(nightly, 2),
                    'totalPrice': round(total, 2) if isinstance(total, (int, float)) else None,
                    'pricePerNightText': _get(item, 12, 4, 0),
                    'totalPriceText': _get(item, 12, 5, 0),
                })
            return hotel, offers
    return None, []


REQUIRED_FIELDS = ('hotelId', 'name', 'url', 'checkInDate', 'checkOutDate', 'nights', 'adults', 'currency')
