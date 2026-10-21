"""Apple's feed returns empty pages at random, and sometimes serves no reviews at all. These tests run the whole Actor
against a local server that replays saved real responses (a real 50-review page and a real empty page saved on 8 Oct
2026, the real empty feed the control app returned on 11 Oct 2026 while the feed was not serving, and a real 20-review
page of the App Store website's review route saved the same day) in a flaky order."""

import copy
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from apify import Actor

import src.main as main_module

FIX = Path(__file__).parent / 'fixtures'
FULL = json.loads((FIX / 'apple_rss_us_310633997_p1.json').read_text(encoding='utf-8'))
EMPTY = (FIX / 'apple_rss_us_310633997_p2_empty_flaky.json').read_bytes()
DARK = (FIX / 'apple_rss_us_389801252_p1_dark_20261011.json').read_bytes()
WEB = json.loads((FIX / 'apple_web_us_389801252_recent_offset0.json').read_text(encoding='utf-8'))


def page(n: int, size: int = 50) -> bytes:
    """The saved real page with review IDs made unique per page, so each page holds different reviews."""
    d = copy.deepcopy(FULL)
    entries = [e for e in d['feed']['entry'] if 'im:rating' in e][:size]
    for e in entries:
        e['id']['label'] = f'{e["id"]["label"]}{n:02d}'
    d['feed']['entry'] = entries
    return json.dumps(d).encode()


def web_page(offset: int, last: bool) -> bytes:
    """The saved real website page with review IDs made unique per offset; the last page has no next link."""
    d = copy.deepcopy(WEB)
    for item in d['data']:
        item['id'] = f'{item["id"]}{offset:05d}'
    if last:
        d.pop('next', None)
    return json.dumps(d).encode()


