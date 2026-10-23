from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from .sec_edgar import SecEdgarError, configured_user_agent, fetch_company_fundamentals, fetch_ticker_map

MAP_TTL_DAYS = 30


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_ticker_map(con) -> dict[str, int] | None:
    row = con.execute('SELECT payload, fetched_at FROM sec_ticker_map WHERE source=?', ('SEC EDGAR',)).fetchone()
    if not row:
        return None
    try:
        fetched = datetime.fromisoformat(row['fetched_at'])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - fetched > timedelta(days=MAP_TTL_DAYS):
            return None
        return {key: int(value) for key, value in json.loads(row['payload']).items()}
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _save_ticker_map(con, mapping: dict[str, int]) -> None:
    con.execute(
        'INSERT OR REPLACE INTO sec_ticker_map(source, fetched_at, payload) VALUES(?,?,?)',
        ('SEC EDGAR', _utcnow(), json.dumps(mapping, separators=(',', ':'))),
    )
    con.commit()


def _save_snapshot(con, record: dict[str, Any]) -> None:
    con.execute(
        '''INSERT OR REPLACE INTO fundamental_snapshots
        (ticker, source, fetched_at, status, filed_at, period_end, form, payload)
        VALUES(?,?,?,?,?,?,?,?)''',
        (record['ticker'], record.get('source', 'SEC EDGAR'), _utcnow(), record.get('status', 'DATA UNAVAILABLE'),
         record.get('filed_at'), record.get('period_end'), record.get('form'), json.dumps(record, separators=(',', ':'))),
    )
    con.commit()


def _cached(con, ticker: str) -> dict[str, Any] | None:
    row = con.execute('SELECT payload FROM fundamental_snapshots WHERE ticker=? AND source=?', (ticker, 'SEC EDGAR')).fetchone()
    if not row:
        return None
    try:
        return json.loads(row['payload'])
    except json.JSONDecodeError:
        return None


def refresh_fundamentals(con, assets: list[dict], cfg: dict) -> dict[str, Any]:
    """Refresh a bounded, auditable SEC subset. It never changes technical fields."""
    settings = cfg.get('fundamentals', {})
    maximum = int(settings.get('max_symbols_per_refresh', 35))
    candidates = sorted(
        (asset for asset in assets if asset.get('ticker') and asset.get('score') is not None),
        key=lambda asset: (-float(asset.get('opportunity_score') or -1), asset['ticker']),
    )[:maximum]
    user_agent = configured_user_agent()
    records: dict[str, dict[str, Any]] = {asset['ticker']: _cached(con, asset['ticker']) or {
        'ticker': asset['ticker'], 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR',
        'reason': 'Not refreshed yet',
    } for asset in assets}
    result = {
        'source': 'SEC EDGAR', 'status': 'DATA UNAVAILABLE', 'updated_at': _utcnow(),
        'selected_count': len(candidates), 'covered_count': 0, 'errors': [],
        'note': 'Fundamentals are contextual and do not change the technical score.',
    }
    if not settings.get('enabled', True):
        result['note'] = 'Fundamental refresh disabled by configuration.'
    elif not user_agent:
        result['note'] = 'DATA UNAVAILABLE · SEC_USER_AGENT is not configured.'
    else:
        try:
            mapping = _load_ticker_map(con) or fetch_ticker_map(user_agent)
            if _load_ticker_map(con) is None:
                _save_ticker_map(con, mapping)
            for asset in candidates:
                ticker = asset['ticker']
                cik = mapping.get(ticker)
                if cik is None:
                    record = {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR', 'reason': 'Ticker not mapped to an SEC CIK'}
                else:
                    try:
                        record = fetch_company_fundamentals(ticker, cik, user_agent)
                    except SecEdgarError as exc:
                        record = _cached(con, ticker) or {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR', 'reason': str(exc)}
                        result['errors'].append(f'{ticker}: {exc}')
                records[ticker] = record
                _save_snapshot(con, record)
        except SecEdgarError as exc:
            result['errors'].append(str(exc))
            result['note'] = f'DATA UNAVAILABLE · {exc}'
    result['covered_count'] = sum(record.get('status') == 'OK' for record in records.values())
    if result['covered_count']:
        result['status'] = 'OK'
    for asset in assets:
        asset['fundamentals'] = records[asset['ticker']]
    return result
