"""Pure helpers for the Google Ads Transparency Center's public RPC endpoints.

The Transparency Center web app calls these endpoints itself; no login or browser is needed.
Nothing here does network I/O, so every function is tested against saved responses.
"""

from __future__ import annotations

import difflib

import html as htmlmod
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

SCHEMA_VERSION = 1
# Overridable only for local replay tests that serve saved real responses (scripts/replay_ads_rpc.py).
RPC_BASE = os.environ.get('ADS_RPC_BASE_URL', 'https://adstransparency.google.com/anji/_/rpc/')
RPC_HEADERS = {
    'Content-Type': 'application/x-www-form-urlencoded',
    'Origin': 'https://adstransparency.google.com',
    'Referer': 'https://adstransparency.google.com/',
}
PAGE_SIZE = 100

# Creative format codes used by the Transparency Center (checked with its own format filter, 7 Oct 2026).
FORMATS = {1: 'text', 2: 'image', 3: 'video'}
FORMAT_CODES = {v: k for k, v in FORMATS.items()}
# Platform codes the Transparency Center's own platform filter sends (read from its requests, 7 Oct 2026).
PLATFORMS = {1: 'play', 2: 'maps', 3: 'search', 4: 'shopping', 5: 'youtube'}
PLATFORM_CODES = {v: k for k, v in PLATFORMS.items()}

# ISO 3166-1 numeric codes. Google's country criterion id is 2000 + this number (US 840 -> 2840).
ISO_NUMERIC = {
    'AD': 20, 'AE': 784, 'AF': 4, 'AG': 28, 'AI': 660, 'AL': 8, 'AM': 51, 'AO': 24, 'AQ': 10, 'AR': 32, 'AS': 16,
    'AT': 40, 'AU': 36, 'AW': 533, 'AX': 248, 'AZ': 31, 'BA': 70, 'BB': 52, 'BD': 50, 'BE': 56, 'BF': 854,
    'BG': 100, 'BH': 48, 'BI': 108, 'BJ': 204, 'BL': 652, 'BM': 60, 'BN': 96, 'BO': 68, 'BQ': 535, 'BR': 76,
    'BS': 44, 'BT': 64, 'BV': 74, 'BW': 72, 'BY': 112, 'BZ': 84, 'CA': 124, 'CC': 166, 'CD': 180, 'CF': 140,
    'CG': 178, 'CH': 756, 'CI': 384, 'CK': 184, 'CL': 152, 'CM': 120, 'CN': 156, 'CO': 170, 'CR': 188, 'CU': 192,
    'CV': 132, 'CW': 531, 'CX': 162, 'CY': 196, 'CZ': 203, 'DE': 276, 'DJ': 262, 'DK': 208, 'DM': 212, 'DO': 214,
    'DZ': 12, 'EC': 218, 'EE': 233, 'EG': 818, 'EH': 732, 'ER': 232, 'ES': 724, 'ET': 231, 'FI': 246, 'FJ': 242,
    'FK': 238, 'FM': 583, 'FO': 234, 'FR': 250, 'GA': 266, 'GB': 826, 'GD': 308, 'GE': 268, 'GF': 254, 'GG': 831,
    'GH': 288, 'GI': 292, 'GL': 304, 'GM': 270, 'GN': 324, 'GP': 312, 'GQ': 226, 'GR': 300, 'GS': 239, 'GT': 320,
    'GU': 316, 'GW': 624, 'GY': 328, 'HK': 344, 'HM': 334, 'HN': 340, 'HR': 191, 'HT': 332, 'HU': 348, 'ID': 360,
    'IE': 372, 'IL': 376, 'IM': 833, 'IN': 356, 'IO': 86, 'IQ': 368, 'IR': 364, 'IS': 352, 'IT': 380, 'JE': 832,
    'JM': 388, 'JO': 400, 'JP': 392, 'KE': 404, 'KG': 417, 'KH': 116, 'KI': 296, 'KM': 174, 'KN': 659, 'KP': 408,
    'KR': 410, 'KW': 414, 'KY': 136, 'KZ': 398, 'LA': 418, 'LB': 422, 'LC': 662, 'LI': 438, 'LK': 144, 'LR': 430,
    'LS': 426, 'LT': 440, 'LU': 442, 'LV': 428, 'LY': 434, 'MA': 504, 'MC': 492, 'MD': 498, 'ME': 499, 'MF': 663,
    'MG': 450, 'MH': 584, 'MK': 807, 'ML': 466, 'MM': 104, 'MN': 496, 'MO': 446, 'MP': 580, 'MQ': 474, 'MR': 478,
    'MS': 500, 'MT': 470, 'MU': 480, 'MV': 462, 'MW': 454, 'MX': 484, 'MY': 458, 'MZ': 508, 'NA': 516, 'NC': 540,
    'NE': 562, 'NF': 574, 'NG': 566, 'NI': 558, 'NL': 528, 'NO': 578, 'NP': 524, 'NR': 520, 'NU': 570, 'NZ': 554,
    'OM': 512, 'PA': 591, 'PE': 604, 'PF': 258, 'PG': 598, 'PH': 608, 'PK': 586, 'PL': 616, 'PM': 666, 'PN': 612,
    'PR': 630, 'PS': 275, 'PT': 620, 'PW': 585, 'PY': 600, 'QA': 634, 'RE': 638, 'RO': 642, 'RS': 688, 'RU': 643,
    'RW': 646, 'SA': 682, 'SB': 90, 'SC': 690, 'SD': 729, 'SE': 752, 'SG': 702, 'SH': 654, 'SI': 705, 'SJ': 744,
    'SK': 703, 'SL': 694, 'SM': 674, 'SN': 686, 'SO': 706, 'SR': 740, 'SS': 728, 'ST': 678, 'SV': 222, 'SX': 534,
    'SY': 760, 'SZ': 748, 'TC': 796, 'TD': 148, 'TF': 260, 'TG': 768, 'TH': 764, 'TJ': 762, 'TK': 772, 'TL': 626,
    'TM': 795, 'TN': 788, 'TO': 776, 'TR': 792, 'TT': 780, 'TV': 798, 'TW': 158, 'TZ': 834, 'UA': 804, 'UG': 800,
    'UM': 581, 'US': 840, 'UY': 858, 'UZ': 860, 'VA': 336, 'VC': 670, 'VE': 862, 'VG': 92, 'VI': 850, 'VN': 704,
    'VU': 548, 'WF': 876, 'WS': 882, 'YE': 887, 'YT': 175, 'ZA': 710, 'ZM': 894, 'ZW': 716,
}
CRITERION_TO_ISO = {2000 + n: code for code, n in ISO_NUMERIC.items()}

