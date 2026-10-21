"""Per-domain cap (maxAdsPerDomain): counts delivered ads per domain plus previews still in flight."""

from __future__ import annotations


class DomainLimiter:
    """Keeps `delivered + pending` at or below the cap for each domain source.

    `counts` is the delivery state's persisted counter dict, so a restarted run keeps its per-domain totals.
    Sources that are not domains (advertiser IDs, names) are never limited here.
    """

    PREFIX = 'domain:'

    def __init__(self, counts: dict[str, int], cap: int | None) -> None:
        self.counts = counts
        self.cap = cap
        self.pending: dict[str, int] = {}

    def applies(self, source: str) -> bool:
        return self.cap is not None and source.startswith(self.PREFIX)

    def room(self, source: str) -> bool:
        if not self.applies(source):
            return True
        return self.counts.get(source, 0) + self.pending.get(source, 0) < self.cap

    def reserve(self, source: str) -> None:
        """A preview was queued for this domain; hold its slot until it is delivered or dropped."""
        if self.applies(source):
            self.pending[source] = self.pending.get(source, 0) + 1

    def release(self, source: str) -> None:
        if self.applies(source):
            self.pending[source] = max(0, self.pending.get(source, 0) - 1)

    def can_deliver(self, source: str) -> bool:
        return not self.applies(source) or self.counts.get(source, 0) < self.cap

    def record(self, source: str, delivered: int) -> None:
        if self.applies(source) and delivered:
            self.counts[source] = self.counts.get(source, 0) + delivered

    def full(self, source: str) -> bool:
        return self.applies(source) and not self.room(source)
