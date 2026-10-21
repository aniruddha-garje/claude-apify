"""Google Hotels scraper: prices across a date calendar, OTA offers and rate parity, with plain HTTP."""

from __future__ import annotations

import difflib
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from apify import Actor
from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import HttpCrawler, HttpCrawlingContext

from .delivery import Delivery
from .invariants import check_rows
from .metrics import AttemptStats
from .run_checks import run_checks
from .parsers import (
    AMENITY_IDS,
    CONSENT_HEADERS,
    REQUIRED_FIELDS,
    build_ts,
    entity_id_from_url,
    entity_url,
    is_blocked,
    is_consent_page,
    parse_entity,
    parse_search,
    search_place,
    search_url,
)

PLACE_CACHE_STORE = 'google-hotels-place-cache'
PAGE_STEP = 18  # Google's own Next button advances the offset by 18
MAX_OFFSET = 360
PLACE_RETRIES = 3
# Named-hotel mode (hotelUrls) is off for launch: at $1.99 per 1,000 it costs more than it earns on residential proxy
# (8 Oct 2026, reports/pricing/hotels.md). The code is kept; HOTELS_NAMED_MODE=1 switches it back on.
NAMED_MODE_FLAG = 'HOTELS_NAMED_MODE'
NAMED_MODE_OFF = ('Named-hotel tracking (hotelUrls) is coming soon and is switched off for now, so this run stopped '
                  'before any request and cost nothing. Use a search instead, for example queries ["hotels in Paris"] '
                  'with checkInFromDaysAhead 7.')


def named_mode_enabled() -> bool:
    return os.environ.get(NAMED_MODE_FLAG) == '1'
DEFAULT_CHILD_AGE = 8


class InputError(ValueError):
    pass


class BlockedError(RuntimeError):
    pass


def _parse_date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise InputError(f'{name} must be a date like 2026-11-12, got "{value}". Example: 2026-11-12.') from exc


def _days_ahead(cfg: dict[str, Any], name: str) -> int | None:
    value = cfg.get(name)
    if value is None or value == '':
        return None
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise InputError(f'{name} must be a whole number of days from the run date, got "{value}". Example: 7.') from exc
    if not 0 <= days <= 365:
        raise InputError(f'{name} must be between 0 (today) and 365, got {days}. Example: 7.')
    return days