class Feed:
    """Serves pages; `plan[page]` is how many requests answer empty first (-1 means always empty).
    The control app gets its own answers: a full page, or the real empty feed when `dark` is set."""

    def __init__(self, plan: dict[int, int], last_page: int, last_size: int, dark: bool = False,
                 web_pages: int = 3) -> None:
        self.plan, self.last_page, self.last_size, self.dark = plan, last_page, last_size, dark
        self.hits: dict[int, int] = {}
        self.control_hits = 0
        self.web_pages, self.web_offsets = web_pages, []

    def web(self, offset: int) -> bytes | None:
        self.web_offsets.append(offset)
        if self.web_pages == 0:
            return None  # the website has no reviews for this app: HTTP 404
        return web_page(offset, last=offset // 20 + 1 >= self.web_pages)

    def control(self) -> bytes:
        self.control_hits += 1
        return DARK if self.dark else page(1)

    def body(self, n: int) -> bytes:
        self.hits[n] = self.hits.get(n, 0) + 1
        empties = self.plan.get(n, 0)
        if empties == -1 or self.hits[n] <= empties or n > self.last_page:
            return EMPTY
        return page(n, self.last_size if n == self.last_page else 50)


@pytest.fixture
def serve(monkeypatch):
    servers = []

    def start(feed: Feed) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                if self.path.startswith('/web/'):
                    data = feed.web(int(self.path.split('offset=')[1].split('/')[0]))
                    if data is None:
                        self.send_response(404)
                        self.end_headers()
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.end_headers()
                    self.wfile.write(data)
                    return
                n = int(self.path.split('page=')[1].split('/')[0])
                app = self.path.split('id=')[1].split('/')[0]
                data = feed.control() if app == main_module.APPLE_CONTROL_APP else feed.body(n)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        srv = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        port = srv.server_address[1]
        monkeypatch.setattr(main_module, 'APPLE_LATE_WAIT', 0)
        monkeypatch.setattr(main_module, 'apple_feed_url',
                            lambda app, country, n, sort: f'http://127.0.0.1:{port}/{country}/page={n}/id={app}/json')
        monkeypatch.setattr(main_module, 'apple_web_reviews_url',
                            lambda app, country, off, sort: f'http://127.0.0.1:{port}/web/offset={off}/id={app}/json')

    yield start
    for srv in servers:
        srv.shutdown()


async def run_actor(tmp_path, max_reviews=0, source='auto') -> tuple[list[dict], dict]:
    store = Path(tmp_path) / 'storage' / 'key_value_stores' / 'default'
    store.mkdir(parents=True, exist_ok=True)
    (store / 'INPUT.json').write_text(json.dumps({
        'apps': ['310633997'], 'stores': ['appStore'], 'countries': ['us'], 'maxReviewsPerApp': max_reviews,
        'appStoreSource': source, 'proxyConfiguration': {'useApifyProxy': False}}))
    await main_module.main()
    async with Actor:
        items = [i async for i in (await Actor.open_dataset()).iterate_items()]
        summary = await Actor.get_value('RUN_SUMMARY')
    return items, summary


async def test_empty_pages_are_retried_until_the_reviews_come(serve, tmp_path):
    feed = Feed(plan={1: 2, 2: 3}, last_page=3, last_size=10)  # pages 1 and 2 answer empty 2 and 3 times first
    serve(feed)
    items, summary = await run_actor(tmp_path)
    assert len(items) == 50 + 50 + 10
    assert feed.hits[1] == 3 and feed.hits[2] == 4
    assert 4 not in feed.hits  # a short page is the real end; nothing after it is requested
    assert 'no more reviews' in next(iter(summary['notes'].values()))


async def test_a_page_that_stays_empty_does_not_end_the_walk_and_is_reported(serve, tmp_path):
    feed = Feed(plan={2: -1}, last_page=3, last_size=10)
    serve(feed)
    items, summary = await run_actor(tmp_path)
    assert len(items) == 50 + 10  # pages 1 and 3; page 2 never answered
    assert feed.hits[2] == main_module.APPLE_EMPTY_TRIES + main_module.APPLE_LATE_TRIES
    note = next(iter(summary['notes'].values()))
    assert 'page(s) [2] empty' in note and 'may be missing' in note


async def test_a_page_empty_for_longer_is_collected_on_a_late_try(serve, tmp_path):
    # Page 1 is empty for the 4 quick tries and the first late try, then answers; the walk did not wait for it.
    feed = Feed(plan={1: main_module.APPLE_EMPTY_TRIES + 1}, last_page=3, last_size=10)
    serve(feed)
    items, summary = await run_actor(tmp_path)
    assert len(items) == 50 + 50 + 10
    assert feed.hits[1] == main_module.APPLE_EMPTY_TRIES + 2
    assert 'may be missing' not in next(iter(summary['notes'].values()))


async def test_a_feed_that_serves_nothing_switches_to_the_website_at_once(serve, tmp_path):
    feed = Feed(plan={n: -1 for n in range(1, 11)}, last_page=10, last_size=50, dark=True, web_pages=3)
    serve(feed)
    items, summary = await run_actor(tmp_path)
    assert feed.hits == {1: main_module.APPLE_EMPTY_TRIES}  # no later feed page and no late try is asked for
    assert feed.control_hits == main_module.APPLE_EMPTY_TRIES
    assert feed.web_offsets == [0, 20, 40] and len(items) == 60
    assert all(i['appVersion'] is None and i['store'] == 'appStore' for i in items)
    note = next(iter(summary['notes'].values()))
    assert 'come from the App Store website' in note and 'no more reviews' in note


async def test_a_control_app_with_reviews_keeps_the_usual_retries(serve, tmp_path):
    feed = Feed(plan={1: -1}, last_page=3, last_size=10)  # only this app's page 1 is empty; the control has reviews
    serve(feed)
    items, summary = await run_actor(tmp_path)
    assert len(items) == 50 + 10
    assert feed.hits[1] == main_module.APPLE_EMPTY_TRIES + main_module.APPLE_LATE_TRIES
    assert feed.control_hits == 1
    assert feed.web_offsets == [0]  # one website check showed the app has reviews, so the feed walk went on
    assert 'page(s) [1] empty' in next(iter(summary['notes'].values()))


async def test_an_app_with_no_reviews_stops_after_one_website_check(serve, tmp_path):
    feed = Feed(plan={n: -1 for n in range(1, 11)}, last_page=10, last_size=50, web_pages=0)
    serve(feed)
    items, summary = await run_actor(tmp_path)
    assert items == []
    assert feed.hits == {1: main_module.APPLE_EMPTY_TRIES}  # no later feed page and no late try
    assert feed.web_offsets == [0]
    assert 'has no reviews in us' in next(iter(summary['notes'].values()))


async def test_website_source_skips_the_feed_and_honours_the_cap_exactly(serve, tmp_path):
    feed = Feed(plan={}, last_page=10, last_size=50, web_pages=50)
    serve(feed)
    items, summary = await run_actor(tmp_path, max_reviews=30, source='website')
    assert feed.hits == {} and feed.control_hits == 0  # the feed is never asked
    assert feed.web_offsets == [0, 20] and len(items) == 30
    assert 'Reached maxReviewsPerApp (30)' in next(iter(summary['notes'].values()))


async def test_website_source_goes_past_500_reviews(serve, tmp_path):
    feed = Feed(plan={}, last_page=10, last_size=50, web_pages=30)
    serve(feed)
    items, _ = await run_actor(tmp_path, max_reviews=600, source='website')
    assert len(items) == 600 and feed.web_offsets[-1] == 580
