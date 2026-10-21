"""Delivery tests: caps are exact, duplicates are never pushed or charged, and a restart resumes cleanly."""

import json
from pathlib import Path

import pytest
from apify import Actor
from crawlee import service_locator

import apify._actor as actor_module

from src.delivery import Delivery
from src.parsers import parse_apple_feed

FIX = Path(__file__).parent / 'fixtures'
PRICING = {'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents': {
    'review': {'eventTitle': 'Review', 'eventPriceUsd': 0.0005},
    'apify-default-dataset-item': {'eventTitle': 'Dataset item', 'eventPriceUsd': 0},
}}}


@pytest.fixture
def ppe_env(monkeypatch, tmp_path):
    monkeypatch.setenv('ACTOR_TEST_PAY_PER_EVENT', 'true')
    monkeypatch.setenv('APIFY_ACTOR_PRICING_INFO', json.dumps(PRICING))
    monkeypatch.setenv('APIFY_CHARGED_ACTOR_EVENT_COUNTS', json.dumps({'review': 0, 'apify-default-dataset-item': 0}))
    monkeypatch.setenv('CRAWLEE_PURGE_ON_START', 'true')
    return tmp_path


def rows():
    return parse_apple_feed((FIX / 'apple_rss_us_310633997_p1.json').read_bytes(), '310633997', 'us', False)


def make():
    return Delivery('review', row_id=lambda r: f"{r['store']}|{r['appId']}|{r['country']}|{r['reviewId']}")


async def charged() -> int:
    return Actor.get_charging_manager().get_charged_event_count('review')


async def test_cap_is_exact_and_duplicates_are_not_charged(ppe_env):
    async with Actor:
        d = make()
        await d.load()
        assert await d.deliver(rows(), 'k', cap=30) == 30
        assert await d.deliver(rows(), 'k', cap=30) == 0  # cap reached
        assert await d.deliver(rows()[:10], 'other', cap=None) == 0  # same rows, other key: still duplicates
        assert await charged() == 30
        dataset = await Actor.open_dataset()
        assert (await dataset.get_data()).total == 30


async def test_restart_does_not_repeat_or_rebill(ppe_env, monkeypatch):
    async with Actor:
        d = make()
        await d.load()
        assert await d.deliver(rows()[:20], 'k', cap=None) == 20
    service_locator.__init__()
    actor_module.Actor.__wrapped__ = actor_module._ActorType(exit_process=False)
    # A new process after a migration or restart: same storage (not purged), state reloaded.
    monkeypatch.setenv('CRAWLEE_PURGE_ON_START', 'false')
    async with Actor:
        d = make()
        await d.load()
        assert await d.deliver(rows(), 'k', cap=None) == 30  # only the 30 not yet delivered
        assert await charged() == 30  # this run charges only its own new rows
        dataset = await Actor.open_dataset()
        assert (await dataset.get_data()).total == 50


async def test_charge_only_events_are_charged_once_and_respect_the_limit(ppe_env, monkeypatch):
    # Used for files saved outside the dataset (Ads images): one charge per key, never twice, never past the cap.
    monkeypatch.setenv('ACTOR_MAX_TOTAL_CHARGE_USD', '0.0015')  # room for 3 reviews at $0.0005
    async with Actor:
        d = make()
        await d.load()
        row = {'store': 'appStore', 'appId': '1', 'country': 'us', 'reviewId': 'a'}
        assert await d.deliver_charge_only(row) == 1
        assert await d.deliver_charge_only(row) == 0
        assert await d.deliver_charge_only({**row, 'reviewId': 'b'}) == 1
        assert await d.deliver_charge_only({**row, 'reviewId': 'c'}) == 1
        assert await d.deliver_charge_only({**row, 'reviewId': 'd'}) == 0
        assert d.budget_reached and await charged() == 3


async def test_spending_limit_stops_pushing(ppe_env, monkeypatch):
    monkeypatch.setenv('ACTOR_MAX_TOTAL_CHARGE_USD', '0.004')
    async with Actor:
        d = make()
        await d.load()
        assert await d.deliver(rows(), 'k', cap=None) == 8
        assert d.budget_reached
        dataset = await Actor.open_dataset()
        assert (await dataset.get_data()).total == 8
