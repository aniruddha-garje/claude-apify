"""Parser tests against real responses saved from Apple and Google Play on 7 Oct 2026."""

from pathlib import Path

import pytest

from src.parsers import (
    REQUIRED_FIELDS,
    PlayResponseError,
    apple_feed_url,
    apple_web_reviews_url,
    parse_app,
    parse_apple_feed,
    parse_apple_web_reviews,
    parse_play_response,
    play_request_body,
)

FIX = Path(__file__).parent / 'fixtures'


def read(name: str) -> bytes:
    return (FIX / name).read_bytes()


def test_apple_page_parses_all_50_reviews_with_required_fields():
    rows = parse_apple_feed(read('apple_rss_us_310633997_p1.json'), '310633997', 'US', include_reviewer=False)
    assert len(rows) == 50
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert row[field] not in (None, ''), field
        assert 1 <= row['rating'] <= 5
        assert row['date'].endswith('Z')
        assert row['country'] == 'us'
        assert 'userName' not in row and 'userUrl' not in row
    assert len({r['reviewId'] for r in rows}) == 50


def test_apple_reviewer_info_only_when_asked():
    rows = parse_apple_feed(read('apple_rss_us_310633997_p1.json'), '310633997', 'us', include_reviewer=True)
    assert all(r['userName'] for r in rows)
    assert all(r['userUrl'].startswith('https://itunes.apple.com/') for r in rows)


def test_apple_last_page_and_unknown_app():
    assert len(parse_apple_feed(read('apple_rss_gb_310633997_p10_helpful.json'), '310633997', 'gb', False)) > 0
    assert parse_apple_feed(read('apple_rss_unknown_app.json'), '1', 'us', False) == []


def test_apple_feed_url_uses_sort_and_country():
    url = apple_feed_url('310633997', 'GB', 3, 'mostHelpful')
    assert url == 'https://itunes.apple.com/gb/rss/customerreviews/page=3/id=310633997/sortby=mosthelpful/json'


def test_play_page_parses_reviews_token_and_replies():
    rows, token, found = parse_play_response(
        read('play_batchexecute_spotify_star1_replies.txt'), 'com.spotify.music', 'en', 'us', include_reviewer=False
    )
    assert found and token
    assert len(rows) == 100
    assert {r['rating'] for r in rows} == {1}
    replied = [r for r in rows if r['developerReply']]
    assert replied and all(r['developerReplyDate'].endswith('Z') for r in replied)
    for row in rows:
        for field in REQUIRED_FIELDS:
            assert row[field] not in (None, ''), field
        assert 'reviewId=' + row['reviewId'] in row['reviewUrl']
        assert 'userName' not in row


def test_play_reviewer_info_only_when_asked():
    rows, _, _ = parse_play_response(read('play_batchexecute_whatsapp_newest_p1.txt'), 'com.whatsapp', 'en', 'us', True)
    assert len(rows) == 150
    assert all('userName' in r and r['userUrl'] is None for r in rows)


def test_play_language_is_carried():
    rows, _, _ = parse_play_response(read('play_batchexecute_duolingo_de.txt'), 'com.duolingo', 'de', 'de', False)
    assert rows and all(r['language'] == 'de' and r['country'] == 'de' for r in rows)


def test_play_unknown_app_is_reported_not_crashed():
    rows, token, found = parse_play_response(read('play_batchexecute_unknown_app.txt'), 'x.y', 'en', 'us', False)
    assert (rows, token, found) == ([], None, False)


def test_play_garbage_raises_for_retry():
    with pytest.raises(PlayResponseError):
        parse_play_response(b'<html>blocked</html>', 'x.y', 'en', 'us', False)


def test_play_request_body_has_star_filter_and_token():
    body = play_request_body('com.whatsapp', 'newest', 'TOKEN', 2, 50)
    assert 'UsvDTd' in body and 'TOKEN' in body and 'com.whatsapp' in body


@pytest.mark.parametrize(
    ('value', 'store', 'app_id'),
    [
        ('310633997', 'appStore', '310633997'),
        ('id310633997', 'appStore', '310633997'),
        ('https://apps.apple.com/us/app/whatsapp-messenger/id310633997', 'appStore', '310633997'),
        ('com.whatsapp', 'googlePlay', 'com.whatsapp'),
        ('https://play.google.com/store/apps/details?id=com.whatsapp&hl=en', 'googlePlay', 'com.whatsapp'),
    ],
)
def test_parse_app_accepts_ids_urls_and_packages(value, store, app_id):
    ref = parse_app(value)
    assert ref is not None and (ref.store, ref.app_id) == (store, app_id)


@pytest.mark.parametrize('value', ['', 'hello', 'https://example.com/app'])
def test_parse_app_rejects_garbage(value):
    assert parse_app(value) is None


# The App Store website's review route, real responses saved on 11 Oct 2026.

def test_apple_web_page_parses_20_reviews_into_the_same_schema():
    rows, more = parse_apple_web_reviews(read('apple_web_us_389801252_recent_offset0.json'), '389801252', 'US', False)
    assert len(rows) == 20 and more
    feed_keys = set(parse_apple_feed(read('apple_rss_us_310633997_p1.json'), '310633997', 'us', False)[0])
    for r in rows:
        assert set(r) == feed_keys  # one schema for both App Store sources
        assert all(r[f] is not None for f in REQUIRED_FIELDS)
        assert r['store'] == 'appStore' and r['country'] == 'us' and 1 <= r['rating'] <= 5
        assert r['date'].endswith('.000Z') and r['appVersion'] is None and r['thumbsUpCount'] is None
    assert [r['date'] for r in rows] == sorted((r['date'] for r in rows), reverse=True)  # newest first
    assert rows[0]['date'] == '2026-10-09T20:41:04.000Z'


def test_apple_web_last_page_and_reviewer_flag():
    rows, more = parse_apple_web_reviews(read('apple_web_us_441735034_recent_last.json'), '441735034', 'us', False)
    assert len(rows) == 1 and not more
    assert not any(f in rows[0] for f in ('userName', 'userUrl', 'userImage'))
    rows, _ = parse_apple_web_reviews(read('apple_web_us_441735034_recent_last.json'), '441735034', 'us', True)
    assert rows[0]['userName'] and rows[0]['userUrl'] is None


def test_apple_web_url_pages_by_offset_and_sorts():
    assert apple_web_reviews_url('389801252', 'GB', 40, 'newest') == (
        'https://apps.apple.com/api/apps/v1/catalog/gb/apps/389801252/reviews'
        '?l=en-US&offset=40&limit=20&platform=web&sort=recent')
    assert 'sort=' not in apple_web_reviews_url('389801252', 'us', 0, 'mostHelpful')
