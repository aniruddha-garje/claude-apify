"""Independent golden capture with Playwright: open the live sites in a real Chromium browser and read the rendered pages.

This never imports or runs scraper code. It reads what a person would see on screen (the DOM), the same way the
first golden sets were read by hand, and writes them to each Actor's tests/golden/captures/.

Usage: python scripts/pw_capture.py reviews|hotels|ads [--checkin-days 10] [--nights 2]
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
TZ = 'Asia/Kolkata'  # this computer's time zone; recorded in every capture


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


STAGING = 'staging'


def save(actor: str, name: str, data: dict) -> None:
    path = ROOT / actor / 'tests' / 'golden' / 'captures' / STAGING / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print('saved', path.relative_to(ROOT))


# ---------------------------------------------------------------- reviews

APPLE_JS = """() => [...document.querySelectorAll('li')]
  .filter(e => e.querySelector('[aria-labelledby^="review-"]') && e.querySelector('time'))
  .map(li => {
    const c = li.querySelector('[aria-labelledby^="review-"]');
    const t = li.querySelector('time');
    const p = [...li.querySelectorAll('p')].map(p => p.textContent.trim()).filter(Boolean);
    return {reviewId: c.getAttribute('aria-labelledby').match(/review-(\\d+)-title/)[1],
            rating: parseInt(li.querySelector('ol.stars').getAttribute('aria-label')),
            dateTime: t.getAttribute('datetime'), title: li.querySelector('h3').textContent.trim(), text: p[0] || ''};
  })"""

PLAY_JS = """() => [...document.querySelectorAll('[role=dialog] div.RHo1pe')].map(el => {
    const h = el.querySelector('header[data-review-id]');
    const r = el.querySelector('[role=img][aria-label*="Rated"]');
    return {reviewId: h.getAttribute('data-review-id'), rating: parseInt(r.getAttribute('aria-label').match(/Rated (\\d)/)[1]),
            dateText: el.querySelector('.bp9Aid').textContent, text: (el.querySelector('.h3YV2d') || {}).textContent || ''};
  })"""


SPECS = {
    'small': {
        'apple': [('1228128916', ['us', 'gb'], 5)],
        'play': [('edvarderos.habitgrid', ['us', 'in'], 5)],
        'cities': ['Rome', 'Tokyo', 'Paris'], 'hotels_per_city': 5,
        'ads_per_advertiser': 5,
    },
    'large': {
        # 3 apps per store: a small one with old reviews, one mid-size, and WhatsApp (high volume) in both stores.
        'apple': [('1228128916', ['us', 'gb', 'in', 'au'], 10), ('6478388488', ['us', 'gb', 'in', 'au'], 10),
                  ('310633997', ['us', 'gb', 'in', 'au'], 10)],
        'play': [('edvarderos.habitgrid', ['us', 'in', 'gb', 'au'], 10), ('com.junhwank.habitjar', ['us', 'in', 'gb', 'au'], 10),
                 ('com.whatsapp', ['us', 'in', 'gb', 'au'], 10)],
        # 8 cities in 3 countries; Indonesia is where about half of hotels showed no price on 7 Oct.
        'cities': ['Tokyo', 'Osaka', 'Kyoto', 'Rome', 'Milan', 'Florence', 'Jakarta', 'Bali'], 'hotels_per_city': 20,
        'ads_per_advertiser': 13,
    },
}


def _scroll_dialog(page, times: int) -> None:
    for _ in range(times):
        page.evaluate("""() => { const d = document.querySelector('[role=dialog]');
            const s = [...d.querySelectorAll('div')].find(x => x.scrollHeight > x.clientHeight + 50
                && getComputedStyle(x).overflowY != 'visible'); if (s) s.scrollTop = s.scrollHeight; }""")
        page.wait_for_timeout(900)


def capture_reviews(page, spec: dict) -> None:
    actor = 'app-store-play-reviews-scraper'
    for app_id, countries, per in spec['apple']:
        for cc in countries:
            url = f'https://apps.apple.com/{cc}/app/id{app_id}?see-all=reviews'
            page.goto(url, wait_until='networkidle')
            items = sorted(page.evaluate(APPLE_JS), key=lambda x: x['dateTime'])[:per]
            save(actor, f'appstore_{app_id}_{cc}.json', {'store': 'appStore', 'appId': app_id, 'country': cc,
                 'method': f'Playwright Chromium, apps.apple.com Ratings & Reviews page (DOM); oldest {per} of '
                           f'the reviews shown', 'capturedAt': now(), 'url': page.url, 'items': items})
    for app_id, countries, per in spec['play']:
        seen: set[str] = set()
        for cc in countries:
            url = f'https://play.google.com/store/apps/details?id={app_id}&hl=en&gl={cc}'
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_timeout(4000)
            button = page.get_by_text('See all reviews', exact=True)
            if button.count() == 0:
                save(actor, f'play_{app_id}_{cc}.json', {'store': 'googlePlay', 'appId': app_id, 'country': cc,
                     'language': 'en', 'capturedAt': now(), 'url': url, 'items': [],
                     'method': 'Google Play showed no reviews section for this country from this connection.'})
                continue
            button.last.click()
            page.wait_for_selector('[role=dialog] div.RHo1pe', timeout=20000)
            _scroll_dialog(page, 6)
            all_items = page.evaluate(PLAY_JS)
            items = [x for x in all_items if x['reviewId'] not in seen][:per]  # in the order Google shows them
            seen |= {x['reviewId'] for x in items}
            save(actor, f'play_{app_id}_{cc}.json', {'store': 'googlePlay', 'appId': app_id, 'country': cc,
                 'language': 'en', 'method': f'Playwright Chromium, Google Play See all reviews dialog (DOM), default '
                 f'Most relevant order; first {per} of {len(all_items)} loaded, not already captured for another country',
                 'capturedAt': now(), 'url': url, 'items': items})


# ---------------------------------------------------------------- hotels

CARDS_JS = """() => [...document.querySelectorAll('a.PVOOXe')].map(a => {
    let c = a.parentElement; while (c && !/₹/.test(c.innerText || '')) c = c.parentElement;
    const m = ((c && c.innerText) || '').match(/₹[\\d,]+/);
    return {name: a.getAttribute('aria-label'), hotelId: ((a.getAttribute('data-href') || '').match(/entity\\/([^?]+)/) || [])[1],
            priceText: m ? m[0] : null};
  })"""


def capture_hotels(page, check_in: date, nights: int, spec: dict) -> None:
    check_out = check_in + timedelta(days=nights)
    captures = []
    for city in spec['cities']:
        url = f'https://www.google.com/travel/search?q=hotels%20in%20{city}&hl=en&gl=in'
        page.goto(url, wait_until='domcontentloaded')
        page.wait_for_selector('input[placeholder="Check-in"]')
        page.wait_for_timeout(2500)
        page.locator('input[placeholder="Check-in"]').first.click()
        page.wait_for_timeout(800)
        for d in (check_in, check_out):
            cell = page.locator(f'[data-iso="{d.isoformat()}"]').first
            for _ in range(3):
                if cell.is_visible():
                    break
                page.get_by_role('button', name=re.compile('^Next$')).last.click()
                page.wait_for_timeout(500)
            cell.click()
            page.wait_for_timeout(500)
        page.locator('button:visible, [role=button]:visible').filter(has_text=re.compile(r'^\s*Done\s*$')).last.click()
        page.wait_for_timeout(6000)
        page.wait_for_selector('a.PVOOXe')
        shown_in = page.locator('input[placeholder="Check-in"]').first.input_value()
        shown_out = page.locator('input[placeholder="Check-out"]').first.input_value()
        trav = page.locator('[aria-label*="travelers" i]').first
        travelers = trav.get_attribute('aria-label')
        trav.click()
        page.wait_for_timeout(1000)
        occupancy = page.evaluate("""() => [...document.querySelectorAll('[aria-label="Adults"],[aria-label="Children"]')]
            .map(e => e.getAttribute('aria-label') + ' ' + (e.closest('div').innerText || '').replace(/\\s+/g, ' ').trim())""")
        page.keyboard.press('Escape')
        for _ in range(3):  # let the list render fully
            page.mouse.wheel(0, 3000)
            page.wait_for_timeout(700)
        cards = [c for c in page.evaluate(CARDS_JS) if c['hotelId']][:spec['hotels_per_city']]  # priced or not
        captures.append({'city': city, 'url': url, 'finalUrl': page.url, 'capturedAt': now(),
                         'checkIn': check_in.isoformat(), 'checkOut': check_out.isoformat(),
                         'checkInShown': shown_in, 'checkOutShown': shown_out, 'travelers': travelers,
                         'occupancyShown': occupancy, 'cards': cards})
        print(city, shown_in, shown_out, travelers, len(cards))
    save('google-hotels-scraper', 'browser_captures.json', {
        'method': 'Playwright Chromium on this computer (India), Google Hotels results page: dates picked in the '
                  'date picker, first organic results per city, nightly price shown on each card or none (DOM). Prices are '
                  'in Indian rupees because the browser is in India, so the stated country is IN and currency INR.',
        'timezone': TZ, 'captures': captures})


# ---------------------------------------------------------------- ads

ADVERTISERS_SMALL = ['AR03840027764254048257', 'AR14055623489428127745', 'AR13203228903881048065',
                     'AR12166817904825729025', 'AR12462675644678930433']
# Large: adds big advertisers with video and third-party-hosted ads (Nike, HubSpot, IKEA Deutschland, Commerce.com,
# Superhuman, HYBE, Runikon) to the five small ones.
ADVERTISERS_LARGE = ADVERTISERS_SMALL + ['AR16735076323512287233', 'AR10072600183532683265', 'AR08008732350420615169',
                                         'AR05107678471086145537', 'AR05288677867637440513', 'AR11720784202516398081',
                                         'AR13303717429071839233']


def _goto_ads(page, url: str, selector: str) -> None:
    """Open a Transparency Center page gently: pause between pages and back off if Google answers 429."""
    for attempt in range(4):
        page.wait_for_timeout(6000)
        page.goto(url, wait_until='domcontentloaded')
        if '429' in (page.title() or '') or "That's an error" in page.inner_text('body')[:500]:
            print(f'  429 from Google, waiting {60 * (attempt + 1)} s')
            page.wait_for_timeout(60000 * (attempt + 1))
            continue
        page.wait_for_selector(selector, timeout=45000)
        page.wait_for_timeout(1500)
        return
    raise RuntimeError(f'Google kept answering 429 for {url}')


def capture_ads(page, spec: dict, advertisers: list[str], append: bool = False) -> None:
    out = []
    if append:  # add advertisers to the current capture instead of starting over
        current = ROOT / 'google-ads-transparency-scraper' / 'tests' / 'golden' / 'captures' / 'browser_captures.json'
        out = json.loads(current.read_text(encoding='utf-8'))['advertisers']
        advertisers = [a for a in advertisers if a not in {x['advertiserId'] for x in out}]
    for adv in advertisers:
        url = f'https://adstransparency.google.com/advertiser/{adv}?region=anywhere'
        _goto_ads(page, url, 'a[href*="/creative/"]')
        text = page.inner_text('body')
        name = re.search(r'Legal name: ([^\n]+)', text).group(1).strip()
        count = (re.search(r'(\d[\d,]*) ads', text) or [None, None])[1]
        links = []
        for href in page.eval_on_selector_all('a[href*="/creative/"]', 'els => els.map(a => a.getAttribute("href"))'):
            cr = re.search(r'creative/(CR\d+)', href).group(1)
            if cr not in links:
                links.append(cr)
        ads = []
        for cr in links[:spec['ads_per_advertiser']]:
            _goto_ads(page, f'https://adstransparency.google.com/advertiser/{adv}/creative/{cr}?region=anywhere',
                      'text=Format:')
            t = page.inner_text('body')
            ads.append({'creativeId': cr, 'lastShownText': re.search(r'Last shown: ([^\n]+)', t).group(1).strip(),
                        'format': re.search(r'Format: ([^\n]+)', t).group(1).strip(), 'capturedAt': now()})
        out.append({'advertiserId': adv, 'advertiserName': name, 'adsShown': int(count.replace(',', '')) if count else None,
                    'url': url, 'ads': ads})
        print(name, [a['format'] for a in ads])
    save('google-ads-transparency-scraper', 'browser_captures.json', {
        'method': 'Playwright Chromium, adstransparency.google.com advertiser pages (first ad links) and each Ad '
                  'details page, rendered page (DOM): Last shown and Format as displayed, region Shown anywhere.',
        'timezone': TZ, 'advertisers': out})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('which', choices=['reviews', 'hotels', 'ads'])
    ap.add_argument('--checkin-days', type=int, default=10)
    ap.add_argument('--nights', type=int, default=2)
    ap.add_argument('--headed', action='store_true')
    ap.add_argument('--size', choices=['small', 'large'], default='large')
    ap.add_argument('--add-advertisers', nargs='*', default=None,
                    help='Ads only: capture these advertisers and add them to the current golden capture')
    args = ap.parse_args()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not args.headed)
        ctx = browser.new_context(locale='en-US', timezone_id=TZ, viewport={'width': 1366, 'height': 900})
        page = ctx.new_page()
        page.set_default_timeout(30000)
        try:
            _run(args, page)
        except Exception:
            shot = ROOT / 'reports' / 'tmp' / f'pw-{args.which}-error.png'
            page.screenshot(path=str(shot))
            print('screenshot of the failure:', shot)
            raise
        browser.close()


def _run(args, page) -> None:
    if True:
        spec = SPECS[args.size]
        folder = {'reviews': 'app-store-play-reviews-scraper', 'hotels': 'google-hotels-scraper',
                  'ads': 'google-ads-transparency-scraper'}[args.which]
        captures = ROOT / folder / 'tests' / 'golden' / 'captures'
        for leftover in (captures / STAGING).glob('*.json'):
            leftover.unlink()
        if args.which == 'reviews':
            capture_reviews(page, spec)
        elif args.which == 'hotels':
            capture_hotels(page, datetime.now(timezone.utc).date() + timedelta(days=args.checkin_days), args.nights, spec)
        else:
            if args.add_advertisers:
                capture_ads(page, spec, args.add_advertisers, append=True)
            else:
                capture_ads(page, spec, ADVERTISERS_LARGE if args.size == 'large' else ADVERTISERS_SMALL)
        # Success: replace the previous capture with the new one (history stays in git).
        for old in captures.glob('*.json'):
            old.unlink()
        for new in (captures / STAGING).glob('*.json'):
            new.replace(captures / new.name)


if __name__ == '__main__':
    main()
