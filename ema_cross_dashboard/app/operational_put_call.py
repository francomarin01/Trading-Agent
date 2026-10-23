"""Small, fail-closed cache for the daily operational Cboe Put/Call gate."""
from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path

from .cboe_put_call import FIELDS, URL, _fetch

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'data' / 'operational' / 'cboe_total_put_call.csv'


def load_operational_ratios(path: Path = CACHE) -> dict[str, float]:
    if not path.exists():
        return {}
    ratios: dict[str, float] = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        for row in csv.DictReader(stream):
            date = row.get('Date')
            try:
                value = float(row.get('PutCall', ''))
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Invalid operational Put/Call row: {date}') from exc
            try:
                normalized_date = datetime.strptime(date or '', '%Y-%m-%d').date().isoformat()
                has_timezone = datetime.fromisoformat(row.get('DownloadedAtUTC', '')).tzinfo is not None
            except ValueError:
                normalized_date, has_timezone = None, False
            if normalized_date != date or date in ratios or not 0 <= value < 100 or row.get('Source') != URL + date or not has_timezone or not re.fullmatch(r'[0-9a-f]{64}', row.get('PageSHA256', '')):
                raise ValueError(f'Invalid operational Put/Call provenance: {date}')
            ratios[date] = value
    return ratios


def _write(rows: dict[str, dict]) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows[date] for date in sorted(rows))
    temporary.replace(CACHE)


def refresh_previous_session(spy_bars: list[dict]) -> dict:
    """Fetch at most one dated Cboe observation, preserving a usable cache.

    The engine always consumes the previous completed SPY session.  A failure
    never carries an older ratio forward: the gate reports DATA UNAVAILABLE.
    """
    if len(spy_bars) < 2:
        return {'status': 'DATA UNAVAILABLE', 'error': 'SPY history insufficient'}
    required_date = spy_bars[-2]['date']
    existing = load_operational_ratios()
    if required_date in existing:
        return {'status': 'CACHED', 'date': required_date, 'value': existing[required_date]}
    row = _fetch(required_date)
    if 'error' in row:
        return {'status': 'DATA UNAVAILABLE', 'date': required_date, 'error': row['error']}
    existing_rows: dict[str, dict] = {}
    if CACHE.exists():
        with CACHE.open(encoding='utf-8-sig', newline='') as stream:
            existing_rows = {item['Date']: item for item in csv.DictReader(stream)}
    existing_rows[required_date] = row
    _write(existing_rows)
    return {'status': 'DOWNLOADED', 'date': required_date, 'value': row['PutCall']}