_IMG_RE = re.compile(r'<img[^>]+src="([^"]+)"[^>]*?(?:height="(\d+)")?[^>]*?(?:width="(\d+)")?', re.I)
_BS = chr(92)
_HEX_ESCAPE_RE = re.compile(re.escape(_BS) + r'x([0-9a-fA-F]{2})')


class RpcError(ValueError):
    pass


def region_code(region: str | None) -> int | None:
    """'US' -> 2840. None or 'anywhere' -> None (no region filter)."""
    if not region or region.lower() == 'anywhere':
        return None
    code = region.upper()
    if code not in ISO_NUMERIC:
        raise ValueError(f'Unknown region "{region}". Use a two-letter ISO 3166 code such as US, or "anywhere".')
    return 2000 + ISO_NUMERIC[code]


def rpc_url(path: str) -> str:
    return RPC_BASE + path + '?authuser=0'


def form_body(req: dict) -> str:
    return urlencode({'f.req': json.dumps(req, separators=(',', ':'))})


def suggestions_request(term: str) -> dict:
    return {'1': term, '2': 10, '3': 10}


def creatives_request(
    *,
    advertiser_id: str | None = None,
    domain: str | None = None,
    region: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    format_code: int | None = None,
    platform_code: int | None = None,
    page_token: str | None = None,
    page_size: int = PAGE_SIZE,
) -> dict:
    filters: dict[str, Any] = {}
    if format_code:
        filters['4'] = format_code
    if date_from:
        filters['6'] = int(date_from.strftime('%Y%m%d'))
    if date_to:
        filters['7'] = int(date_to.strftime('%Y%m%d'))
    if region:
        filters['8'] = [region]
    if platform_code:
        filters['14'] = [platform_code]
    if domain:
        filters['12'] = {'1': domain, '2': True}
    if advertiser_id:
        filters['13'] = {'1': [advertiser_id]}
    req: dict[str, Any] = {'2': page_size, '3': filters, '7': {'1': 1, '2': 0, '3': region or 2840}}
    if page_token:
        req['4'] = page_token
    return req