def read_input(cfg: dict[str, Any], today: date) -> dict[str, Any]:
    """Validate everything before any request is made, so a bad input never costs anything."""
    queries = [q.strip() for q in cfg.get('queries') or [] if str(q).strip()]
    hotel_urls = [u.get('url') if isinstance(u, dict) else u for u in cfg.get('hotelUrls') or [] if u]
    if hotel_urls and not named_mode_enabled():
        raise InputError(NAMED_MODE_OFF)
    entities = []
    for url in hotel_urls:
        eid = entity_id_from_url(str(url))
        if not eid:
            raise InputError(f'"{url}" is not a Google Hotels hotel link. It should contain /travel/hotels/entity/, for example '
                             'https://www.google.com/travel/hotels/entity/ChkI-6KAqfDmrMXQARoNL2cvMTFiN3J3NnQ0aBAB')
        entities.append(eid)
    if not queries and not entities:
        raise InputError('Add at least one search. Example: queries ["hotels in Paris"].')

    from_ahead, to_ahead = _days_ahead(cfg, 'checkInFromDaysAhead'), _days_ahead(cfg, 'checkInToDaysAhead')
    if from_ahead is not None and cfg.get('checkInFrom'):
        raise InputError('Use either checkInFrom or checkInFromDaysAhead, not both. '
                         'For a scheduled run, keep only checkInFromDaysAhead, for example 7.')
    if to_ahead is not None and cfg.get('checkInTo'):
        raise InputError('Use either checkInTo or checkInToDaysAhead, not both. For example checkInToDaysAhead: 30.')
    if from_ahead is not None:
        check_in_from = today + timedelta(days=from_ahead)
    else:
        check_in_from = _parse_date(cfg.get('checkInFrom') or (today + timedelta(days=1)).isoformat(), 'checkInFrom')
    if to_ahead is not None:
        check_in_to = today + timedelta(days=to_ahead)
    else:
        check_in_to = _parse_date(cfg.get('checkInTo') or check_in_from.isoformat(), 'checkInTo')
    if check_in_from < today:
        raise InputError(f'checkInFrom {check_in_from} is in the past. Google only prices future stays; use {today} or '
                         f'later, for example {today + timedelta(days=7)}. For scheduled runs, use '
                         'checkInFromDaysAhead (for example 7) so the date moves forward with every run.')
    if check_in_to < check_in_from:
        raise InputError(f'The last check-in date ({check_in_to}) is before the first ({check_in_from}). '
                         f'Example: checkInFrom {check_in_from}, checkInTo {check_in_from + timedelta(days=6)}.')
    if (check_in_to - check_in_from).days > 365:
        raise InputError(f'The check-in range can span at most 366 days; {check_in_from} to {check_in_to} is longer. '
                         f'Example: checkInFrom {check_in_from}, checkInTo {check_in_from + timedelta(days=30)}.')
    nights = int(cfg.get('nights') or 1)
    if not 1 <= nights <= 30:
        raise InputError(f'nights must be between 1 and 30, got {nights}. Example: 2.')
    adults = int(cfg.get('adults') if cfg.get('adults') is not None else 2)
    if not 1 <= adults <= 12:
        raise InputError(f'adults must be between 1 and 12, got {adults}. Example: 2.')
    child_ages = [int(a) for a in cfg.get('childrenAges') or []]
    children = int(cfg.get('children') or 0)
    if children < len(child_ages):
        children = len(child_ages)
    child_ages += [DEFAULT_CHILD_AGE] * (children - len(child_ages))
    if any(not 0 <= a <= 17 for a in child_ages):
        raise InputError(f'childrenAges must each be between 0 and 17, got {child_ages}. Example: [4, 9].')
    currency = str(cfg.get('currency') or 'USD').upper()
    if len(currency) != 3 or not currency.isalpha():
        raise InputError(f'currency must be a three-letter ISO 4217 code, got "{currency}". Example: USD, EUR or IDR.')
    country = str(cfg.get('country') or 'US').upper()
    if len(country) != 2 or not country.isalpha():
        raise InputError(f'country must be a two-letter country code, got "{country}". Example: US, GB or IN.')

    filters = cfg.get('filters') or {}
    classes = sorted({int(c) for c in filters.get('hotelClass') or []})
    if any(c not in (2, 3, 4, 5) for c in classes):
        raise InputError(f'filters.hotelClass accepts 2, 3, 4 and 5 (Google offers no 1-star filter), got {classes}. '
                         'Example: [4, 5].')
    unknown = [a for a in filters.get('amenities') or [] if a not in AMENITY_IDS]
    if unknown:
        raise InputError(f'Unknown amenities {unknown}. Use any of: {", ".join(AMENITY_IDS)}. Example: ["freeWifi", "pool"].')
    if filters.get('freeCancellation'):
        raise InputError(
            'filters.freeCancellation is not available: Google Hotels does not expose a free-cancellation filter '
            'to logged-out visitors (checked 7 Oct 2026). Remove it, for example filters {"hotelClass": [4, 5]}.'
        )

    dates = [check_in_from + timedelta(days=i) for i in range((check_in_to - check_in_from).days + 1)]
    return {
        'queries': queries,
        'entities': entities,
        'dates': dates,
        'nights': nights,
        'adults': adults,
        'childAges': child_ages,
        'currency': currency,
        'country': country,
        'language': cfg.get('language') or 'en',
        'classes': classes,
        'amenityIds': [AMENITY_IDS[a] for a in filters.get('amenities') or []],
        'minRating': filters.get('minRating'),
        'priceMin': filters.get('priceMin'),
        'priceMax': filters.get('priceMax'),
        'includeOffers': bool(cfg.get('includeOffers', False)),
        'includeUnavailable': bool(cfg.get('includeUnavailable', False)),
        'maxHotels': _max_hotels(cfg),
    }


def _max_hotels(cfg: dict[str, Any]) -> int:
    value = cfg.get('maxHotelsPerQuery')
    n = 50 if value in (None, '') else int(value)
    if not 1 <= n <= 360:
        raise InputError(f'maxHotelsPerQuery must be between 1 and 360, got {n}. Example: 50.')
    return n


_STOP = {'hotel', 'hotels', 'in', 'near', 'the', 'and', 'of', 'at', 'for', 'best', 'cheap', 'resort', 'resorts'}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r'\w+', text.lower()) if w not in _STOP and len(w) > 1}


def place_matches(query: str, place_name: str) -> bool:
    """True when the place Google resolved shares a word (allowing small spelling differences, such as Roma and
    Rome) with the search. Used only to warn, never to drop results, so a false alarm costs nothing."""
    q, pl = _words(query), _words(place_name)
    if not q or not pl:
        return True
    return any(a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.75 for a in q for b in pl)


def utc_today() -> date:
    """Today's date when the run starts. Every default date is computed from it, so nothing in the input goes stale."""
    return datetime.now(timezone.utc).date()


