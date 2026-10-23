from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .sec_edgar import SecEdgarError, configured_user_agent, fetch_company_fundamentals, fetch_ticker_map, valuation_metrics

MAP_TTL_DAYS = 30
FUNDAMENTAL_CACHE_TTL_DAYS = 7
SEC_REQUEST_INTERVAL_SECONDS = 0.12


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


def _cache_is_fresh(con, ticker: str, ttl_days: int = FUNDAMENTAL_CACHE_TTL_DAYS) -> bool:
    row = con.execute('SELECT fetched_at, payload FROM fundamental_snapshots WHERE ticker=? AND source=?', (ticker, 'SEC EDGAR')).fetchone()
    if not row:
        return False
    try:
        record = json.loads(row['payload'])
        fetched = datetime.fromisoformat(row['fetched_at'])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
        return record.get('schema_version') == 2 and datetime.now(timezone.utc) - fetched <= timedelta(days=ttl_days)
    except (ValueError, TypeError, json.JSONDecodeError):
        return False


def refresh_fundamentals(con, assets: list[dict], cfg: dict) -> dict[str, Any]:
    """Refresh auditable fundamentals; ratios use snapshot close and never affect technical fields."""
    settings = cfg.get('fundamentals', {})
    maximum = int(settings.get('max_symbols_per_refresh', 600))
    candidates = sorted((asset for asset in assets if asset.get('ticker') and asset.get('score') is not None),
                        key=lambda asset: (-float(asset.get('opportunity_score') or -1), asset['ticker']))[:maximum]
    cache_ttl = max(1, int(settings.get('cache_ttl_days', FUNDAMENTAL_CACHE_TTL_DAYS)))
    refresh_candidates = [asset for asset in candidates if not _cache_is_fresh(con, asset['ticker'], cache_ttl)]
    user_agent = configured_user_agent()
    records: dict[str, dict[str, Any]] = {}
    for asset in assets:
        cached = _cached(con, asset['ticker'])
        if cached and cached.get('schema_version') != 2:
            cached = {'ticker': asset['ticker'], 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR',
                      'reason': 'SEC cache is from an older format; a successful refresh is required for comparable ratios'}
        records[asset['ticker']] = cached or {
            'ticker': asset['ticker'], 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR',
            'reason': 'Not refreshed yet',
        }
        if records[asset['ticker']].get('schema_version') == 2:
            records[asset['ticker']].setdefault('refresh_status', 'CACHED')
    result = {
        'source': 'SEC EDGAR', 'status': 'DATA UNAVAILABLE', 'updated_at': _utcnow(),
        'selected_count': len(candidates), 'refreshed_count': 0, 'covered_count': 0, 'stale_count': 0, 'errors': [],
        'cache_ttl_days': cache_ttl,
        'note': 'Ratios are contextual and do not change the technical score or backtests.',
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
            for asset in refresh_candidates:
                ticker = asset['ticker']
                cik = mapping.get(ticker)
                if cik is None:
                    record = {'schema_version': 2, 'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR', 'reason': 'Ticker not mapped to an SEC CIK'}
                else:
                    try:
                        time.sleep(SEC_REQUEST_INTERVAL_SECONDS)
                        record = fetch_company_fundamentals(ticker, cik, user_agent)
                        record['schema_version'] = 2
                        record['refresh_status'] = 'FRESH'
                    except SecEdgarError as exc:
                        record = _cached(con, ticker) or {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR', 'reason': str(exc)}
                        if record.get('schema_version') != 2:
                            record = {'ticker': ticker, 'status': 'DATA UNAVAILABLE', 'source': 'SEC EDGAR',
                                      'reason': f'No se pudo renovar SEC y la caché anterior no contiene TTM comparables: {exc}'}
                        else:
                            record['refresh_status'] = 'STALE'
                            record['refresh_error'] = str(exc)
                        result['errors'].append(f'{ticker}: {exc}')
                records[ticker] = record
                _save_snapshot(con, record)
                result['refreshed_count'] += 1
        except SecEdgarError as exc:
            result['errors'].append(str(exc))
            result['note'] = f'DATA UNAVAILABLE · {exc}'
    result['covered_count'] = sum(record.get('status') == 'OK' for record in records.values())
    result['stale_count'] = sum(record.get('refresh_status') == 'STALE' for record in records.values())
    if result['covered_count']:
        result['status'] = 'OK'
    for asset in assets:
        asset['fundamentals'] = records[asset['ticker']]
        asset['valuation'] = valuation_metrics(records[asset['ticker']], asset.get('close'))
    return result
