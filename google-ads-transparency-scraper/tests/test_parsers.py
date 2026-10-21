"""Ads Transparency parser and input tests against real responses saved on 7 Oct 2026."""

import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.main import InputError, read_input
from src.parsers import (
    REQUIRED_FIELDS,
    RpcError,
    creatives_request,
    parse_creative_detail,
    parse_creatives,
    parse_preview,
    parse_suggestions,
    region_code,
)

FIX = Path(__file__).parent / 'fixtures'
TODAY = date(2026, 10, 7)


def raw(name: str) -> bytes:
    data = (FIX / name).read_bytes()
    return gzip.decompress(data) if name.endswith('.gz') else data


def test_creatives_page_rows_are_complete():
    rows, token = parse_creatives(raw('search_creatives_domain_nike_com.json'), 'US')
    assert len(rows) == 40 and token
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert row[field] not in (None, ''), field
        assert row['lastShown'].endswith('Z') and row['firstShown'].endswith('Z')
        assert row['adUrl'].startswith('https://adstransparency.google.com/advertiser/AR')
        assert row['imageUrl'] or row['previewUrl']
    assert len({r['creativeId'] for r in rows}) == 40


def test_suggestions_give_advertisers():
    found = parse_suggestions(raw('search_suggestions_nike.json'))
    slim = [{k: a[k] for k in ('advertiserId', 'advertiserName', 'country')} for a in found]  # ad counts tested in test_fixes
    assert {'advertiserId': 'AR16735076323512287233', 'advertiserName': 'Nike, Inc.', 'country': 'US'} in slim


def test_creative_detail_variations_and_regions():
    detail = parse_creative_detail(raw('creative_detail.json'))
    assert len(detail['variations']) == 2 and detail['regionStats'][0]['region'] == 'CA'


def test_preview_content_shopping_ad():
    content = parse_preview(raw('preview_content_shopping.js.gz').decode())
    assert content['headline'].startswith('Nike Toddler Quilted Faux Fur Jacket')
    assert content['imageUrls'] and content['imageUrls'][0].startswith('https://encrypted-tbn')
    assert not any('invokeResponseCallback' in t for t in content['texts'])


def test_preview_content_local_ad_has_text_and_display_url():
    content = parse_preview(raw('preview_content_local.js.gz').decode())
    assert content['headline'] == 'Nike Factory Store - Sawgrass Mills'
    assert 'Fleece' in content['description']
    assert content['displayUrl'] == 'www.nike.com/'
    assert content['imageUrls'][0].startswith('https://tpc.googlesyndication.com/simgad/')


def test_rate_limit_page_is_an_error_not_empty_data():
    with pytest.raises(RpcError):
        parse_creatives(b'<!DOCTYPE html><html><title>Error 429</title></html>', 'US')


def test_request_shapes():
    req = creatives_request(domain='nike.com', region=2840, date_from=date(2026, 9, 1), date_to=date(2026, 10, 1),
                            format_code=2, page_token='T')
    assert req['3'] == {'4': 2, '6': 20260901, '7': 20261001, '8': [2840], '12': {'1': 'nike.com', '2': True}}
    assert req['4'] == 'T' and json.dumps(req)


def test_platform_filter_matches_the_transparency_centers_own_request():
    # The site's own request when YouTube is picked in its platform filter (captured 7 Oct 2026): "14": [5].
    req = creatives_request(advertiser_id='AR16735076323512287233', region=2840, platform_code=5)
    assert req['3']['14'] == [5]
    p = read_input({'domains': ['nike.com'], 'platforms': ['youtube', 'search']}, TODAY)
    assert sorted(pl for _, pl in p['streams']) == [3, 5]
    assert read_input({'domains': ['nike.com']}, TODAY)['streams'] == [(None, None)]


def test_region_codes():
    assert region_code('US') == 2840 and region_code('in') == 2356 and region_code('anywhere') is None
    with pytest.raises(ValueError):
        region_code('XX')


def test_defaults():
    p = read_input({'domains': ['https://www.Nike.com/us']}, TODAY)
    assert p['domains'] == ['nike.com'] and p['formatCodes'] == [None]
    assert (p['dateFrom'], p['dateTo']) == (date(2026, 9, 7), TODAY) and p['maxAds'] == 100


@pytest.mark.parametrize('cfg', [
    {},
    {'advertiserIds': ['nike']},
    {'domains': ['x.com'], 'region': 'Narnia'},
    {'domains': ['x.com'], 'formats': ['audio']},
    {'domains': ['x.com'], 'platforms': ['tiktok']},
    {'domains': ['x.com'], 'dateFrom': '2026-10-05', 'dateTo': '2026-10-01'},
])
def test_bad_inputs_fail_fast(cfg):
    with pytest.raises(InputError):
        read_input(cfg, TODAY)


def test_preview_of_externally_hosted_video_is_reported_not_retried():
    content = parse_preview(raw('preview_content_external_video.js.gz').decode())
    assert content['previewType'] == 'externallyHosted'
    assert content['externalContentUrl'].startswith('https://displayads-formats.googleusercontent.com/ads/preview/content.html')


def test_non_preview_text_asks_for_retry():
    assert parse_preview('<html>rate limited</html>') == {}


def test_displayed_dates_follow_us_pacific_time_like_the_site():
    # 2026-09-17T06:44:48Z is shown on the site as "Last shown: 16 Sept 2026" (golden capture, 7 Oct 2026).
    from src.parsers import pacific_date
    assert pacific_date(int(datetime(2026, 9, 17, 6, 44, 48, tzinfo=timezone.utc).timestamp())) == '2026-09-16'
    # Winter (PST, UTC-8): 2026-02-05T07:59:52Z is shown as "4 Feb 2026".
    assert pacific_date(int(datetime(2026, 2, 5, 7, 59, 52, tzinfo=timezone.utc).timestamp())) == '2026-02-04'
    assert pacific_date(int(datetime(2026, 10, 7, 15, 54, 58, tzinfo=timezone.utc).timestamp())) == '2026-10-07'
    rows, _ = parse_creatives(raw('search_creatives_domain_nike_com.json'), 'US')
    assert all(r['lastShownDate'] and r['firstShownDate'] <= r['lastShownDate'] for r in rows)


def test_the_sponsored_label_in_another_language_is_not_the_headline():
    """Real preview of creative CR16542342414883356673 (nike.com, France), saved 11 Oct 2026."""
    content = parse_preview(raw('preview_sponsored_label_fr.js.gz').decode('utf-8', 'replace'))
    assert content['headline'] == 'Nike'
    assert 'Sponsorisé' not in content['texts']
    assert content['description'].startswith('Nike Store')


def test_map_card_buttons_are_not_ad_text():
    """Real preview of creative CR15343210085828526081 (nike.com, Germany), saved 11 Oct 2026."""
    content = parse_preview(raw('preview_map_buttons_de.js.gz').decode('utf-8', 'replace'))
    assert content['headline'] == 'Nike Store'
    assert not {'Cancel', 'Add Stop', 'Gesponsert'} & set(content['texts'])


def test_escaped_line_breaks_are_not_text():
    """Real preview of creative CR17291474055215448065 (netflix.com video ad), saved 11 Oct 2026."""
    content = parse_preview(raw('preview_escaped_newlines.js.gz').decode('utf-8', 'replace'))
    assert content['headline'] is None and content['texts'] == []
