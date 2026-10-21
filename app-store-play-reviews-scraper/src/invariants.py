"""Rules every delivered review must satisfy. Checked in the tests and at the end of every run.

check_rows() is pure, so the same rules run on saved real responses in tests and on the dataset after a run.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

REVIEWER_FIELDS = ('userName', 'userUrl', 'userImage')


def _dt(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def check_rows(rows: list[dict], *, countries: list[str], cap: int | None, include_reviewer: bool,
               now: datetime | None = None) -> list[str]:
    now = now or datetime.now(timezone.utc)
    problems: list[str] = []
    keys = Counter((r.get('store'), r.get('appId'), r.get('country'), r.get('reviewId')) for r in rows)
    for key, n in keys.items():
        if n > 1:
            problems.append(f'duplicate review {"/".join(map(str, key))} appears {n} times')
    per_key = Counter((r.get('store'), r.get('appId'), r.get('country'), r.get('language')) for r in rows)
    if cap is not None:
        for key, n in per_key.items():
            if n > cap:
                problems.append(f'{"/".join(map(str, key))} has {n} reviews, above the cap of {cap}')
    for r in rows:
        rid = f'{r.get("store")}/{r.get("appId")}/{r.get("reviewId")}'
        if r.get('store') not in ('appStore', 'googlePlay'):
            problems.append(f'{rid}: unknown store {r.get("store")!r}')
        if not isinstance(r.get('rating'), int) or not 1 <= r['rating'] <= 5:
            problems.append(f'{rid}: rating {r.get("rating")!r} is not 1 to 5')
        d = _dt(r.get('date'))
        if d is None:
            problems.append(f'{rid}: date {r.get("date")!r} is not ISO 8601')
        elif not datetime(2008, 7, 1, tzinfo=timezone.utc) <= d <= now + timedelta(days=1):
            problems.append(f'{rid}: date {r["date"]} is outside 2008 to now')
        if r.get('country') not in countries:
            problems.append(f'{rid}: country {r.get("country")!r} was not requested')
        if not str(r.get('reviewUrl', '')).startswith('https://'):
            problems.append(f'{rid}: reviewUrl {r.get("reviewUrl")!r} is not an https link')
        if r.get('developerReplyDate') and _dt(r['developerReplyDate']) is None:
            problems.append(f'{rid}: developerReplyDate {r["developerReplyDate"]!r} is not ISO 8601')
        if not include_reviewer and any(f in r for f in REVIEWER_FIELDS):
            problems.append(f'{rid}: reviewer fields present although includeReviewerInfo is off')
    return problems
