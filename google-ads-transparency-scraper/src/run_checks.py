"""End-of-run checks: invariant rules on every delivered row, plus the optional drift canary.

If any rule fails or the canary sees drift, the run is marked FAILED with the reason in its status message, so it is
visible in Console, in the API and in Apify's failed-run notifications. Rows already delivered stay in the dataset.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Any

from apify import Actor

CHECKS_KEY = 'CHECKS'


def profile(rows: list[dict], fields: list[str], mix_field: str | None, count_by: str | None = None) -> dict:
    """Null rate per field, the share of each value of mix_field (percent), and row counts per count_by value."""
    n = len(rows)
    nulls = {f: round(100 * sum(1 for r in rows if r.get(f) in (None, '', [])) / n, 2) if n else 0.0 for f in fields}
    mix = {}
    if mix_field and n:
        mix = {str(k): round(100 * v / n, 2) for k, v in Counter(r.get(mix_field) for r in rows).items()}
    counts = dict(Counter(str(r.get(count_by)) for r in rows)) if count_by else {}
    return {'rows': n, 'nullRates': nulls, 'mix': mix, 'mixField': mix_field, 'countBy': count_by, 'counts': counts}


def compare(current: dict, baseline: dict, threshold: float, max_count_drop: float = 20.0) -> list[str]:
    drift = []
    for key, base in (baseline.get('counts') or {}).items():
        now = (current.get('counts') or {}).get(key, 0)
        if base and (base - now) / base * 100 > max_count_drop:
            drift.append(f'{current.get("countBy")}={key} returned {now} rows, down from {base} '
                         f'(more than {max_count_drop:.0f}% drop)')
    for f, rate in current['nullRates'].items():
        base = baseline.get('nullRates', {}).get(f)
        if base is not None and abs(rate - base) > threshold:
            drift.append(f'null rate of {f} moved {base:.1f}% -> {rate:.1f}%')
    keys = set(current.get('mix', {})) | set(baseline.get('mix', {}))
    for k in sorted(keys):
        a, b = current.get('mix', {}).get(k, 0.0), baseline.get('mix', {}).get(k, 0.0)
        if abs(a - b) > threshold:
            drift.append(f'{current.get("mixField")}={k} share moved {b:.1f}% -> {a:.1f}%')
    if current['rows'] == 0 and baseline.get('rows', 0) > 0:
        drift.append('no rows delivered, baseline had rows')
    return drift


async def run_checks(
    check_rows: Callable[[list[dict]], list[str]],
    *,
    canary: dict | None,
    profile_fields: list[str],
    mix_field: str | None = None,
    count_by: str | None = None,
    async_checks: list[Callable[[list[dict]], Awaitable[list[str]]]] = (),
) -> dict[str, Any]:
    dataset = await Actor.open_dataset()
    rows = [item async for item in dataset.iterate_items()]
    problems = check_rows(rows)
    for extra in async_checks:
        problems += await extra(rows)
    result: dict[str, Any] = {'rowsChecked': len(rows), 'invariantViolations': len(problems), 'violations': problems[:200]}

    drift: list[str] = []
    if canary and canary.get('baselineStore'):
        threshold = float(canary.get('thresholdPoints', 5))
        store = await Actor.open_key_value_store(name=canary['baselineStore'])
        current = profile(rows, profile_fields, mix_field, count_by)
        baseline = await store.get_value('BASELINE')
        if baseline is None or canary.get('resetBaseline'):
            await store.set_value('BASELINE', current)
            result['canary'] = {'status': 'baseline saved', 'profile': current}
        else:
            drift = compare(current, baseline, threshold, float(canary.get('maxCountDropPercent', 20)))
            result['canary'] = {'status': 'drift' if drift else 'ok', 'thresholdPoints': threshold,
                                'drift': drift, 'profile': current, 'baseline': baseline}
        await store.set_value('LAST_RUN', result['canary'])

    await Actor.set_value(CHECKS_KEY, result)
    if problems or drift:
        parts = []
        if problems:
            parts.append(f'INVARIANT CHECK FAILED ({len(problems)}): ' + '; '.join(problems[:3]))
        if drift:
            parts.append('DRIFT CANARY ALERT: ' + '; '.join(drift[:5]))
        message = ' | '.join(parts)[:1000]
        Actor.log.error(message)
        await Actor.fail(exit_code=3, status_message=message)
    else:
        Actor.log.info(f'All invariant checks passed on {len(rows)} rows.'
                       + (f' Canary: {result["canary"]["status"]}.' if 'canary' in result else ''))
    return result
