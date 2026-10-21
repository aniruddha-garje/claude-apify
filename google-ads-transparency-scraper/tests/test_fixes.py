"""Pre-launch fix (8 Oct 2026): a name search returns every candidate advertiser with a match score."""

from datetime import date
from pathlib import Path

import pytest

from src.main import InputError, read_input
from src.parsers import match_score, parse_suggestions, rank_candidates

FIX = Path(__file__).parent / 'fixtures'
NIKE = parse_suggestions((FIX / 'search_suggestions_nike.json').read_bytes())  # real response, 7 Oct 2026
TODAY = date(2026, 10, 8)


def test_suggestions_carry_googles_ad_count_range():
    inc = next(a for a in NIKE if a['advertiserId'] == 'AR16735076323512287233')
    assert (inc['advertiserName'], inc['adsMin'], inc['adsMax']) == ('Nike, Inc.', 9000, 10000)


def test_the_old_substring_rule_missed_the_real_brand():
    # Google lists Nike, Inc. eighth; "first five names containing the term" never reached it.
    old = [a['advertiserName'] for a in NIKE if 'nike' in a['advertiserName'].lower()][:5]
    assert 'Nike, Inc.' not in old


def test_exact_names_rank_first_and_the_biggest_wins_ties():
    ranked = rank_candidates('nike', NIKE, min_score=1.0, limit=3)
    assert ranked[0]['advertiserName'] == 'Nike, Inc.' and ranked[0]['matchScore'] == 1.0
    assert [a['selected'] for a in ranked].count(True) == 3
    assert all(a['matchScore'] == 1.0 for a in ranked if a['selected'])
    assert len(ranked) == len(NIKE)  # every candidate is returned, not only the selected ones


def test_near_names_score_below_one_and_are_not_scraped_by_default():
    ranked = {a['advertiserName']: a for a in rank_candidates('nike', NIKE, min_score=1.0, limit=10)}
    for name in ('Nikena', 'nikey', 'Individual advertiser B'):
        assert ranked[name]['matchScore'] < 1.0 and not ranked[name]['selected']


@pytest.mark.parametrize('term, name, score', [
    ('nike', 'Nike, Inc.', 1.0), ('hubspot', 'HubSpot, Inc.', 1.0), ('ikea', 'IKEA B.V.', 1.0),
    ('ikea deutschland', 'IKEA Deutschland GmbH & Co. KG', 1.0), ('nike', 'Nikena', 0.8),
])
def test_match_score_ignores_legal_forms(term, name, score):
    assert match_score(term, name) == score


def test_no_match_selects_nothing():
    assert not any(a['selected'] for a in rank_candidates('adidas', NIKE, min_score=1.0, limit=3))


@pytest.mark.parametrize('cfg, says', [
    ({'minMatchScore': 1.5}, 'between 0 and 1'), ({'maxAdvertisersPerTerm': 0}, 'between 1 and 10'),
])
def test_bad_match_settings_are_rejected_with_an_example(cfg, says):
    with pytest.raises(InputError) as exc:
        read_input({'searchTerms': ['nike'], **cfg}, TODAY)
    assert says in str(exc.value) and 'xample' in str(exc.value)
