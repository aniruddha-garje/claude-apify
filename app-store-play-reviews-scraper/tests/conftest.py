"""Give every test a fresh Actor and fresh storage, as a new run on the platform would have."""

import pytest
from crawlee import service_locator

import apify._actor as actor_module


@pytest.fixture(autouse=True)
def fresh_actor(monkeypatch, tmp_path):
    monkeypatch.setenv('CRAWLEE_STORAGE_DIR', str(tmp_path / 'storage'))
    service_locator.__init__()
    actor_module.Actor.__wrapped__ = actor_module._ActorType(exit_process=False)
    yield
    service_locator.__init__()