def creative_detail_request(advertiser_id: str, creative_id: str, region: int | None) -> dict:
    return {'1': advertiser_id, '2': creative_id, '5': {'1': 1, '2': 0, '3': region or 2840}}


def parse_json(body: str | bytes) -> dict:
    text = body.decode('utf-8') if isinstance(body, bytes) else body
    text = text.lstrip()
    if not text.startswith('{'):
        raise RpcError('Expected JSON from the Transparency Center, got something else (likely a rate-limit page)')
    return json.loads(text)


def parse_suggestions(body: str | bytes) -> list[dict]:
    """Advertisers Google suggests for a name, in Google's order, with the ad-count range it shows for each."""
    data = parse_json(body)
    out = []
    for item in data.get('1') or []:
        adv = item.get('1')
        if adv:
            counts = (adv.get('4') or {}).get('2') or {}
            out.append({'advertiserId': adv.get('2'), 'advertiserName': adv.get('1'), 'country': adv.get('3'),
                        'adsMin': _int(counts.get('1')), 'adsMax': _int(counts.get('2'))})
    return out


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# Legal-form words ignored when comparing an advertiser's name with the search term ("Nike, Inc." matches "nike").
_LEGAL = {'inc', 'llc', 'ltd', 'limited', 'srl', 's', 'r', 'l', 'gmbh', 'ag', 'bv', 'b', 'v', 'as', 'a', 'oy', 'sa',
          'co', 'kg', 'plc', 'corp', 'corporation', 'company', 'spa', 'p', 'sl', 'sas', 'pty', 'nv', 'ab', 'kk',
          'pvt', 'private', 'holdings', 'group'}


def _norm_name(text: str) -> str:
    words = re.findall(r'\w+', (text or '').lower())
    return ' '.join(w for w in words if w not in _LEGAL)


def match_score(term: str, name: str) -> float:
    """1.0 when the advertiser name equals the term after dropping legal forms and punctuation; otherwise the
    similarity of the two (0 to 1), so "Nikena" scores well below "Nike, Inc." for the term "nike"."""
    t, n = _norm_name(term), _norm_name(name)
    if not t or not n:
        return 0.0
    if t == n:
        return 1.0
    return round(difflib.SequenceMatcher(None, t, n).ratio(), 2)


def rank_candidates(term: str, found: list[dict], min_score: float, limit: int) -> list[dict]:
    """Every suggestion with its match score, best first (score, then Google's ad count); `selected` marks the
    ones the Actor will scrape: score at least `min_score`, at most `limit` of them."""
    scored = [{**a, 'matchScore': match_score(term, a['advertiserName'])} for a in found]
    scored.sort(key=lambda a: (-a['matchScore'], -(a['adsMax'] or 0)))
    picked = 0
    for a in scored:
        a['selected'] = a['matchScore'] >= min_score and picked < limit
        picked += a['selected']
    return scored


def _ts(node: Any) -> str | None:
    if isinstance(node, dict) and node.get('1'):
        dt = datetime.fromtimestamp(int(node['1']), tz=timezone.utc)
        return dt.strftime('%Y-%m-%dT%H:%M:%SZ')
    return None


