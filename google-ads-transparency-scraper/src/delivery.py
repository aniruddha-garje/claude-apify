"""Row delivery: dedupe, cap, push to the dataset, then charge only for what was pushed.

The state (rows already delivered and per-key counters) is persisted to the key-value store, so a
restarted or migrated run does not deliver or bill the same row twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from apify import Actor



class Delivery:
    def __init__(self, event_name: str, row_id: Callable[[dict], str]) -> None:
        self.event_name = event_name
        self.state_key = f'DELIVERY_STATE_{event_name.upper().replace("-", "_")}'
        self.row_id = row_id
        self.seen: set[str] = set()
        self.counts: dict[str, int] = {}
        self.notes: dict[str, str] = {}
        self.errors: list[dict[str, Any]] = []
        self.charged = 0
        self.free = 0  # rows delivered without a charge (for example hotels with no price)
        self.budget_reached = False
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        state = await Actor.get_value(self.state_key) or {}
        self.seen = set(state.get('seen', []))
        self.counts = dict(state.get('counts', {}))
        self.notes = dict(state.get('notes', {}))
        self.errors = list(state.get('errors', []))
        self.charged = int(state.get('charged', 0))
        self.free = int(state.get('free', 0))
        if self.seen:
            Actor.log.info(f'Resumed: {len(self.seen)} rows were already delivered before the restart and will not be repeated.')

    async def save(self) -> None:
        await Actor.set_value(
            self.state_key,
            {
                'seen': sorted(self.seen),
                'counts': self.counts,
                'notes': self.notes,
                'errors': self.errors[-500:],
                'charged': self.charged,
                'free': self.free,
            },
        )

    def remaining(self, key: str, cap: int | None) -> int | None:
        if cap is None:
            return None
        return max(0, cap - self.counts.get(key, 0))

    async def deliver(self, rows: list[dict], key: str, cap: int | None, charge: bool = True) -> int:
        """Push new rows for `key` (respecting `cap` and the user's spending limit) and charge for them.

        With charge=False the rows are pushed and counted toward `cap` but never charged.
        """
        async with self._lock:
            fresh = []
            for row in rows:
                rid = self.row_id(row)
                if rid in self.seen:
                    continue
                self.seen.add(rid)  # also dedupes repeats within the same batch
                fresh.append((rid, row))
            room = self.remaining(key, cap)
            if room is not None:
                for rid, _ in fresh[room:]:
                    self.seen.discard(rid)
                fresh = fresh[:room]
            if not fresh:
                return 0
            if not charge:
                await Actor.push_data([row for _, row in fresh])
                self.free += len(fresh)
                self.counts[key] = self.counts.get(key, 0) + len(fresh)
                await self.save()
                return len(fresh)

            manager = Actor.get_charging_manager()
            allowed = manager.compute_push_data_limit(len(fresh), self.event_name, is_default_dataset=True)
            if allowed < len(fresh):
                for rid, _ in fresh[allowed:]:
                    self.seen.discard(rid)
                fresh = fresh[:allowed]
                self.budget_reached = True
            if not fresh:
                return 0

            items = [row for _, row in fresh]
            await Actor.push_data(items)
            # Charge only after the rows are safely in the dataset.
            result = await Actor.charge(event_name=self.event_name, count=len(items))
            if result is not None and getattr(result, 'event_charge_limit_reached', False):
                self.budget_reached = True
            self.charged += len(items)
            self.counts[key] = self.counts.get(key, 0) + len(items)
            await self.save()
            return len(items)

    async def deliver_charge_only(self, row: dict) -> int:
        """Charge for something already stored elsewhere (for example a file), once, within the spending limit."""
        async with self._lock:
            rid = self.row_id(row)
            if rid in self.seen:
                return 0
            allowed = Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(self.event_name)
            if allowed is not None and allowed < 1:
                self.budget_reached = True
                return 0
            await Actor.charge(event_name=self.event_name, count=1)
            self.seen.add(rid)
            self.charged += 1
            await self.save()
            return 1

    def record_error(self, key: str, url: str, message: str) -> None:
        self.errors.append({'key': key, 'url': url, 'error': message[:500]})
