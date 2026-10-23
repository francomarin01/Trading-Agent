"""Prospective Top 10 ranking history, separate from the signal backtest."""
from __future__ import annotations

from datetime import datetime, timezone
import statistics

HORIZONS = (5, 10, 20)


def _by_date(rows: list[dict]) -> dict[str, dict]:
    return {row['date']: row for row in rows}


def register_top10_events(con, snapshot: dict) -> int:
    """Record first entry into the current top ten once per dated snapshot."""
    as_of = snapshot.get('as_of')
    if not as_of:
        return 0
    top = list(snapshot.get('assets', []))[:10]
    top_by_ticker = {asset['ticker']: (rank, asset) for rank, asset in enumerate(top, 1)}
    prior = {row['ticker']: dict(row) for row in con.execute('SELECT * FROM top10_membership')}
    # Rebuilding the same closing snapshot is idempotent.
    if prior and all(row.get('last_date') == as_of for row in prior.values()):
        return 0
    inserted = 0
    for ticker, (rank, asset) in top_by_ticker.items():
        was_member = bool(prior.get(ticker, {}).get('is_top10'))
        if not was_member:
            cursor = con.execute(
                """INSERT OR IGNORE INTO top10_events(ticker,sector_etf,signal_date,signal_score,entry_rank,status,updated_at) VALUES(?,?,?,?,?,?,?)""",
                (ticker, asset['sector_etf'], as_of, asset['opportunity_score'], rank, 'PENDING_ENTRY', datetime.now(timezone.utc).isoformat()),
            )
            inserted += cursor.rowcount
        con.execute('INSERT INTO top10_membership(ticker,is_top10,last_date) VALUES(?,?,?) ON CONFLICT(ticker) DO UPDATE SET is_top10=excluded.is_top10,last_date=excluded.last_date', (ticker, 1, as_of))
    for ticker in set(prior).difference(top_by_ticker):
        con.execute('UPDATE top10_membership SET is_top10=0,last_date=? WHERE ticker=?', (as_of, ticker))
    con.commit()
    return inserted


def update_top10_outcomes(con, bars: dict[str, list[dict]], cfg: dict) -> list[dict]:
    spy = bars.get(cfg['benchmark'], [])
    if not spy:
        return []
    spy_by_date = _by_date(spy); dates = [row['date'] for row in spy]; index = {day: n for n, day in enumerate(dates)}
    last_date = dates[-1]; cost = cfg.get('backtest', {}).get('round_trip_cost_bps', 10) / 100
    result = []
    for raw in con.execute('SELECT * FROM top10_events ORDER BY signal_date DESC, entry_rank, ticker'):
        item = dict(raw); entry_date, entry_open, status = item['entry_date'], item['entry_open'], item['status']
        signal_index = index.get(item['signal_date']); asset = _by_date(bars.get(item['ticker'], [])); sector = _by_date(bars.get(item['sector_etf'], []))
        if signal_index is None:
            status = 'DATA UNAVAILABLE'
        elif signal_index + 1 >= len(dates):
            status = 'PENDING_ENTRY'
        else:
            candidate = dates[signal_index + 1]; bar = asset.get(candidate)
            if bar is None:
                status = 'DATA UNAVAILABLE'
            else:
                entry_date, entry_open, status = candidate, bar['open'], 'ACTIVE'
        values = {f'return_{h}d_pct': None for h in HORIZONS} | {f'spy_{h}d_pct': None for h in HORIZONS} | {f'excess_spy_{h}d_pct': None for h in HORIZONS}
        values['current_return_pct'] = None
        if entry_date and entry_open is not None and entry_date <= last_date:
            current = asset.get(last_date)
            values['current_return_pct'] = round(100 * (current['close'] / entry_open - 1) - cost, 2) if current else None
            entry_index = index.get(entry_date)
            for horizon in HORIZONS:
                exit_index = entry_index + horizon if entry_index is not None else None
                if exit_index is None or exit_index >= len(dates):
                    continue
                day = dates[exit_index]; close = asset.get(day); spy_exit = spy_by_date.get(day)
                if close:
                    values[f'return_{horizon}d_pct'] = round(100 * (close['close'] / entry_open - 1) - cost, 2)
                if entry_date in spy_by_date and spy_exit:
                    values[f'spy_{horizon}d_pct'] = round(100 * (spy_exit['close'] / spy_by_date[entry_date]['open'] - 1) - cost, 2)
                if values[f'return_{horizon}d_pct'] is not None and values[f'spy_{horizon}d_pct'] is not None:
                    values[f'excess_spy_{horizon}d_pct'] = round(values[f'return_{horizon}d_pct'] - values[f'spy_{horizon}d_pct'], 2)
            if values['return_20d_pct'] is not None:
                status = 'CLOSED'
        con.execute('UPDATE top10_events SET entry_date=?,entry_open=?,status=?,updated_at=? WHERE id=?', (entry_date, entry_open, status, datetime.now(timezone.utc).isoformat(), item['id']))
        result.append({**item, 'entry_date': entry_date, 'entry_open': entry_open, 'status': status, **values})
    con.commit()
    return result


def current_top10(snapshot: dict, events: list[dict]) -> list[dict]:
    latest = {}
    for event in events:
        if event['ticker'] not in latest:
            latest[event['ticker']] = event
    rows = []
    for rank, asset in enumerate(snapshot.get('assets', [])[:10], 1):
        event = latest.get(asset['ticker'], {})
        rows.append({'rank': rank, 'ticker': asset['ticker'], 'sector_etf': asset['sector_etf'], 'score': asset['opportunity_score'], 'state': asset['state'], 'signal_date': event.get('signal_date'), 'entry_date': event.get('entry_date'), 'status': event.get('status', 'DATA UNAVAILABLE'), 'current_return_pct': event.get('current_return_pct'), **{f'return_{h}d_pct': event.get(f'return_{h}d_pct') for h in HORIZONS}, **{f'excess_spy_{h}d_pct': event.get(f'excess_spy_{h}d_pct') for h in HORIZONS}})
    return rows


def summarize_current_top10(rows: list[dict]) -> list[dict]:
    output = []
    for horizon in HORIZONS:
        valid = [row for row in rows if row.get(f'return_{horizon}d_pct') is not None]
        output.append({'horizon_sessions': horizon, 'valid': len(valid), 'average_return_pct': round(statistics.fmean(row[f'return_{horizon}d_pct'] for row in valid), 2) if valid else None, 'average_excess_spy_pct': round(statistics.fmean(row[f'excess_spy_{horizon}d_pct'] for row in valid if row.get(f'excess_spy_{horizon}d_pct') is not None), 2) if any(row.get(f'excess_spy_{horizon}d_pct') is not None for row in valid) else None, 'win_rate_pct': round(100 * sum(row[f'return_{horizon}d_pct'] > 0 for row in valid) / len(valid), 2) if valid else None})
    return output
