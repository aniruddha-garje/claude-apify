"""Counts every HTTP attempt, including retries, so a run reports its real success rate."""

from __future__ import annotations

from collections import Counter
from typing import Any

from crawlee.crawlers import BasicCrawler


class AttemptStats:
    def __init__(self) -> None:
        self.failed_attempts = 0
        self.errors: Counter[str] = Counter()

    def attach(self, crawler: BasicCrawler) -> None:
        @crawler.error_handler
        async def _on_error(_ctx: Any, error: Exception) -> None:
            self.failed_attempts += 1
            self.errors[type(error).__name__ + ': ' + str(error)[:80]] += 1

    def summary(self, final_stats: Any) -> dict:
        finished = getattr(final_stats, 'requests_finished', 0) or 0
        failed = getattr(final_stats, 'requests_failed', 0) or 0
        failed_attempts = self.failed_attempts + failed  # retried attempts plus each request's last failed attempt
        attempts = finished + failed_attempts
        return {
            'requestsSucceeded': finished,
            'requestsFailed': failed,
            'attempts': attempts,
            'failedAttempts': failed_attempts,
            'attemptSuccessRate': round(finished / attempts, 4) if attempts else None,
            'requestSuccessRate': round(finished / (finished + failed), 4) if finished + failed else None,
            'topErrors': self.errors.most_common(5),
        }
