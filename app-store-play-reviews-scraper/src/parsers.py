"""Pure parsing helpers for Apple App Store and Google Play reviews.

Nothing in this module does network I/O, so every function can be tested against saved responses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

SCHEMA_VERSION = 1

APPLE_MAX_PAGE = 10  # Apple's public feed answers HTTP 400 from page 11 on (probe 7 Oct 2026).
APPLE_PAGE_SIZE = 50
PLAY_PAGE_SIZE = 200

APPLE_SORT = {'newest': 'mostrecent', 'mostHelpful': 'mosthelpful', 'rating': 'mostrecent'}
# The App Store website's own review route (apps.apple.com forwards it to Apple's catalog API, so no token is needed).
# Probe 11 Oct 2026: 20 reviews per request at most (limit=50 answers HTTP 400); newest first with sort=recent; most
# helpful without a sort; offset 5,000 still answered for a large app, so it goes past the feed's 500 per country.
APPLE_WEB_PAGE_SIZE = 20
APPLE_WEB_SORT = {'newest': 'recent', 'mostHelpful': None, 'rating': 'recent'}
PLAY_SORT = {'mostHelpful': 1, 'newest': 2, 'rating': 3}

PLAY_BATCH_URL = 'https://play.google.com/_/PlayStoreUi/data/batchexecute'

_PACKAGE_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$')
_APPLE_ID_RE = re.compile(r'(?:^|/id|[?&]id=)(\d{5,})')


@dataclass(frozen=True)
class AppRef:
    store: str  # 'appStore' or 'googlePlay'
    app_id: str
    original: str


def parse_app(value: str) -> AppRef | None:
    """Turn an App Store ID or URL, or a Google Play package name or URL, into an AppRef."""
    raw = (value or '').strip()
    if not raw:
        return None
    if raw.isdigit():
        return AppRef('appStore', raw, value)
    if raw.lower().startswith(('http://', 'https://')):
        parsed = urlparse(raw)
        host = parsed.netloc.lower()
        if 'play.google.com' in host:
            pkg = parse_qs(parsed.query).get('id', [None])[0]
            if pkg and _PACKAGE_RE.match(pkg):
                return AppRef('googlePlay', pkg, value)
            return None
        if 'apple.com' in host:
            match = _APPLE_ID_RE.search(parsed.path + '?' + parsed.query)
            if match:
                return AppRef('appStore', match.group(1), value)
            return None
        return None
    if raw.lower().startswith('id') and raw[2:].isdigit():
        return AppRef('appStore', raw[2:], value)
    if _PACKAGE_RE.match(raw):
        return AppRef('googlePlay', raw, value)
    return None


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')


# ---------------------------------------------------------------- Apple


def apple_feed_url(app_id: str, country: str, page: int, sort: str) -> str:
    return (
        f'https://itunes.apple.com/{country.lower()}/rss/customerreviews/'
        f'page={page}/id={app_id}/sortby={APPLE_SORT.get(sort, "mostrecent")}/json'
    )


def apple_reviews_page_url(app_id: str, country: str) -> str:
    """Public page that lists the app's reviews. Apple has no per-review URL, so this is the working link."""
    return f'https://apps.apple.com/{country.lower()}/app/id{app_id}?see-all=reviews'


def _label(entry: dict, key: str) -> Any:
    node = entry.get(key)
    if isinstance(node, dict):
        return node.get('label')
    return None


def parse_apple_feed(body: str | bytes, app_id: str, country: str, include_reviewer: bool) -> list[dict]:
    """Parse one page of Apple's customer reviews feed into flat review rows."""
    data = json.loads(body)
    entries = data.get('feed', {}).get('entry') or []
    if isinstance(entries, dict):  # a feed with a single entry is not wrapped in a list
        entries = [entries]
    rows = []
    for entry in entries:
        if 'im:rating' not in entry:  # older feeds put an app-info entry first
            continue
        updated = _label(entry, 'updated')
        date = iso_utc(datetime.fromisoformat(updated)) if updated else None
        title = _label(entry, 'title') or ''
        text = (entry.get('content') or {}).get('label') or ''
        author = entry.get('author') or {}
        row = {
            'schemaVersion': SCHEMA_VERSION,
            'store': 'appStore',
            'appId': app_id,
            'country': country.lower(),
            'language': None,
            'reviewId': str(_label(entry, 'id')),
            'rating': int(_label(entry, 'im:rating')),
            'title': title,
            'text': text,
            'date': date,
            'appVersion': _label(entry, 'im:version'),
            'thumbsUpCount': int(_label(entry, 'im:voteSum') or 0),
            'voteCount': int(_label(entry, 'im:voteCount') or 0),
            'developerReply': None,
            'developerReplyDate': None,
            'reviewUrl': apple_reviews_page_url(app_id, country),
        }
        if include_reviewer:
            row['userName'] = (author.get('name') or {}).get('label')
            row['userUrl'] = (author.get('uri') or {}).get('label')
            row['userImage'] = None
        rows.append(row)
    return rows


def apple_web_reviews_url(app_id: str, country: str, offset: int, sort: str) -> str:
    query = f'l=en-US&offset={offset}&limit={APPLE_WEB_PAGE_SIZE}&platform=web'
    if APPLE_WEB_SORT.get(sort, 'recent'):
        query += f'&sort={APPLE_WEB_SORT.get(sort, "recent")}'
    return f'https://apps.apple.com/api/apps/v1/catalog/{country.lower()}/apps/{app_id}/reviews?{query}'


