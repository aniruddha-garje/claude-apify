"""Invariant rules: real parsed reviews pass, and every rule catches the problem it guards against."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.invariants import check_rows
from src.parsers import parse_apple_feed, parse_play_response
from src.run_checks import compare, profile

FIX = Path(__file__).parent / 'fixtures'
NOW = datetime(2026, 10, 7, 20, 0, tzinfo=timezone.utc)


def real_rows(include_reviewer=False):
    apple = parse_apple_feed((FIX / 'apple_rss_us_310633997_p1.json').read_bytes(), '310633997', 'us', include_reviewer)
    play, _, _ = parse_play_response((FIX / 'play_batchexecute_spotify_star1_replies.txt').read_bytes(),
                                     'com.spotify.music', 'en', 'us', include_reviewer)
    return apple + play


def check(rows, **kw):
    args = {'countries': ['us'], 'cap': 500, 'include_reviewer': False, 'now': NOW, **kw}
    return check_rows(rows, **args)


def test_every_saved_real_page_passes_all_rules():
    assert check(real_rows()) == []
    assert check(real_rows(True), include_reviewer=True) == []


@pytest.mark.parametrize(('mutate', 'expected'), [
    (lambda rows: rows + [rows[0]], 'duplicate review'),
    (lambda rows: [{**rows[0], 'rating': 6}] + rows[1:], 'rating 6'),
    (lambda rows: [{**rows[0], 'date': 'yesterday'}] + rows[1:], 'not ISO 8601'),
    (lambda rows: [{**rows[0], 'date': '2031-01-01T00:00:00.000Z'}] + rows[1:], 'outside 2008 to now'),
    (lambda rows: [{**rows[0], 'country': 'fr'}] + rows[1:], 'was not requested'),
    (lambda rows: [{**rows[0], 'reviewUrl': 'about:blank'}] + rows[1:], 'not an https link'),
    (lambda rows: [{**rows[0], 'userName': 'x'}] + rows[1:], 'reviewer fields present'),
])
def test_each_rule_catches_its_problem(mutate, expected):
    problems = check(mutate(real_rows()))
    assert any(expected in p for p in problems), problems


def test_cap_rule():
    rows = real_rows()
    assert any('above the cap' in p for p in check(rows, cap=10))


def test_canary_flags_null_rate_moves_above_5_points_only():
    rows = real_rows()
    base = profile(rows, ['appVersion', 'developerReply'], 'store')
    assert compare(base, base, 5) == []
    worse = [{**r, 'appVersion': None} if i % 3 == 0 else r for i, r in enumerate(rows)]
    drift = compare(profile(worse, ['appVersion', 'developerReply'], 'store'), base, 5)
    assert any('appVersion' in d for d in drift)
    slight = [{**r, 'appVersion': None} if i == 0 else r for i, r in enumerate(rows)]
    assert compare(profile(slight, ['appVersion', 'developerReply'], 'store'), base, 5) == []
