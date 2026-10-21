"""Rules every delivered ad must satisfy. Checked in the tests and at the end of every run.

check_rows() is pure. check_image_urls() makes real requests, so runs check a sample of image links and tests
check it against a stub response.
"""

from __future__ import annotations

import random
from collections import Counter
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

FORMATS = ('text', 'image', 'video', 'unknown')
CONTENT = ('ok', 'imageOnly', 'externallyHosted', 'notAvailable', 'previewFailed', 'none')


def _dt(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def check_rows(rows: list[dict], *, max_per_advertiser: int | None, max_per_domain: int | None,
               formats: list[str] | None = None, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    problems: list[str] = []
    for key, n in Counter((r.get('advertiserId'), r.get('creativeId'), r.get('region')) for r in rows).items():
        if n > 1:
            problems.append(f'duplicate ad {"/".join(map(str, key))} appears {n} times')
    if max_per_advertiser is not None:
        for adv, n in Counter(r.get('advertiserId') for r in rows).items():
            if n > max_per_advertiser:
                problems.append(f'advertiser {adv} has {n} ads, above maxAdsPerAdvertiser {max_per_advertiser}')
    if max_per_domain is not None:
        for src, n in Counter(r.get('source') for r in rows if str(r.get('source', '')).startswith('domain:')).items():
            if n > max_per_domain:
                problems.append(f'{src} has {n} ads, above maxAdsPerDomain {max_per_domain}')
    for r in rows:
        rid = f'{r.get("advertiserId")}/{r.get("creativeId")}'
        if not str(r.get('advertiserId', '')).startswith('AR') or not str(r.get('creativeId', '')).startswith('CR'):
            problems.append(f'{rid}: advertiser or creative ID has the wrong shape')
        if r.get('format') not in FORMATS:
            problems.append(f'{rid}: format {r.get("format")!r} is not one of {FORMATS}')
        elif formats and r['format'] not in formats:
            problems.append(f'{rid}: format {r["format"]} was not requested')
        if r.get('contentStatus') not in CONTENT:
            problems.append(f'{rid}: contentStatus {r.get("contentStatus")!r} is unknown')
        first, last = _dt(r.get('firstShown')), _dt(r.get('lastShown'))
        if last is None:
            problems.append(f'{rid}: lastShown {r.get("lastShown")!r} is not ISO 8601')
        else:
            if last > now + timedelta(days=1):
                problems.append(f'{rid}: lastShown {r["lastShown"]} is in the future')
            if first is not None and first > last:
                problems.append(f'{rid}: firstShown {r["firstShown"]} is after lastShown {r["lastShown"]}')
        if not str(r.get('adUrl', '')).startswith('https://adstransparency.google.com/advertiser/'):
            problems.append(f'{rid}: adUrl {r.get("adUrl")!r} is not a Transparency Center link')
        for url in r.get('imageUrls') or []:
            if not str(url).startswith('https://'):
                problems.append(f'{rid}: image URL {url!r} is not https')
    return problems


async def check_image_urls(rows: list[dict], fetch_status: Callable[[str], Awaitable[int]],
                           sample: int = 10, seed: int = 0) -> list[str]:
    """Request a sample of image URLs and report any that do not answer HTTP 200."""
    urls = sorted({u for r in rows for u in (r.get('imageUrls') or [])})
    picked = random.Random(seed).sample(urls, min(sample, len(urls)))
    problems = []
    for url in picked:
        try:
            status = await fetch_status(url)
        except Exception as exc:  # noqa: BLE001
            problems.append(f'image URL {url} failed: {type(exc).__name__}')
            continue
        if status != 200:
            problems.append(f'image URL {url} answered HTTP {status}')
    return problems
