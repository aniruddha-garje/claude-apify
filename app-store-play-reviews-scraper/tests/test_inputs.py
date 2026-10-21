"""Bad inputs are rejected before any request, with a message that says what was wrong and shows a valid example."""

import pytest

from src.main import InputError, read_input


@pytest.mark.parametrize('cfg, says', [
    ({}, 'Add at least one app'),
    ({'apps': ['not an app!']}, 'None of these is an App Store ID'),
    ({'apps': ['310633997'], 'stores': ['amazon']}, 'Unknown stores'),
    ({'apps': ['310633997'], 'countries': ['usa']}, 'two-letter'),
    ({'apps': ['310633997'], 'sort': 'oldest'}, 'Unknown sort'),
    ({'apps': ['310633997'], 'maxReviewsPerApp': -1}, '0 (no limit) or more'),
    ({'apps': ['310633997'], 'ratings': ['6']}, 'stars from 1 to 5'),
    ({'apps': ['310633997'], 'since': 'last week'}, 'ISO date'),
])
def test_bad_input_is_rejected_with_an_example(cfg, says):
    with pytest.raises(InputError) as exc:
        read_input(cfg)
    assert says in str(exc.value) and 'xample' in str(exc.value)


def test_good_input_and_a_mixed_list_pass():
    read_input({'apps': ['310633997', 'com.whatsapp', 'not an app!'], 'countries': ['us', 'gb'], 'since': '2026-09-01'})