def parse_apple_web_reviews(body: str | bytes, app_id: str, country: str,
                            include_reviewer: bool) -> tuple[list[dict], bool]:
    """Parse one page of the App Store website's review route. Returns (rows, whether a next page exists).

    This source has no app version and no vote counts, so those fields are null. A developer reply is read if the
    page carries one (developerResponse); none appeared in 700 reviews of 6 apps checked on 11 Oct 2026."""
    data = json.loads(body)
    rows = []
    for item in data.get('data') or []:
        a = item.get('attributes') or {}
        if 'rating' not in a:
            continue
        reply = a.get('developerResponse') or {}
        row = {
            'schemaVersion': SCHEMA_VERSION,
            'store': 'appStore',
            'appId': app_id,
            'country': country.lower(),
            'language': None,
            'reviewId': str(item.get('id')),
            'rating': int(a['rating']),
            'title': a.get('title') or '',
            'text': a.get('review') or '',
            'date': iso_utc(datetime.fromisoformat(a['date'].replace('Z', '+00:00'))) if a.get('date') else None,
            'appVersion': None,
            'thumbsUpCount': None,
            'voteCount': None,
            'developerReply': reply.get('body'),
            'developerReplyDate': (iso_utc(datetime.fromisoformat(reply['modified'].replace('Z', '+00:00')))
                                   if reply.get('modified') else None),
            'reviewUrl': apple_reviews_page_url(app_id, country),
        }
        if include_reviewer:
            row['userName'] = a.get('userName')
            row['userUrl'] = None
            row['userImage'] = None
        rows.append(row)
    return rows, bool(data.get('next'))


# ---------------------------------------------------------------- Google Play


def play_request_body(package: str, sort: str, token: str | None, star: int | None, count: int = PLAY_PAGE_SIZE) -> str:
    """Form body for the public reviews RPC (UsvDTd) that the Play web page itself calls."""
    star_filter = [None, star] if star else []
    inner = json.dumps([None, None, [2, PLAY_SORT.get(sort, 2), [count, None, token], None, star_filter], [package, 7]])
    freq = json.dumps([[['UsvDTd', inner, None, 'generic']]])
    return urlencode({'f.req': freq})


def play_batch_url(language: str, country: str) -> str:
    return f'{PLAY_BATCH_URL}?{urlencode({"hl": language, "gl": country.lower()})}'


def play_review_url(package: str, review_id: str, language: str, country: str) -> str:
    return 'https://play.google.com/store/apps/details?' + urlencode(
        {'id': package, 'hl': language, 'gl': country.lower(), 'reviewId': review_id}
    )


class PlayResponseError(ValueError):
    pass


def _ts(node: Any) -> str | None:
    if isinstance(node, list) and node and isinstance(node[0], (int, float)):
        return iso_utc(datetime.fromtimestamp(node[0], tz=timezone.utc))
    return None


def _get(node: Any, *path: int) -> Any:
    for idx in path:
        if not isinstance(node, list) or idx >= len(node):
            return None
        node = node[idx]
    return node


def parse_play_response(
    body: str | bytes, package: str, language: str, country: str, include_reviewer: bool
) -> tuple[list[dict], str | None, bool]:
    """Parse a batchexecute response. Returns (rows, next_token, app_found)."""
    text = body.decode('utf-8') if isinstance(body, bytes) else body
    line = next((ln for ln in text.split('\n') if ln.startswith('[["wrb.fr"')), None)
    if line is None:
        raise PlayResponseError('Google Play response had no wrb.fr payload')
    outer = json.loads(line)
    payload_raw = outer[0][2]
    if payload_raw is None:
        raise PlayResponseError('Google Play returned an empty payload (likely an error for this request)')
    payload = json.loads(payload_raw)
    if not payload:
        return [], None, False
    reviews = payload[0] or []
    token = _get(payload, len(payload) - 1, 1)
    rows = []
    for review in reviews:
        reply = _get(review, 7)
        row = {
            'schemaVersion': SCHEMA_VERSION,
            'store': 'googlePlay',
            'appId': package,
            'country': country.lower(),
            'language': language,
            'reviewId': review[0],
            'rating': _get(review, 2),
            'title': None,
            'text': _get(review, 4) or '',
            'date': _ts(_get(review, 5)),
            'appVersion': _get(review, 10),
            'thumbsUpCount': _get(review, 6) or 0,
            'voteCount': None,
            'developerReply': _get(reply, 1) if reply else None,
            'developerReplyDate': _ts(_get(reply, 2)) if reply else None,
            'reviewUrl': play_review_url(package, review[0], language, country),
        }
        if include_reviewer:
            row['userName'] = _get(review, 1, 0)
            row['userUrl'] = None  # Google Play has no public reviewer profile page.
            row['userImage'] = _get(review, 1, 1, 3, 2)
        rows.append(row)
    return rows, token if isinstance(token, str) else None, True


REQUIRED_FIELDS = ('store', 'appId', 'country', 'reviewId', 'rating', 'date', 'reviewUrl')
