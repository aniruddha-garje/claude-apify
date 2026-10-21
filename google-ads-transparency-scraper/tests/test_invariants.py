"""Invariant rules: real parsed ads pass, and every rule catches the problem it guards against."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.invariants import check_image_urls, check_rows
from src.main import _image_only_content
from src.parsers import parse_creatives
from src.run_checks import compare, profile

FIX = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)


def real_rows():
    rows, _ = parse_creatives((FIX / 'search_creatives_domain_nike_com.json').read_bytes(), 'US')
    for r in rows:
        r['source'] = 'domain:nike.com'
        r.update(_image_only_content(r))
    return rows


def check(rows, **kw):
    args = {'max_per_advertiser': 100, 'max_per_domain': 200, 'now': NOW, **kw}
    return check_rows(rows, **args)


def test_every_saved_real_page_passes_all_rules():
    assert check(real_rows()) == []


@pytest.mark.parametrize(('mutate', 'expected'), [
    (lambda rows: rows + [rows[0]], 'duplicate ad'),
    (lambda rows: [{**rows[0], 'format': 'banner'}] + rows[1:], 'format'),
    (lambda rows: [{**rows[0], 'firstShown': '2026-10-07T00:00:00Z', 'lastShown': '2026-10-01T00:00:00Z'}] + rows[1:],
     'is after lastShown'),
    (lambda rows: [{**rows[0], 'lastShown': '2027-01-01T00:00:00Z'}] + rows[1:], 'in the future'),
    (lambda rows: [{**rows[0], 'lastShown': 'Oct 7'}] + rows[1:], 'not ISO 8601'),
    (lambda rows: [{**rows[0], 'creativeId': 'X1'}] + rows[1:], 'wrong shape'),
    (lambda rows: [{**rows[0], 'contentStatus': 'maybe'}] + rows[1:], 'contentStatus'),
    (lambda rows: [{**rows[0], 'imageUrls': ['http://x/y.png']}] + rows[1:], 'not https'),
])
def test_each_rule_catches_its_problem(mutate, expected):
    problems = check(mutate(real_rows()))
    assert any(expected in p for p in problems), problems


def test_caps_per_advertiser_and_per_domain():
    rows = real_rows()
    assert any('above maxAdsPerAdvertiser' in p for p in check(rows, max_per_advertiser=2))
    assert any('above maxAdsPerDomain' in p for p in check(rows, max_per_domain=5))


def test_requested_formats_only():
    assert any('was not requested' in p for p in check(real_rows(), formats=['video']))


async def test_image_url_check_reports_non_200():
    rows = real_rows()

    async def status(url):
        return 404 if url.endswith('1') else 200

    problems = await check_image_urls(rows, status, sample=40)
    assert problems and all('HTTP 404' in p for p in problems)

    async def ok(url):
        return 200

    assert await check_image_urls(rows, ok) == []


def test_canary_detects_content_status_mix_shift():
    rows = real_rows()
    base = profile(rows, ['headline', 'imageUrl'], 'contentStatus')
    shifted = [{**r, 'contentStatus': 'notAvailable'} if i % 4 == 0 else r for i, r in enumerate(rows)]
    drift = compare(profile(shifted, ['headline', 'imageUrl'], 'contentStatus'), base, 5)
    assert any('contentStatus=notAvailable' in d for d in drift)
