"""Turn the raw browser captures in each Actor's tests/golden/captures/ into tests/golden/golden.json.

This only reformats what was read from the live pages (for example "₹12,538" becomes 12538). It never imports or
runs scraper code, so the expected values stay independent of the Actors. Run: python scripts/build_golden.py
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MONTHS = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6, 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Sept': 9,
          'Oct': 10, 'Nov': 11, 'Dec': 12}


def load(actor: str, name: str) -> dict:
    return json.loads((ROOT / actor / 'tests' / 'golden' / 'captures' / name).read_text(encoding='utf-8'))


def write(actor: str, golden: dict) -> None:
    path = ROOT / actor / 'tests' / 'golden' / 'golden.json'
    path.write_text(json.dumps(golden, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'{path.relative_to(ROOT)}: {len(golden["items"])} items')


def reviews() -> None:
    items = []
    apps: set[str] = set()
    countries: set[str] = set()
    folder = ROOT / 'app-store-play-reviews-scraper' / 'tests' / 'golden' / 'captures'
    for path in sorted(folder.glob('*.json')):
        cap = load('app-store-play-reviews-scraper', path.name)
        if cap['items']:
            apps.add(cap['appId'])
            countries.add(cap['country'])
        for it in cap['items']:
            if cap['store'] == 'appStore':
                date = it['dateTime'][:10]  # the page's own machine-readable UTC timestamp
                date_basis = 'UTC date from the page <time datetime> attribute'
            else:
                date = datetime.strptime(it['dateText'], '%B %d, %Y').date().isoformat()
                date_basis = 'date as displayed by Google Play, which is the UTC date'
            items.append({
                'store': cap['store'], 'appId': cap['appId'], 'country': cap['country'], 'reviewId': it['reviewId'],
                'expected': {'rating': it['rating'], 'date': date, 'text': it['text'], 'title': it.get('title')},
                'dateBasis': date_basis, 'sourceUrl': cap['url'], 'capturedAt': cap['capturedAt'],
            })
    write('app-store-play-reviews-scraper', {
        'description': 'Reviews read from the live App Store and Google Play web pages by Playwright.',
        'auditInput': {'apps': sorted(apps), 'countries': sorted(countries), 'languages': ['en'],
                       'sort': 'mostHelpful', 'maxReviewsPerApp': 500, 'proxyConfiguration': {'useApifyProxy': True}},
        'compare': {'text': 'whitespace removed on both sides (the web page drops line breaks)', 'title': 'exact',
                    'rating': 'exact', 'date': 'calendar date, see dateBasis'},
        'items': items,
    })


def hotels() -> None:
    cap = load('google-hotels-scraper', 'browser_captures.json')
    items = []
    stays: dict[tuple, list[str]] = {}
    for city in cap['captures']:
        ci = datetime.fromisoformat(city['checkIn']).date()
        co = datetime.fromisoformat(city['checkOut']).date()
        stays.setdefault((ci.isoformat(), (co - ci).days), []).append(f'hotels in {city["city"]}')
        for card in city['cards']:
            items.append({
                'hotelId': card['hotelId'], 'city': city['city'],
                'stay': {'checkInDate': ci.isoformat(), 'checkOutDate': co.isoformat(), 'nights': (co - ci).days,
                         'adults': 2, 'children': 0, 'country': 'IN', 'currency': 'INR'},
                'expected': {'name': card['name'],
                             'pricePerNight': int(re.sub(r'[^\d]', '', card['priceText'])) if card['priceText'] else None,
                             'priceText': card['priceText'], 'available': bool(card['priceText'])},
                'sourceUrl': city['url'], 'capturedAt': city['capturedAt'],
            })
    write('google-hotels-scraper', {
        'description': 'Hotels and nightly prices read from the live Google Hotels page by Playwright in India.',
        'captureMethod': cap['method'],
        'auditInputs': [{'queries': queries, 'checkInFrom': ci, 'checkInTo': ci, 'nights': n, 'adults': 2, 'children': 0,
                         'currency': 'INR', 'country': 'IN', 'maxHotelsPerQuery': 300, 'includeUnavailable': True,
                         'proxyConfiguration': {'useApifyProxy': True, 'apifyProxyGroups': ['RESIDENTIAL']}}
                        for (ci, n), queries in stays.items()],
        'compare': {'name': 'exact', 'stay fields': 'exact', 'available': 'exact (a price shown or not)',
                    'pricePerNight': 'within 10 percent, for hotels that showed a price'},
        'items': items,
    })


def ads() -> None:
    cap = load('google-ads-transparency-scraper', 'browser_captures.json')
    items = []
    for adv in cap['advertisers']:
        for ad in adv['ads']:
            d, m, y = ad['lastShownText'].split()
            items.append({
                'advertiserId': adv['advertiserId'], 'creativeId': ad['creativeId'],
                'expected': {'advertiserName': adv['advertiserName'], 'format': ad['format'].lower(),
                             'lastShownDate': f'{int(y):04d}-{MONTHS[m]:02d}-{int(d):02d}'},
                'sourceUrl': f'https://adstransparency.google.com/advertiser/{adv["advertiserId"]}/creative/{ad["creativeId"]}?region=anywhere',
                'capturedAt': ad['capturedAt'],
            })
    write('google-ads-transparency-scraper', {
        'description': 'Ads read from the live Ads Transparency Center pages by Playwright.',
        # The Transparency Center lists an advertiser's ads newest first, as the Actor does, so 30 per advertiser
        # covers the 13 captured from the top of each list without paying for thousands of ads from big brands.
        'auditInput': {'advertiserIds': [a['advertiserId'] for a in cap['advertisers']], 'region': 'anywhere',
                       'dateFrom': '2020-01-01', 'maxAdsPerAdvertiser': 30, 'includeCreativeContent': True,
                       'proxyConfiguration': {'useApifyProxy': True}},
        'compare': {'advertiserName': 'exact', 'format': 'exact', 'lastShownDate': 'calendar date as displayed'},
        'items': items,
    })


if __name__ == '__main__':
    import sys
    chosen = sys.argv[1:] or ['reviews', 'hotels', 'ads']
    for name in chosen:
        {'reviews': reviews, 'hotels': hotels, 'ads': ads}[name]()
    print('built at', datetime.now(timezone.utc).isoformat())