def _nth_sunday(year: int, month: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def pacific_date(epoch: int) -> str:
    """Calendar date in US Pacific time, which is how the Transparency Center displays 'Last shown'.

    Checked against the site on 7 Oct 2026: every displayed date matched this, not the UTC date.
    US daylight time runs from the second Sunday of March to the first Sunday of November, at 2:00 local time.
    """
    utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    y = utc.year
    dst_start = datetime(y, 3, 1, tzinfo=timezone.utc).replace(day=_nth_sunday(y, 3, 2).day, hour=10)  # 2:00 PST
    dst_end = datetime(y, 11, 1, tzinfo=timezone.utc).replace(day=_nth_sunday(y, 11, 1).day, hour=9)  # 2:00 PDT
    offset = -7 if dst_start <= utc < dst_end else -8
    return (utc + timedelta(hours=offset)).date().isoformat()


def _pdate(node: Any) -> str | None:
    if isinstance(node, dict) and node.get('1'):
        return pacific_date(int(node['1']))
    return None


def _first_img(snippet: str | None) -> dict | None:
    if not snippet:
        return None
    m = re.search(r'src="([^"]+)"', snippet)
    if not m:
        return None
    h = re.search(r'height="(\d+)"', snippet)
    w = re.search(r'width="(\d+)"', snippet)
    return {'url': htmlmod.unescape(m.group(1)), 'height': int(h.group(1)) if h else None,
            'width': int(w.group(1)) if w else None}


def creative_url(advertiser_id: str, creative_id: str, region: str | None) -> str:
    url = f'https://adstransparency.google.com/advertiser/{advertiser_id}/creative/{creative_id}'
    if region and region.lower() != 'anywhere':
        url += f'?region={region.upper()}'
    return url


def parse_creatives(body: str | bytes, region: str | None) -> tuple[list[dict], str | None]:
    """Parse one SearchCreatives page into (ad rows, next page token)."""
    data = parse_json(body)
    rows = []
    for c in data.get('1') or []:
        adv, cid = c.get('1'), c.get('2')
        content = c.get('3') or {}
        image = _first_img((content.get('3') or {}).get('2'))
        preview = (content.get('1') or {}).get('4')
        rows.append({
            'schemaVersion': SCHEMA_VERSION,
            'advertiserId': adv,
            'advertiserName': c.get('12'),
            'creativeId': cid,
            'format': FORMATS.get(c.get('4'), 'unknown'),
            'firstShown': _ts(c.get('6')),
            'lastShown': _ts(c.get('7')),
            'firstShownDate': _pdate(c.get('6')),
            'lastShownDate': _pdate(c.get('7')),
            'daysShown': c.get('13'),
            'targetDomain': c.get('14'),
            'region': region.upper() if region and region.lower() != 'anywhere' else 'anywhere',
            'adUrl': creative_url(adv, cid, region),
            'imageUrl': image['url'] if image else None,
            'previewUrl': preview,
        })
    return rows, data.get('2') or None


def parse_creative_detail(body: str | bytes) -> dict:
    """Variations, per-region stats and the advertiser name from GetCreativeById."""
    data = parse_json(body).get('1') or {}
    variations = []
    for v in data.get('5') or []:
        image = _first_img((v.get('3') or {}).get('2'))
        variations.append({'imageUrl': image['url'] if image else None, 'previewUrl': (v.get('1') or {}).get('4')})
    regions = []
    for r in data.get('17') or []:
        regions.append({'region': CRITERION_TO_ISO.get(r.get('1'), r.get('1')), 'lastShownDate': _yyyymmdd(r.get('5')),
                        'firstShownDate': _yyyymmdd(r.get('4'))})
    return {'variations': variations, 'regionStats': regions, 'lastShown': _ts(data.get('4'))}


def _yyyymmdd(value: Any) -> str | None:
    if isinstance(value, int) and value > 19000000:
        s = str(value)
        return f'{s[:4]}-{s[4:6]}-{s[6:8]}'
    return None


# ---------------------------------------------------------------- creative content (preview script)


def _preview_html(script: str) -> str | None:
    """The preview script carries the rendered ad as an escaped HTML string; return it unescaped."""
    start = script.find(_BS + 'x3chtml')
    if start < 0:
        start = script.find(_BS + 'x3c!DOCTYPE')
    if start < 0:
        return None
    close = _BS + 'x3c/html' + _BS + 'x3e'
    end = script.find(close, start)
    raw = script[start:end + len(close) if end > start else None]
    raw = _HEX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), raw)
    return raw.replace(_BS + "'", "'").replace(_BS + '/', '/')