def resolve_input(cfg: dict[str, Any]) -> dict[str, Any]:
    return read_input(cfg, utc_today())


def keep(row: dict, p: dict) -> bool:
    if not row['available'] and not p['includeUnavailable']:
        return False
    if p['minRating'] is not None and (row['rating'] or 0) < float(p['minRating']):
        return False
    price = row['pricePerNight']
    if price is not None:
        if p['priceMin'] is not None and price < float(p['priceMin']):
            return False
        if p['priceMax'] is not None and price > float(p['priceMax']):
            return False
    return True


async def main() -> None:
    async with Actor:
        cfg = await Actor.get_input() or {}
        try:
            p = resolve_input(cfg)
        except InputError as exc:
            Actor.log.error(str(exc))
            await Actor.fail(status_message=str(exc))
            return

        hotels = Delivery('hotel', row_id=lambda r: '|'.join(str(r[k]) for k in (
            'hotelId', 'checkInDate', 'nights', 'adults', 'childAges', 'currency')))
        offers = Delivery('offer', row_id=lambda r: '|'.join(str(r[k]) for k in (
            'hotelId', 'checkInDate', 'nights', 'adults', 'childAges', 'currency', 'provider')))
        await hotels.load()
        await offers.load()
        stats = (await Actor.get_value('RUN_STATS')) or {'pages': 0, 'blocked': 0, 'hotelsSeen': 0}

        requests: list[Request] = []
        def stay_ts(d: date, place: tuple[str, str] | None = None) -> str:
            return build_ts(d, d + timedelta(days=p['nights']), p['adults'], p['childAges'], p['currency'],
                            p['classes'], p['amenityIds'], place)

        def search_request(q: str, d: date, place, offset: int = 0) -> Request:
            ts = stay_ts(d, place)
            data = {'kind': 'search', 'query': q, 'ts': ts, 'offset': offset, 'key': f'q|{q}|{d}',
                    'checkIn': d.isoformat(), 'place': list(place) if place else None}
            url = search_url(q, ts, offset, p['language'], p['country'])
            # Its own unique key: when Google resolves no place, this URL equals the resolution page's URL and would
            # otherwise be dropped as a duplicate request.
            return Request.from_url(url, user_data=data, unique_key=f'search|{url}', headers=CONSENT_HEADERS)

        # Each search is first resolved to Google's place (one page), then every date is searched with that place
        # in the request, exactly as Google's own page does after the first load.
        # Resolved places are cached in a named key-value store on the user's account, so repeat and scheduled runs
        # skip the resolution page. Places (a city's id) do not change, so a cached value stays valid.
        place_store = await Actor.open_key_value_store(name=PLACE_CACHE_STORE)
        place_cache: dict = await place_store.get_value('PLACES') or {}

        def cache_key(q: str) -> str:
            return f'{q.strip().lower()}|{p["language"]}|{p["country"]}'

        for q in p['queries']:
            cached = place_cache.get(cache_key(q))
            if cached:
                stats.setdefault('places', {})[q] = cached
                requests += [search_request(q, day, tuple(cached)) for day in p['dates']]
                continue
            first = p['dates'][0]
            data = {'kind': 'resolve', 'query': q, 'checkIn': first.isoformat(), 'key': f'r|{q}'}
            requests.append(Request.from_url(search_url(q, stay_ts(first), 0, p['language'], p['country']),
                                             user_data=data, headers=CONSENT_HEADERS))
        for d in p['dates']:
            ts = stay_ts(d)
            for eid in p['entities']:
                data = {'kind': 'entity', 'entity': eid, 'key': f'e|{eid}|{d}', 'checkIn': d.isoformat(), 'query': None}
                requests.append(Request.from_url(entity_url(eid, ts, p['language'], p['country']), user_data=data,
                                                 headers=CONSENT_HEADERS))

        proxy_input = dict(cfg.get('proxyConfiguration') or {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL']})
        if proxy_input.get('useApifyProxy') and not proxy_input.get('apifyProxyCountry'):
            proxy_input['apifyProxyCountry'] = p['country']  # Google prices follow the requester's country
        proxy = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)

        crawler = HttpCrawler(
            proxy_configuration=proxy,
            max_request_retries=8,
            concurrency_settings=ConcurrencySettings(max_concurrency=10, desired_concurrency=5),
            additional_http_error_status_codes=[429],
        )

        attempts = AttemptStats()
        attempts.attach(crawler)

        @crawler.router.default_handler
        async def handle(ctx: HttpCrawlingContext) -> None:
            d = ctx.request.user_data
            if hotels.budget_reached or (d.get('offersOnly') and offers.budget_reached):
                return  # the user's spending limit is reached; skip without fetching more
            html = (await ctx.http_response.read()).decode('utf-8', errors='replace')
            stats['pages'] += 1
            if is_blocked(html):
                stats['blocked'] += 1
                if ctx.session:
                    ctx.session.retire()
                raise BlockedError('Google answered with a rate-limit page; retrying on a new IP')

            if d['kind'] == 'entity':
                hotel, hotel_offers = parse_entity(html, d['entity'])
                if hotel is None:
                    raise BlockedError('Hotel page had no price data; retrying')
                _fill(hotel, p, d['query'], None)
                if not d.get('offersOnly') and keep(hotel, p):
                    await _deliver_hotels(hotels, [hotel], d['key'], None)
                if p['includeOffers'] and hotel_offers:
                    await offers.deliver([_offer_row(hotel, o) for o in hotel_offers], d['key'], None)
                return

            if d['kind'] == 'resolve':
                if not parse_search(html):
                    await _save_debug_page(html, 'resolve')
                    if is_consent_page(html):
                        stats['consent'] = stats.get('consent', 0) + 1
                        raise BlockedError('Google sent its cookie-consent page instead of results; retrying on a new IP')
                    raise BlockedError('Search page had no hotel data (unexpected page); retrying on a new IP')
                place = search_place(html)
                if not place and ctx.request.retry_count < PLACE_RETRIES:
                    # Google sometimes sends a page without its place data for a real city (seen for "hotels in
                    # Prague" on 8 Oct 2026), so a missing place is retried on a new IP before the search is skipped.
                    if ctx.session:
                        ctx.session.retire()
                    raise BlockedError('Search page had no place data; retrying on a new IP')
                stats.setdefault('places', {})[d['query']] = list(place) if place else None
                if not place:
                    # Google did not recognise the place. Its fallback results are for an unrelated location (for
                    # example the proxy's own city), so nothing is delivered or charged for this search.
                    stats.setdefault('unrecognisedQueries', []).append(d['query'])
                    Actor.log.warning(f'"{d["query"]}" was not recognised by Google as a place or hotel, so it was skipped '
                                      'and not charged. Check the spelling, or add a region, for example '
                                      '"hotels in Springfield, Massachusetts".')
                    return
                if place_matches(d['query'], place[1]):
                    ctx.log.info(f'"{d["query"]}" resolves to {place[1]} ({place[0]}). If you meant a different '
                                 f'{place[1]}, add the region or country to the search.')
                else:
                    stats.setdefault('placeMismatches', {})[d['query']] = place[1]
                    Actor.log.warning(f'Google read "{d["query"]}" as "{place[1]}", which shares no word with your '
                                      'search. Every row carries resolvedPlace; check it before using the results.')
                place_cache[cache_key(d['query'])] = list(place)
                await place_store.set_value('PLACES', place_cache)
                await ctx.add_requests([search_request(d['query'], day, place) for day in p['dates']])
                return

            rows = parse_search(html)
            if not rows and d['offset'] == 0:
                await _save_debug_page(html, 'search')
                raise BlockedError('Search page had no hotel data (unexpected page); retrying on a new IP')
            stats['hotelsSeen'] += len(rows)
            for row in rows:
                _fill(row, p, d['query'], d['place'][1] if d.get('place') else None)
            kept = [r for r in rows if keep(r, p)]
            before = hotels.counts.get(d['key'], 0)
            delivered = await _deliver_hotels(hotels, kept, d['key'], p['maxHotels'])
            added = hotels.counts.get(d['key'], 0) - before
            ctx.log.info(f'{d["query"]} {d["checkIn"]} offset {d["offset"]}: {len(rows)} hotels, {added} new')

            if p['includeOffers']:
                # Offers only for hotels delivered to the user: before 9 Oct 2026 every hotel on the page was fetched
                # and its offers charged, even past maxHotelsPerQuery (run 3BgiBhR7DZifI3D2P: 2 hotels, 478 offers).
                for row in delivered:
                    if row['hotelId']:
                        data = {'kind': 'entity', 'entity': row['hotelId'], 'key': d['key'], 'checkIn': d['checkIn'],
                                'query': d['query'], 'offersOnly': True}
                        await ctx.add_requests([Request.from_url(
                            entity_url(row['hotelId'], d['ts'], p['language'], p['country']), user_data=data,
                            headers=CONSENT_HEADERS)])

            room = hotels.remaining(d['key'], p['maxHotels'])
            if rows and room != 0 and d['offset'] + PAGE_STEP <= MAX_OFFSET and not hotels.budget_reached:
                place = tuple(d['place']) if d.get('place') else None
                await ctx.add_requests([search_request(d['query'], date.fromisoformat(d['checkIn']), place,
                                                       d['offset'] + PAGE_STEP)])
            await Actor.set_value('RUN_STATS', stats)

        @crawler.failed_request_handler
        async def failed(ctx: HttpCrawlingContext, error: Exception) -> None:
            d = ctx.request.user_data
            Actor.log.error(f'Gave up on {d.get("key")} (offset {d.get("offset")}) after retries: {error}')
            hotels.record_error(d.get('key', ''), ctx.request.url, str(error))
            await hotels.save()

        final = await crawler.run(requests)
        await hotels.save()
        await offers.save()
        stats.update({'hotelsDelivered': hotels.charged, 'hotelsWithoutPriceNotCharged': hotels.free,
                      'offersDelivered': offers.charged, 'errors': hotels.errors,
                      'requests': attempts.summary(final)})
        await Actor.set_value('RUN_STATS', stats)
        Actor.log.info(f'Delivered {hotels.charged} hotel rows and {offers.charged} offers; '
                       f'{stats["pages"]} pages, {stats["blocked"]} blocked responses, {len(hotels.errors)} failed requests.')
        if hotels.budget_reached or offers.budget_reached:
            Actor.log.warning('Stopped early because the maximum cost you set for this run was reached.')
        skipped = stats.get('unrecognisedQueries') or []
        mismatched = stats.get('placeMismatches') or {}
        notes = []
        if skipped:
            notes.append('Not recognised by Google, so skipped and not charged: '
                         + ', '.join(f'"{q}"' for q in skipped) + '.')
        if mismatched:
            notes.append('Google read ' + ', '.join(f'"{q}" as "{pl}"' for q, pl in mismatched.items())
                         + '; check resolvedPlace on those rows.')
        if notes:
            await Actor.set_status_message(f'Finished with {hotels.charged} priced hotels. ' + ' '.join(notes))
        await run_checks(
            lambda rows: check_rows(rows, nights=p['nights'], adults=p['adults'], child_ages=p['childAges'],
                                    currency=p['currency'], check_in_dates=p['dates'], max_hotels=p['maxHotels']),
            canary=cfg.get('canary'),
            profile_fields=['pricePerNight', 'hotelClass', 'rating', 'reviewCount', 'latitude', 'website', 'description'],
            mix_field='type',
            count_by='query',  # hotels returned per search; the canary alerts on a drop of more than 20 percent
        )


_debug_saved = False


async def _save_debug_page(html: str, kind: str) -> None:
    """Keep the first unexpected page of a run (for diagnosing Google layout changes), once."""
    global _debug_saved
    if not _debug_saved:
        _debug_saved = True
        await Actor.set_value('DEBUG_UNEXPECTED_PAGE', html[:200000], content_type='text/html')
        Actor.log.warning(f'Unexpected {kind} page saved as DEBUG_UNEXPECTED_PAGE; retrying.')


async def _deliver_hotels(hotels: Delivery, rows: list[dict], key: str, cap: int | None) -> list[dict]:
    """Priced hotels are charged; hotels without a price (includeUnavailable) are delivered free.
    Returns the rows actually delivered by this call (within the cap and the spending limit, not duplicates)."""
    seen_before = set(hotels.seen)
    await hotels.deliver([r for r in rows if r['available']], key, cap)
    await hotels.deliver([r for r in rows if not r['available']], key, cap, charge=False)
    return [r for r in rows if hotels.row_id(r) in hotels.seen and hotels.row_id(r) not in seen_before]


def _fill(row: dict, p: dict, query: str | None, place: str | None = None) -> None:
    """Fields Google may omit for sold-out hotels; the values requested are echoed so every row is complete."""
    row['type'] = 'hotel'
    row['query'] = query
    row['resolvedPlace'] = place
    row['currency'] = row['currency'] or p['currency']
    row['country'] = p['country']


def _offer_row(hotel: dict, offer: dict) -> dict:
    return {
        'schemaVersion': hotel['schemaVersion'],
        'type': 'offer',
        'hotelId': hotel['hotelId'],
        'hotelName': hotel['name'],
        'url': hotel['url'],
        'checkInDate': hotel['checkInDate'],
        'checkOutDate': hotel['checkOutDate'],
        'nights': hotel['nights'],
        'adults': hotel['adults'],
        'childAges': hotel['childAges'],
        'currency': hotel['currency'],
        **offer,
    }


__all__ = ['REQUIRED_FIELDS', 'keep', 'main', 'read_input']
