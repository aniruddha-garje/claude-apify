"""Google Ads Transparency Center scraper: ads with their creative content, by domain, advertiser or name."""

from __future__ import annotations

import asyncio
import hashlib
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any

from apify import Actor
from crawlee import ConcurrencySettings, Request
from crawlee.crawlers import HttpCrawler, HttpCrawlingContext

from .delivery import Delivery
from .invariants import check_image_urls, check_rows
from .limits import DomainLimiter
from .metrics import AttemptStats
from .run_checks import run_checks
from .parsers import (
    FORMAT_CODES,
    PLATFORM_CODES,
    PLATFORMS,
    RPC_HEADERS,
    RpcError,
    creatives_request,
    form_body,
    parse_creatives,
    parse_preview,
    parse_suggestions,
    rank_candidates,
    region_code,
    rpc_url,
    suggestions_request,
)

MAX_ASSETS_PER_AD = 3
MAX_PAGES = 100  # 10,000 ads per source and format
PREVIEW_HEADERS = {'Referer': 'https://adstransparency.google.com/'}


class InputError(ValueError):
    pass


class BlockedError(RuntimeError):
    pass


def _date(value: Any, name: str) -> date:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as exc:
        raise InputError(f'{name} must be a date like 2026-09-01, got "{value}". Example: 2026-09-01.') from exc


def read_input(cfg: dict[str, Any], today: date) -> dict[str, Any]:
    domains = sorted({_clean_domain(d) for d in cfg.get('domains') or [] if str(d).strip()})
    advertiser_ids = sorted({str(a).strip().upper() for a in cfg.get('advertiserIds') or [] if str(a).strip()})
    terms = [str(t).strip() for t in cfg.get('searchTerms') or [] if str(t).strip()]
    if not domains and not advertiser_ids and not terms:
        raise InputError('Add at least one domain, advertiser ID or advertiser name. Example: domains ["nike.com"].')
    bad = [a for a in advertiser_ids if not (a.startswith('AR') and a[2:].isdigit())]
    if bad:
        raise InputError(f'These advertiser IDs are not valid: {bad}. An ID is AR followed by digits, for example '
                         'AR16735076323512287233 (shown in the advertiser page URL).')
    region = str(cfg.get('region') or 'anywhere')
    try:
        region_num = region_code(region)
    except ValueError as exc:
        raise InputError(f'Unknown region "{region}". Use a two-letter ISO 3166 code or "anywhere", for example US, '
                         'DE or anywhere.') from exc
    date_to = _date(cfg['dateTo'], 'dateTo') if cfg.get('dateTo') else today
    date_from = _date(cfg['dateFrom'], 'dateFrom') if cfg.get('dateFrom') else date_to - timedelta(days=30)
    if date_from > date_to:
        raise InputError(f'dateFrom ({date_from}) must be on or before dateTo ({date_to}). '
                         f'Example: dateFrom {date_to - timedelta(days=30)}, dateTo {date_to}.')
    formats = [f.lower() for f in cfg.get('formats') or ['text', 'image', 'video']]
    unknown = [f for f in formats if f not in FORMAT_CODES]
    if unknown:
        raise InputError(f'Unknown formats {unknown}. Use text, image or video, for example ["text", "image"].')
    platforms = [str(x).lower() for x in cfg.get('platforms') or list(PLATFORM_CODES)]
    bad_platforms = [x for x in platforms if x not in PLATFORM_CODES]
    if bad_platforms:
        raise InputError(f'Unknown platforms {bad_platforms}. Use any of {", ".join(PLATFORM_CODES)}, '
                         'for example ["search", "youtube"].')
    format_codes = [None] if len(set(formats)) == 3 else sorted({FORMAT_CODES[f] for f in formats})
    platform_codes = [None] if len(set(platforms)) == len(PLATFORM_CODES) else sorted({PLATFORM_CODES[x] for x in platforms})
    since = None
    if cfg.get('onlyNewSince'):
        try:
            since = datetime.fromisoformat(str(cfg['onlyNewSince']).replace('Z', '+00:00'))
        except ValueError as exc:
            raise InputError(f'onlyNewSince must be an ISO date or date-time, got "{cfg["onlyNewSince"]}". '
                             'Example: 2026-10-01 or 2026-10-01T00:00:00Z.') from exc
        if since.tzinfo is None:
            since = since.replace(tzinfo=timezone.utc)
    min_score = float(cfg.get('minMatchScore') if cfg.get('minMatchScore') is not None else 1.0)
    if not 0 <= min_score <= 1:
        raise InputError(f'minMatchScore must be between 0 and 1, got {min_score}. Example: 1 for exact names only, '
                         '0.8 to include close spellings.')
    per_term = int(cfg.get('maxAdvertisersPerTerm') if cfg.get('maxAdvertisersPerTerm') is not None else 3)
    if not 1 <= per_term <= 10:
        raise InputError(f'maxAdvertisersPerTerm must be between 1 and 10, got {per_term}. Example: 3.')
    max_ads = int(cfg.get('maxAdsPerAdvertiser') if cfg.get('maxAdsPerAdvertiser') is not None else 100)
    max_domain = int(cfg.get('maxAdsPerDomain') if cfg.get('maxAdsPerDomain') is not None else 200)
    if max_ads < 0 or max_domain < 0:
        raise InputError(f'maxAdsPerAdvertiser and maxAdsPerDomain must be 0 (no limit) or more, got {max_ads} and '
                         f'{max_domain}. Example: 100 and 200.')
    return {
        'domains': domains,
        'advertiserIds': advertiser_ids,
        'terms': terms,
        'region': region,
        'regionNum': region_num,
        'dateFrom': date_from,
        'dateTo': date_to,
        'formatCodes': format_codes,
        'streams': [(f, pl) for f in format_codes for pl in platform_codes],
        'since': since,
        'maxAds': None if max_ads == 0 else max_ads,
        'maxDomain': None if max_domain == 0 else max_domain,
        'minScore': min_score,
        'perTerm': per_term,
        'content': bool(cfg.get('includeCreativeContent', True)),
        'assets': bool(cfg.get('downloadAssets', False)),
    }