# Labels and buttons of the preview, not words of the ad. The benchmark of 11 Oct 2026 found the "Sponsored" label in
# other languages saved as the headline (Sponsorisé, Gesponsert, Patrocinado, Gesponsord, Sponsoreret) and the map
# card's "Cancel" and "Add Stop" buttons in the text; the other languages' labels are listed so they never become one.
_SKIP_TEXT = re.compile(
    r'^(Sponsored|Sponsorisé|Gesponsert|Patrocinado|Gesponsord|Sponsoreret|Sponsorizzato|Sponsrad|Sponset|'
    r'Sponsoroitu|Sponsorowane|Sponsorlu|Ad|Learn more|Directions|Start|Reserve|Call|Visit site|Shop now|Install|'
    r'Open|Cancel|Add Stop|\[Price\]|<[^>]+>|.*Rendering Service|\W*)$', re.I)
_ESCAPED_SPACE = re.compile(r'\\[nrt]')  # line breaks left escaped in the preview script: "\n" as two characters
_BIDI_MARKS = re.compile('[‎‏⁦-⁩]')  # invisible direction marks around names and links


def parse_preview(script: str) -> dict:
    """Pull visible text, image, video and landing details from a creative preview script.

    Returns {} when the script is not a preview at all (for example an error page), so the caller can retry.
    Ads served by a third-party ad server (mostly video ads via DoubleClick) carry no content Google hosts;
    those return previewType 'externallyHosted' and the URL Google itself loads.
    """
    page = _preview_html(script)
    if page is None:
        external = re.search(r"insertPreviewHtmlUrl\('[^']*', '[^']*', '([^']+)'", script)
        if external:
            url = _HEX_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), external.group(1))
            return {'previewType': 'externallyHosted', 'headline': None, 'description': None, 'texts': [],
                    'displayUrl': None, 'imageUrls': [], 'videoIds': [], 'videoUrls': [], 'externalContentUrl': url}
        if 'invokeResponseCallback' in script:
            return {'previewType': 'unrecognized', 'headline': None, 'description': None, 'texts': [],
                    'displayUrl': None, 'imageUrls': [], 'videoIds': [], 'videoUrls': []}
        return {}
    body = re.sub(r'<script.*?</script>|<style.*?</style>|<title>.*?</title>', ' ', page, flags=re.S | re.I)
    texts = []
    for chunk in re.split(r'<[^>]+>', body):
        t = ' '.join(_BIDI_MARKS.sub('', _ESCAPED_SPACE.sub(' ', htmlmod.unescape(chunk))).split())
        if t and not _SKIP_TEXT.match(t) and not re.search(r'<[A-Za-z ]+>', t) and t not in texts:
            texts.append(t)
    title = re.search(r'<title>(.*?)</title>', page, re.S | re.I)
    kind = (title.group(1).strip() if title else '') or None
    images = sorted({htmlmod.unescape(u) for u in re.findall(
        r'https://(?:tpc\.googlesyndication\.com/(?:simgad|archive)/\d+|encrypted-tbn\d\.gstatic\.com/shopping\?q=tbn:[\w-]+|'
        r'lh\d\.googleusercontent\.com/[\w\-/=]+)', htmlmod.unescape(page))})
    videos = sorted(set(re.findall(r'(?:youtube\.com/(?:embed/|watch\?v=)|ytimg\.com/vi/|"videoId":")([\w-]{11})', page)))
    url_like = r'(?:www\.)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?'
    display_urls = [t for t in texts if re.fullmatch(url_like, t)]
    for t in texts:
        m = re.search(r'(?:^|[\u2022|]\s*)(www\.[\w-]+(?:\.[\w-]+)+(?:/\S*)?)', t)
        if m and m.group(1) not in display_urls:
            display_urls.append(m.group(1))
    words = [t for t in texts if t not in display_urls]
    return {
        'previewType': kind,
        'headline': words[0] if words else None,
        'description': ' '.join(words[1:3]) if len(words) > 1 else None,
        'texts': words[:12],
        'displayUrl': display_urls[0] if display_urls else None,
        'imageUrls': images[:10],
        'videoIds': videos,
        'videoUrls': [f'https://www.youtube.com/watch?v={v}' for v in videos],
    }


REQUIRED_FIELDS = ('advertiserId', 'creativeId', 'format', 'lastShown', 'adUrl')
