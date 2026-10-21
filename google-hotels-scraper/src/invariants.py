"""Rules every delivered hotel and offer row must satisfy. Checked in the tests and at the end of every run."""

from __future__ import annotations

from collections import Counter
from datetime import date


def _d(value):
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def check_rows(rows: list[dict], *, nights: int, adults: int, child_ages: list[int], currency: str,
               check_in_dates: list[date], max_hotels: int | None) -> list[str]:
    problems: list[str] = []
    hotels = [r for r in rows if r.get('type') == 'hotel']
    offers = [r for r in rows if r.get('type') == 'offer']
    if len(hotels) + len(offers) != len(rows):
        problems.append(f'{len(rows) - len(hotels) - len(offers)} rows have no valid type')

    keys = Counter((r.get('hotelId'), r.get('checkInDate')) for r in hotels)
    for key, n in keys.items():
        if n > 1:
            problems.append(f'duplicate hotel {key[0]} for check-in {key[1]} appears {n} times')
    okeys = Counter((r.get('hotelId'), r.get('checkInDate'), r.get('provider')) for r in offers)
    for key, n in okeys.items():
        if n > 1:
            problems.append(f'duplicate offer {key} appears {n} times')
    if max_hotels is not None:
        for key, n in Counter((r.get('query'), r.get('checkInDate')) for r in hotels if r.get('query')).items():
            if n > max_hotels:
                problems.append(f'{key} has {n} hotels, above maxHotelsPerQuery {max_hotels}')

    allowed = {d.isoformat() for d in check_in_dates}
    for r in rows:
        rid = f'{r.get("type")}/{r.get("hotelId")}/{r.get("checkInDate")}' + (f'/{r["provider"]}' if r.get('provider') else '')
        ci, co = _d(r.get('checkInDate')), _d(r.get('checkOutDate'))
        if ci is None or co is None:
            problems.append(f'{rid}: check-in or check-out is not a date')
        else:
            if (co - ci).days != r.get('nights'):
                problems.append(f'{rid}: nights {r.get("nights")} is not check-out minus check-in ({(co - ci).days})')
            if r.get('checkInDate') not in allowed:
                problems.append(f'{rid}: check-in {r.get("checkInDate")} was not requested')
        if r.get('nights') != nights:
            problems.append(f'{rid}: nights {r.get("nights")} differs from the input {nights}')
        if r.get('adults') != adults:
            problems.append(f'{rid}: adults {r.get("adults")} differs from the input {adults}')
        if sorted(r.get('childAges') or []) != sorted(child_ages):
            problems.append(f'{rid}: childAges {r.get("childAges")} differ from the input {child_ages}')
        if r.get('currency') != currency:
            problems.append(f'{rid}: currency {r.get("currency")!r} differs from the input {currency!r}')
        price = r.get('pricePerNight')
        if r.get('type') == 'offer' or r.get('available'):
            if not isinstance(price, (int, float)) or price <= 0:
                problems.append(f'{rid}: pricePerNight {price!r} is not a positive number')
        if r.get('type') == 'hotel':
            if r.get('rating') is not None and not 0 < r['rating'] <= 5:
                problems.append(f'{rid}: rating {r["rating"]} is not 0 to 5')
            if r.get('hotelClass') is not None and r['hotelClass'] not in (1, 2, 3, 4, 5):
                problems.append(f'{rid}: hotelClass {r["hotelClass"]} is not 1 to 5')
            if not str(r.get('url', '')).startswith('https://www.google.com/travel/hotels/entity/'):
                problems.append(f'{rid}: url {r.get("url")!r} is not a Google Hotels link')
    return problems
