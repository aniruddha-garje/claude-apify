"""App Store and Google Play reviews scraper.

Fetches every review the two stores expose through their public JSON endpoints, with plain HTTP.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from apify import Actor
from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import HttpCrawler, HttpCrawlingContext

from .delivery import Delivery
from .invariants import check_rows
from .metrics import AttemptStats
from .run_checks import run_checks
from .parsers import (
    APPLE_MAX_PAGE,
    APPLE_WEB_PAGE_SIZE,
    PLAY_PAGE_SIZE,
    PlayResponseError,
    apple_feed_url,
    apple_web_reviews_url,
    parse_app,
    parse_apple_feed,
    parse_apple_web_reviews,
    parse_play_response,
    play_batch_url,
    play_request_body,
)

APPLE_PAGE_SIZE = 50
# Apple's feed returns some pages empty although they hold reviews (measured 8 Oct 2026: about 3 requests in 4 were
# empty, and one page stayed empty on 45 tries over 105 seconds). An empty page is retried a few times, never ends the
# walk on its own, and is reported in RUN_SUMMARY when it stays empty.
APPLE_EMPTY_TRIES = 4
# Pages that stay empty through the quick tries are tried again later, while the walk carries on: on 8 Oct 2026 the
# audit lost 64 of 80 App Store golden reviews to pages that were empty for minutes, not seconds.
APPLE_LATE_TRIES = 3
APPLE_LATE_WAIT = 15  # seconds between late tries
# Sometimes the feed serves no reviews for any app at all (11 Oct 2026: 5 large apps in us, gb and in, all empty).
# Retries cannot fix that and only make the run slow, so when page 1 stays empty through the quick tries, the same
# page of an app with a very large number of reviews (Instagram) is asked for. If it is empty too, the feed is not
# serving in that country: the feed walk stops and that app and country switch to the App Store website's own review
# route. If the control has reviews, retries carry on as before.
APPLE_CONTROL_APP = '389801252'
APPLE_SOURCES = ('auto', 'website')

PLAY_HEADERS = {'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8', 'Origin': 'https://play.google.com'}


def _parse_since(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class InputError(ValueError):
    pass


SORTS = ('newest', 'mostHelpful', 'rating')


def read_input(cfg: dict[str, Any]) -> None:
    """Reject a bad input before any request is made, so it never costs anything."""
    apps = [str(a).strip() for a in cfg.get('apps') or [] if str(a).strip()]
    if not apps:
        raise InputError('Add at least one app. Example: apps ["310633997", "com.whatsapp"] '
                         '(an App Store ID and a Google Play package name).')
    if not any(parse_app(a) for a in apps):
        raise InputError(f'None of these is an App Store ID or URL, or a Google Play package name or URL: {apps}. '
                         'Example: 310633997, https://apps.apple.com/us/app/whatsapp-messenger/id310633997 or com.whatsapp.')
    stores = cfg.get('stores') or ['appStore', 'googlePlay']
    bad_stores = [s for s in stores if s not in ('appStore', 'googlePlay')]
    if bad_stores:
        raise InputError(f'Unknown stores {bad_stores}. Use appStore and googlePlay, for example ["appStore"].')
    bad_countries = [c for c in cfg.get('countries') or [] if not (len(str(c)) == 2 and str(c).isalpha())]
    if bad_countries:
        raise InputError(f'Countries must be two-letter ISO 3166 codes; these are not: {bad_countries}. '
                         'Example: ["us", "gb"].')
    sort = cfg.get('sort') or 'newest'
    if sort not in SORTS:
        raise InputError(f'Unknown sort "{sort}". Use newest, mostHelpful or rating, for example newest.')
    source = cfg.get('appStoreSource') or 'auto'
    if source not in APPLE_SOURCES:
        raise InputError(f'Unknown appStoreSource "{source}". Use auto or website, for example auto.')
    cap = cfg.get('maxReviewsPerApp', 500)
    if cap is not None and int(cap) < 0:
        raise InputError(f'maxReviewsPerApp must be 0 (no limit) or more, got {cap}. Example: 500.')
    bad_ratings = [r for r in cfg.get('ratings') or [] if str(r) not in ('1', '2', '3', '4', '5')]
    if bad_ratings:
        raise InputError(f'ratings must be stars from 1 to 5, got {bad_ratings}. Example: ["1", "2"].')
    try:
        _parse_since(cfg.get('since'))
    except ValueError as exc:
        raise InputError(f'since must be an ISO date or date-time, got "{cfg.get("since")}". '
                         'Example: 2026-09-01.') from exc


def _row_date(row: dict) -> datetime | None:
    return datetime.fromisoformat(row['date'].replace('Z', '+00:00')) if row.get('date') else None


def _key(store: str, app_id: str, country: str, language: str | None) -> str:
    return f'{store}|{app_id}|{country}|{language or "-"}'


async def main() -> None:
    async with Actor:
        cfg: dict[str, Any] = await Actor.get_input() or {}
        try:
            read_input(cfg)
        except InputError as exc:
            Actor.log.error(str(exc))
            await Actor.fail(status_message=str(exc))
            return
        apps_in = cfg.get('apps') or []
        stores = set(cfg.get('stores') or ['appStore', 'googlePlay'])
        countries = [c.lower() for c in (cfg.get('countries') or ['us'])]
        languages = cfg.get('languages') or ['en']
        sort = cfg.get('sort') or 'newest'
        apple_source = cfg.get('appStoreSource') or 'auto'
        cap = cfg.get('maxReviewsPerApp', 500)
        cap = None if cap in (None, 0) else int(cap)
        ratings = sorted({int(r) for r in (cfg.get('ratings') or [])}) or [1, 2, 3, 4, 5]
        since = _parse_since(cfg.get('since'))
        include_replies = cfg.get('includeDeveloperReplies', True)
        include_reviewer = cfg.get('includeReviewerInfo', False)
        continue_on_error = cfg.get('continueOnError', True)

        delivery = Delivery('review', row_id=lambda r: f"{r['store']}|{r['appId']}|{r['country']}|{r['reviewId']}")
        await delivery.load()

        requests: list[Request] = []
        for value in apps_in:
            ref = parse_app(str(value))
            if ref is None:
                msg = (f'Skipped "{value}": not an App Store ID or URL, nor a Google Play package name or URL. '
                       'Example: 310633997 or com.whatsapp.')
                Actor.log.warning(msg)
                delivery.record_error(str(value), '', msg)
                continue
            if ref.store not in stores:
                Actor.log.info(f'Skipped "{value}": it is a {ref.store} app and that store is not selected.')
                continue
            for country in countries:
                if ref.store == 'appStore':
                    key = _key('appStore', ref.app_id, country, None)
                    if apple_source == 'website':
                        requests.append(_web_request(ref.app_id, country, 0, sort, key))
                        continue
                    data = {'kind': 'apple', 'appId': ref.app_id, 'country': country, 'page': 1, 'key': key}
                    requests.append(Request.from_url(apple_feed_url(ref.app_id, country, 1, sort), user_data=data))
                else:
                    star_streams: list[int | None] = [None] if len(ratings) == 5 else list(ratings)
                    for language in languages:
                        key = _key('googlePlay', ref.app_id, country, language)
                        for star in star_streams:
                            data = {'kind': 'play', 'appId': ref.app_id, 'country': country, 'language': language,
                                    'star': star, 'token': None, 'page': 1, 'key': key}
                            requests.append(_play_request(data, sort, cap))

        if not requests:
            await delivery.save()
            Actor.log.warning('Nothing to do: no valid apps for the selected stores.')
            await Actor.set_value('RUN_SUMMARY', _summary(delivery))
            return

        proxy = await Actor.create_proxy_configuration(actor_proxy_input=cfg.get('proxyConfiguration'))
        crawler = HttpCrawler(
            proxy_configuration=proxy,
            max_request_retries=6,
            concurrency_settings=ConcurrencySettings(max_concurrency=20, desired_concurrency=10),
            ignore_http_error_status_codes=[400, 404],
        )

        def wanted(row: dict) -> bool:
            if row['rating'] not in ratings:
                return False
            if since is not None:
                d = _row_date(row)
                if d is not None and d < since:
                    return False
            return True

        attempts = AttemptStats()
        attempts.attach(crawler)
        empty_pages: dict[str, list[int]] = {}
        feed_dark: dict[str, bool] = {}  # country -> the feed served no reviews for the control app either
        dark_keys: list[str] = []

        async def _apple_feed_is_dark(ctx: HttpCrawlingContext, country: str) -> bool:
            """Ask for page 1 of the control app, with the same quick tries; dark only if every try is empty."""
            if country not in feed_dark:
                feed_dark[country] = True
                for t in range(1, APPLE_EMPTY_TRIES + 1):
                    resp = await ctx.send_request(apple_feed_url(APPLE_CONTROL_APP, country, 1, sort) + f'?control={t}')
                    if resp.status_code == 200 and parse_apple_feed(await resp.read(), APPLE_CONTROL_APP, country, False):
                        feed_dark[country] = False
                        break
            return feed_dark[country]

        async def _try_later(ctx: HttpCrawlingContext, d: dict, late: int) -> None:
            """Wait, then ask for the same page again; the walk has already moved on to the next page."""
            if late > APPLE_LATE_TRIES or delivery.remaining(d['key'], cap) == 0:
                return
            await asyncio.sleep(APPLE_LATE_WAIT)
            if delivery.remaining(d['key'], cap) == 0:
                return
            url = apple_feed_url(d['appId'], d['country'], d['page'], sort) + f'?late={late}'
            await ctx.add_requests([Request.from_url(url, user_data={**d, 'late': late})])

        async def _late_page(ctx: HttpCrawlingContext, d: dict, rows: list[dict], got: int) -> None:
            if got == 0:
                await _try_later(ctx, d, d['late'] + 1)
                return
            pages = empty_pages.get(d['key'], [])
            if d['page'] in pages:
                pages.remove(d['page'])
                if not pages:
                    empty_pages.pop(d['key'], None)
            pushed = await delivery.deliver([r for r in rows if wanted(r)], d['key'], cap)
            ctx.log.info(f'{d["key"]} page {d["page"]} (late try {d["late"]}): {got} reviews, {pushed} delivered')

        async def _resume_after_empty_first_page(ctx: HttpCrawlingContext, d: dict) -> None:
            """The website has reviews, so page 1 of the feed is only slow: walk on and try page 1 again later."""
            key = d['key']
            empty_pages.setdefault(key, []).append(1)
            if delivery.remaining(key, cap) != 0:
                nxt = {**d, 'kind': 'apple', 'page': 2, 'tries': 1}
                await ctx.add_requests([Request.from_url(apple_feed_url(d['appId'], d['country'], 2, sort), user_data=nxt)])
            await _try_later(ctx, {**d, 'kind': 'apple', 'page': 1}, 1)

        @crawler.router.default_handler
        async def handle(ctx: HttpCrawlingContext) -> None:
            d = ctx.request.user_data
            key = d['key']
            if delivery.budget_reached:
                return
            if delivery.remaining(key, cap) == 0:
                return
            status = ctx.http_response.status_code
            body = await ctx.http_response.read()

            if d['kind'] == 'appleweb':
                if status in (400, 404):
                    delivery.notes[key] = (f'The App Store website has no reviews for app {d["appId"]} in '
                                           f'{d["country"]} (HTTP {status}).')
                    return
                rows, more = parse_apple_web_reviews(body, d['appId'], d['country'], include_reviewer)
                got = len(rows)
                oldest = min((_row_date(r) for r in rows if r.get('date')), default=None)
                rows = [r for r in rows if wanted(r)]
                pushed = await delivery.deliver(rows, key, cap)
                ctx.log.info(f'{key} website offset {d["offset"]}: {got} reviews, {pushed} delivered')
                stop_since = since is not None and sort == 'newest' and oldest is not None and oldest < since
                ended = None
                if stop_since:
                    ended = f'Stopped at reviews older than {cfg.get("since")}.'
                elif not more or got == 0:
                    ended = _ran_out('App Store website', d['offset'] // APPLE_WEB_PAGE_SIZE + 1,
                                     delivery.counts.get(key, 0), cap)
                if ended:  # keep the note that says why this app and country came from the website
                    delivery.notes[key] = f'{delivery.notes.get(key, "")} {ended}'.strip()
                elif delivery.remaining(key, cap) != 0:
                    await ctx.add_requests([_web_request(d['appId'], d['country'], d['offset'] + APPLE_WEB_PAGE_SIZE,
                                                         sort, key)])
                return

            if d['kind'] == 'appleprobe':
                # Page 1 of the feed was empty although the feed serves the control app: ask the website once.
                rows = [] if status in (400, 404) else parse_apple_web_reviews(body, d['appId'], d['country'],
                                                                               include_reviewer)[0]
                if not rows:
                    delivery.notes[key] = (f'App {d["appId"]} has no reviews in {d["country"]}: page 1 of Apple\'s '
                                           'feed was empty and the App Store website lists none.')
                    return
                await _resume_after_empty_first_page(ctx, d)
                return

            if d['kind'] == 'apple':
                if status in (400, 404):
                    delivery.notes[key] = f'App Store feed ended at page {d["page"] - 1} (HTTP {status}).'
                    return
                rows = parse_apple_feed(body, d['appId'], d['country'], include_reviewer)
                got = len(rows)
                tries = d.get('tries', 1)
                if got == 0 and tries < APPLE_EMPTY_TRIES:
                    nxt = {**d, 'tries': tries + 1}
                    url = apple_feed_url(d['appId'], d['country'], d['page'], sort) + f'?try={tries + 1}'
                    await ctx.add_requests([Request.from_url(url, user_data=nxt)])
                    return
                if d.get('late'):
                    await _late_page(ctx, d, rows, got)
                    return
                if got == 0 and d['page'] == 1 and await _apple_feed_is_dark(ctx, d['country']):
                    dark_keys.append(key)
                    delivery.notes[key] = (
                        f"Apple's public review feed served no reviews in {d['country']} for this app or for a control "
                        'app with a very large number of reviews, so these reviews come from the App Store website '
                        'instead, which has no app version or vote counts.')
                    await ctx.add_requests([_web_request(d['appId'], d['country'], 0, sort, key)])
                    return
                if got == 0 and d['page'] == 1:
                    # The feed serves the control app, so either this app has no reviews here or its page 1 is
                    # empty for a while. One website request tells which: on 11 Oct 2026 an app with no US reviews
                    # took 209 s of feed retries to return nothing (run ORna5YgrAiHFuFQqi).
                    probe = {**d, 'kind': 'appleprobe'}
                    await ctx.add_requests([Request.from_url(
                        apple_web_reviews_url(d['appId'], d['country'], 0, sort), user_data=probe,
                        headers={'Accept': 'application/json'}, unique_key=f'probe|{key}')])
                    return
                if got == 0:
                    empty_pages.setdefault(key, []).append(d['page'])
                oldest = min((_row_date(r) for r in rows if r.get('date')), default=None)
                rows = [r for r in rows if wanted(r)]
                pushed = await delivery.deliver(rows, key, cap)
                ctx.log.info(f'{key} page {d["page"]}: {got} reviews, {pushed} delivered')
                stop_since = since is not None and sort == 'newest' and oldest is not None and oldest < since
                if 0 < got < APPLE_PAGE_SIZE:
                    delivery.notes[key] = _ran_out('App Store', d['page'], delivery.counts.get(key, 0), cap)
                elif stop_since:
                    delivery.notes[key] = f'Stopped at reviews older than {cfg.get("since")}.'
                elif d['page'] >= APPLE_MAX_PAGE:
                    delivery.notes[key] = (
                        f'Apple\'s public feed stops at {APPLE_MAX_PAGE} pages of 50 per country and sort order; '
                        f'{delivery.counts.get(key, 0)} reviews delivered for this country. Add more countries '
                        'to collect more.'
                    )
                elif delivery.remaining(key, cap) != 0:
                    nxt = {**d, 'page': d['page'] + 1, 'tries': 1}
                    await ctx.add_requests([Request.from_url(apple_feed_url(d['appId'], d['country'], nxt['page'], sort), user_data=nxt)])
                if got == 0:
                    await _try_later(ctx, d, 1)
                return

            # Google Play
            try:
                rows, token, found = parse_play_response(body, d['appId'], d['language'], d['country'], include_reviewer)
            except PlayResponseError:
                raise  # retried by the crawler, then reported by the failed-request handler
            if not found:
                msg = f'Google Play has no app "{d["appId"]}" for country {d["country"]}, or it has no reviews.'
                delivery.notes[key] = msg
                Actor.log.warning(msg)
                return
            if not include_replies:
                for r in rows:
                    r['developerReply'] = None
                    r['developerReplyDate'] = None
            got = len(rows)
            oldest = min((_row_date(r) for r in rows if r.get('date')), default=None)
            rows = [r for r in rows if wanted(r)]
            pushed = await delivery.deliver(rows, key, cap)
            ctx.log.info(f'{key} star={d["star"]} page {d["page"]}: {got} reviews, {pushed} delivered')
            stop_since = since is not None and sort == 'newest' and oldest is not None and oldest < since
            if stop_since:
                delivery.notes[key] = f'Stopped at reviews older than {cfg.get("since")}.'
            elif not token or got == 0:
                delivery.notes[key] = _ran_out('Google Play', d['page'], delivery.counts.get(key, 0), cap)
            elif delivery.remaining(key, cap) != 0:
                nxt = {**d, 'token': token, 'page': d['page'] + 1}
                await ctx.add_requests([_play_request(nxt, sort, delivery.remaining(key, cap))])

        @crawler.failed_request_handler
        async def failed(ctx: HttpCrawlingContext, error: Exception) -> None:
            d = ctx.request.user_data
            msg = f'Gave up on {d.get("key")} page {d.get("page")} after retries: {error}'
            Actor.log.error(msg)
            delivery.record_error(d.get('key', ''), ctx.request.url, str(error))
            await delivery.save()
            if not continue_on_error:
                await crawler.stop(f'continueOnError is off and a request failed: {error}')

        final = await crawler.run(requests)
        for key, pages in empty_pages.items():
            delivery.notes[key] = (
                f"Apple's feed returned page(s) {sorted(pages)} empty on all {APPLE_EMPTY_TRIES + APPLE_LATE_TRIES} tries; reviews on "
                f'those pages may be missing. {delivery.counts.get(key, 0)} delivered. ' + delivery.notes.get(key, ''))
        for key, count in delivery.counts.items():
            if cap is not None and count >= cap:
                delivery.notes.setdefault(key, f'Reached maxReviewsPerApp ({cap}).')
        await delivery.save()
        summary = _summary(delivery)
        summary['requests'] = attempts.summary(final)
        await Actor.set_value('RUN_SUMMARY', summary)
        for key, note in delivery.notes.items():
            Actor.log.info(f'{key}: {note}')
        Actor.log.info(f'Delivered {delivery.charged} reviews; {len(delivery.errors)} errors.')
        if delivery.budget_reached:
            Actor.log.warning('Stopped early because the maximum cost you set for this run was reached.')
        await run_checks(
            lambda rows: check_rows(rows, countries=countries, cap=cap, include_reviewer=include_reviewer),
            canary=cfg.get('canary'),
            profile_fields=['title', 'text', 'date', 'appVersion', 'developerReply', 'reviewUrl'],
            mix_field='store',
        )
        if dark_keys and not delivery.errors:
            await Actor.set_status_message(
                f"Finished with {delivery.charged} reviews. Apple's public review feed is not serving reviews right now "
                f'(checked with a control app), so {len(dark_keys)} App Store app and country pairs came from the App '
                'Store website, which has no app version or vote counts; see RUN_SUMMARY notes.')
        elif empty_pages and not delivery.errors:
            await Actor.set_status_message(
                f'Finished with {delivery.charged} reviews. Apple returned empty pages for {len(empty_pages)} app and '
                'country pairs even after retries; see RUN_SUMMARY notes.')
        if delivery.errors and not continue_on_error:
            await Actor.fail(status_message=f'Stopped after an error; {delivery.charged} reviews were saved.')


def _web_request(app_id: str, country: str, offset: int, sort: str, key: str) -> Request:
    data = {'kind': 'appleweb', 'appId': app_id, 'country': country, 'offset': offset, 'key': key}
    return Request.from_url(apple_web_reviews_url(app_id, country, offset, sort), user_data=data,
                            headers={'Accept': 'application/json'})


def _play_request(d: dict, sort: str, room: int | None) -> Request:
    # Ask only for what is still needed (at least 20, so ratings filters still make progress).
    count = PLAY_PAGE_SIZE if room is None else max(20, min(PLAY_PAGE_SIZE, room))
    return Request.from_url(
        play_batch_url(d['language'], d['country']),
        method='POST',
        payload=play_request_body(d['appId'], sort, d['token'], d['star'], count).encode(),
        headers=PLAY_HEADERS,
        user_data=d,
        unique_key=f'{d["key"]}|{d["star"]}|{d["page"]}|{d["token"] or ""}',
    )


def _ran_out(store: str, pages: int, count: int, cap: int | None) -> str:
    if cap is not None and count >= cap:
        return f'Reached maxReviewsPerApp ({cap}).'
    return f'{store} has no more reviews for this app, country and filter after {pages} pages; {count} delivered.'


def _summary(delivery: Delivery) -> dict:
    return {
        'reviewsDelivered': delivery.charged,
        'perAppCountryLanguage': delivery.counts,
        'notes': delivery.notes,
        'errors': delivery.errors,
    }
