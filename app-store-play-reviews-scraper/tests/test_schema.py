"""The input form never carries a stale value, and reviewer details are off unless asked for."""

import json
import re
from pathlib import Path

from src.parsers import parse_apple_feed, parse_play_response

ROOT = Path(__file__).parent.parent
SCHEMA = json.loads((ROOT / '.actor' / 'input_schema.json').read_text(encoding='utf-8'))
DATE_LIKE = re.compile(r'\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{2,4}')
FIX = Path(__file__).parent / 'fixtures'


def test_no_prefill_or_default_contains_a_date():
    for name, prop in SCHEMA['properties'].items():
        for key in ('prefill', 'default'):
            if key in prop:
                assert not DATE_LIKE.search(json.dumps(prop[key])), f'{name}.{key} holds a fixed date'


def test_reviewer_info_flag_defaults_to_off():
    prop = SCHEMA['properties']['includeReviewerInfo']
    assert prop['type'] == 'boolean' and prop['default'] is False and 'prefill' not in prop


def test_main_reads_the_flag_with_a_false_default():
    source = (ROOT / 'src' / 'main.py').read_text(encoding='utf-8')
    assert "cfg.get('includeReviewerInfo', False)" in source


def test_rows_carry_no_reviewer_fields_unless_the_flag_is_on():
    apple = parse_apple_feed((FIX / 'apple_rss_us_310633997_p1.json').read_bytes(), '310633997', 'us', False)
    play, _, _ = parse_play_response((FIX / 'play_batchexecute_whatsapp_newest_p1.txt').read_bytes(), 'com.whatsapp',
                                     'en', 'us', False)
    for row in apple + play:
        assert not {'userName', 'userUrl', 'userImage'} & set(row)
        assert not any(isinstance(v, str) and ('/reviews/id' in v or 'googleusercontent.com/a/' in v) for v in row.values())