def utc_today() -> date:
    """Today's date when the run starts. The default date window is computed from it, so it never goes stale."""
    return datetime.now(timezone.utc).date()


def resolve_input(cfg: dict[str, Any]) -> dict[str, Any]:
    return read_input(cfg, utc_today())


def _clean_domain(value: str) -> str:
    v = str(value).strip().lower()
    for prefix in ('https://', 'http://'):
        if v.startswith(prefix):
            v = v[len(prefix):]
    v = v.split('/')[0]
    return v[4:] if v.startswith('www.') else v


def _iso_to_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


def _rpc(path: str, req: dict, data: dict) -> Request:
    body = form_body(req)
    return Request.from_url(
        rpc_url(path), method='POST', payload=body.encode(), headers=RPC_HEADERS, user_data=data,
        unique_key=f'{path}|{hashlib.sha1(body.encode()).hexdigest()}',
    )


async def main() -> None:
    async with Actor:
        cfg = await Actor.get_input() or {}
        try:
            p = resolve_input(cfg)
        except InputError as exc:
            Actor.log.error(str(exc))
            await Actor.fail(status_message=str(exc))
            return

        ads = Delivery('ad', row_id=lambda r: f"{r['advertiserId']}|{r['creativeId']}|{r['region']}")
        assets = Delivery('asset', row_id=lambda r: r['key'])
        await ads.load()
        await assets.load()
        pending: dict[str, int] = {}  # previews queued per advertiser, so the cap is not overshot with requests
        domains = DomainLimiter(ads.counts, p['maxDomain'])
        stats = (await Actor.get_value('RUN_STATS')) or {'requests': 0, 'blocked': 0, 'adsSeen': 0}
        matches: dict[str, list[dict]] = (await Actor.get_value('SEARCH_MATCHES')) or {}

        def creatives_req(source: str, *, advertiser_id=None, domain=None, fmt=None, platform=None, token=None,
                          page=1) -> Request:
            req = creatives_request(advertiser_id=advertiser_id, domain=domain, region=p['regionNum'],
                                    date_from=p['dateFrom'], date_to=p['dateTo'], format_code=fmt,
                                    platform_code=platform, page_token=token)
            data = {'kind': 'creatives', 'source': source, 'advertiserId': advertiser_id, 'domain': domain,
                    'fmt': fmt, 'platform': platform, 'page': page}
            return _rpc('SearchService/SearchCreatives', req, data)

        start: list[Request] = []
        for adv in p['advertiserIds']:
            for fmt, pl in p['streams']:
                start.append(creatives_req(f'advertiser:{adv}', advertiser_id=adv, fmt=fmt, platform=pl))
        for dom in p['domains']:
            for fmt, pl in p['streams']:
                start.append(creatives_req(f'domain:{dom}', domain=dom, fmt=fmt, platform=pl))
        for term in p['terms']:
            start.append(_rpc('SearchService/SearchSuggestions', suggestions_request(term),
                              {'kind': 'suggest', 'term': term}))

        proxy = await Actor.create_proxy_configuration(actor_proxy_input=cfg.get('proxyConfiguration'))
        crawler = HttpCrawler(
            proxy_configuration=proxy,
            max_request_retries=10,
            concurrency_settings=ConcurrencySettings(max_concurrency=8, desired_concurrency=4),
            additional_http_error_status_codes=[429],
        )

        def wanted(row: dict) -> bool:
            if p['since'] is not None:
                first = _iso_to_dt(row['firstShown'])
                if first is None or first < p['since']:
                    return False
            return True

        async def deliver_ad(ctx: HttpCrawlingContext, row: dict, from_preview: bool = False) -> None:
            if from_preview:
                pending[row['advertiserId']] = max(0, pending.get(row['advertiserId'], 0) - 1)
                domains.release(row['source'])
            if not domains.can_deliver(row['source']):
                return  # maxAdsPerDomain reached; nothing stored, nothing charged
            if p['assets'] and row.get('imageUrls'):
                stored = []
                for i, url in enumerate(row['imageUrls'][:MAX_ASSETS_PER_AD]):
                    key = f'asset-{row["creativeId"]}-{i}'
                    if key in assets.seen:
                        stored.append(key)
                        continue
                    allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit('asset')
                    if allowed is not None and allowed < 1:
                        assets.budget_reached = True
                        break  # do not store images the user's spending limit cannot pay for
                    try:
                        resp = await ctx.send_request(url, headers=PREVIEW_HEADERS)
                        body = await resp.read()
                    except Exception as exc:  # noqa: BLE001 - a failed image never fails the ad row
                        Actor.log.warning(f'Could not download {url}: {exc}')
                        continue
                    if resp.status_code != 200 or not body:
                        continue
                    ctype = resp.headers.get('content-type', 'application/octet-stream')
                    await Actor.set_value(key, body, content_type=ctype)
                    if await assets.deliver_charge_only({'key': key}) == 1:
                        stored.append(key)
                row['assetKeys'] = stored
            delivered = await ads.deliver([row], row['advertiserId'], p['maxAds'])
            if delivered:
                domains.record(row['source'], delivered)
                await ads.save()

        attempts = AttemptStats()
        attempts.attach(crawler)

        @crawler.router.default_handler
        async def handle(ctx: HttpCrawlingContext) -> None:
            if ads.budget_reached:
                return
            d = ctx.request.user_data
            stats['requests'] += 1
            body = await ctx.http_response.read()

            if d['kind'] == 'preview':
                row = dict(d['row'])
                content = parse_preview(body.decode('utf-8', errors='replace'))
                if not content:
                    raise BlockedError('Preview script had no ad content; retrying')
                row.update(content)
                row['imageUrls'] = sorted(set(row.get('imageUrls') or []) | ({row['imageUrl']} if row.get('imageUrl') else set()))
                row['contentStatus'] = {'externallyHosted': 'externallyHosted', 'unrecognized': 'notAvailable'}.get(
                    content.get('previewType'), 'ok')
                await deliver_ad(ctx, row, from_preview=True)
                return

            try:
                if d['kind'] == 'suggest':
                    ranked = rank_candidates(d['term'], parse_suggestions(body), p['minScore'], p['perTerm'])
                    matches[d['term']] = ranked
                    await Actor.set_value('SEARCH_MATCHES', matches)
                    picked = [a for a in ranked if a['selected']]
                    Actor.log.info(f'"{d["term"]}" candidates (match score, ads shown by Google): ' + '; '.join(
                        f'{a["advertiserName"]} {a["advertiserId"]} {a["country"]} score {a["matchScore"]}, '
                        f'{a["adsMin"]} to {a["adsMax"]} ads' + (' [scraped]' if a['selected'] else '')
                        for a in ranked[:10]))
                    if not picked:
                        Actor.log.warning(f'No advertiser name matches "{d["term"]}" with a score of at least '
                                          f'{p["minScore"]}, so nothing was scraped for it. See SEARCH_MATCHES for the '
                                          'candidates and put the right one in advertiserIds.')
                    for a in picked:
                        for fmt, pl in p['streams']:
                            req = creatives_req(f'term:{d["term"]}', advertiser_id=a['advertiserId'], fmt=fmt, platform=pl)
                            req.user_data['term'] = d['term']
                            req.user_data['matchScore'] = a['matchScore']
                            await ctx.add_requests([req])
                    return
                rows, token = parse_creatives(body, p['region'])
            except RpcError as exc:
                stats['blocked'] += 1
                if ctx.session:
                    ctx.session.retire()
                raise BlockedError(str(exc)) from exc

            stats['adsSeen'] += len(rows)
            oldest_last_shown = min((_iso_to_dt(r['lastShown']) for r in rows if r['lastShown']), default=None)
            fresh = 0
            for row in rows:
                row['source'] = d['source']
                row['searchTerm'] = d.get('term')
                row['matchScore'] = d.get('matchScore')
                row['platform'] = PLATFORMS.get(d.get('platform')) if d.get('platform') else None
                if not wanted(row):
                    continue
                room = ads.remaining(row['advertiserId'], p['maxAds'])
                if room is not None and room - pending.get(row['advertiserId'], 0) <= 0:
                    continue
                if not domains.room(row['source']):
                    continue
                key = f"{row['advertiserId']}|{row['creativeId']}|{row['region']}"
                if key in ads.seen:
                    continue
                fresh += 1
                if p['content'] and row.get('previewUrl'):
                    pending[row['advertiserId']] = pending.get(row['advertiserId'], 0) + 1
                    domains.reserve(row['source'])
                    await ctx.add_requests([Request.from_url(row['previewUrl'], headers=PREVIEW_HEADERS,
                                                             user_data={'kind': 'preview', 'row': row})])
                else:
                    row.update(_image_only_content(row))
                    await deliver_ad(ctx, row)
            ctx.log.info(f'{d["source"]} format={d.get("fmt")} page {d["page"]}: {len(rows)} ads')

            past_since = p['since'] is not None and oldest_last_shown is not None and oldest_last_shown < p['since']
            capped = (d.get('advertiserId') is not None and ads.remaining(d['advertiserId'], p['maxAds']) == 0)                 or domains.full(d['source'])
            exhausted = bool(rows) and fresh == 0 and p['since'] is None  # every ad on the page was capped or seen
            if token and not past_since and not capped and not exhausted and d['page'] < MAX_PAGES                     and not ads.budget_reached:
                nxt = creatives_req(d['source'], advertiser_id=d.get('advertiserId'), domain=d.get('domain'),
                                    fmt=d.get('fmt'), platform=d.get('platform'), token=token, page=d['page'] + 1)
                nxt.user_data['term'] = d.get('term')
                nxt.user_data['matchScore'] = d.get('matchScore')
                await ctx.add_requests([nxt])
            await Actor.set_value('RUN_STATS', stats)

        @crawler.failed_request_handler
        async def failed(ctx: HttpCrawlingContext, error: Exception) -> None:
            d = ctx.request.user_data
            if d.get('kind') == 'preview':
                # Keep the ad: its metadata and image were collected; say plainly that the text content is missing.
                row = dict(d['row'])
                row.update(_image_only_content(row))
                row['contentStatus'] = 'previewFailed'
                await deliver_ad(ctx, row, from_preview=True)
                return
            Actor.log.error(f'Gave up on {d.get("source") or d.get("term")} page {d.get("page")}: {error}')
            ads.record_error(d.get('source', d.get('term', '')), ctx.request.url, str(error))
            await ads.save()

        final = await crawler.run(start)
        await ads.save()
        await assets.save()
        stats.update({'adsDelivered': ads.charged, 'assetsDelivered': assets.charged, 'errors': ads.errors,
                      'adsPerAdvertiser': {k: v for k, v in ads.counts.items() if not k.startswith('domain:')},
                      'adsPerDomain': {k[7:]: v for k, v in ads.counts.items() if k.startswith('domain:')},
                      'requests': attempts.summary(final)})
        await Actor.set_value('RUN_STATS', stats)
        Actor.log.info(f'Delivered {ads.charged} ads and {assets.charged} assets; {stats["requests"]} requests, '
                       f'{stats["blocked"]} blocked, {len(ads.errors)} failed.')
        if ads.budget_reached or assets.budget_reached:
            Actor.log.warning('Stopped early because the maximum cost you set for this run was reached.')
        selected = [f.lower() for f in cfg.get('formats') or []]
        await run_checks(
            lambda rows: check_rows(rows, max_per_advertiser=p['maxAds'], max_per_domain=p['maxDomain'],
                                    formats=selected if selected and len(set(selected)) < 3 else None),
            canary=cfg.get('canary'),
            profile_fields=['advertiserName', 'firstShown', 'headline', 'imageUrl', 'previewUrl', 'targetDomain'],
            mix_field='contentStatus',
            async_checks=[lambda rows: check_image_urls(rows, _image_status)],
        )


async def _image_status(url: str) -> int:
    """HTTP status of an image URL, fetching only its first byte."""
    def get() -> int:
        req = urllib.request.Request(url, headers={'Range': 'bytes=0-0', 'User-Agent': 'Mozilla/5.0'})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return 200 if resp.status in (200, 206) else resp.status
        except urllib.error.HTTPError as exc:
            return exc.code
    return await asyncio.to_thread(get)


def _image_only_content(row: dict) -> dict:
    """Ads whose creative is a single rendered image (most text ads): the image is the content."""
    return {
        'previewType': None,
        'headline': None,
        'description': None,
        'texts': [],
        'displayUrl': None,
        'imageUrls': [row['imageUrl']] if row.get('imageUrl') else [],
        'videoIds': [],
        'videoUrls': [],
        'contentStatus': 'imageOnly' if row.get('imageUrl') else 'none',
    }
