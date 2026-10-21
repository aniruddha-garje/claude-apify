"""maxAdsPerDomain: exact per-domain caps that survive in-flight previews and restarts."""

from datetime import date

import pytest

from src.limits import DomainLimiter
from src.main import InputError, read_input

TODAY = date(2026, 10, 7)


def test_default_is_200_and_zero_means_no_limit():
    assert read_input({'domains': ['nike.com']}, TODAY)['maxDomain'] == 200
    assert read_input({'domains': ['nike.com'], 'maxAdsPerDomain': 0}, TODAY)['maxDomain'] is None
    assert read_input({'domains': ['nike.com'], 'maxAdsPerDomain': 25}, TODAY)['maxDomain'] == 25


def test_negative_cap_is_rejected():
    with pytest.raises(InputError):
        read_input({'domains': ['nike.com'], 'maxAdsPerDomain': -1}, TODAY)


def test_cap_counts_queued_previews_so_requests_never_overshoot():
    lim = DomainLimiter({}, cap=3)
    src = 'domain:nike.com'
    for _ in range(3):
        assert lim.room(src)
        lim.reserve(src)
    assert not lim.room(src) and lim.full(src)
    lim.release(src)  # one preview delivered
    lim.record(src, 1)
    assert not lim.room(src)  # 1 delivered + 2 still pending = 3


def test_delivery_stops_exactly_at_the_cap():
    lim = DomainLimiter({}, cap=2)
    src = 'domain:nike.com'
    delivered = 0
    for _ in range(5):
        if lim.can_deliver(src):
            lim.record(src, 1)
            delivered += 1
    assert delivered == 2 and lim.counts == {src: 2}


def test_domains_are_independent_and_advertiser_sources_are_not_limited():
    lim = DomainLimiter({}, cap=1)
    lim.record('domain:a.com', 1)
    assert not lim.can_deliver('domain:a.com')
    assert lim.can_deliver('domain:b.com')
    assert lim.can_deliver('advertiser:AR1') and lim.room('term:nike')


def test_restart_keeps_per_domain_totals():
    saved = {'domain:nike.com': 2, 'AR16735076323512287233': 2}  # as reloaded from DELIVERY_STATE_AD
    lim = DomainLimiter(saved, cap=2)
    assert not lim.can_deliver('domain:nike.com')


def test_no_cap_never_blocks():
    lim = DomainLimiter({}, cap=None)
    lim.record('domain:a.com', 10_000)
    assert lim.can_deliver('domain:a.com') and lim.room('domain:a.com')
