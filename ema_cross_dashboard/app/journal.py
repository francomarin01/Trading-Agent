"""Prospective paper-trading journal for monitor signals.

This is deliberately separate from the legacy track record.  It starts when a
new monitor signal is emitted and never back-fills historical opportunities.
"""
from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone


HORIZONS = (5, 10, 20)


def create_journal_schema(con) -> None:
    con.execute("""
    CREATE TABLE IF NOT EXISTS signal_journal (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      model TEXT NOT NULL, ticker TEXT NOT NULL, sector_etf TEXT NOT NULL,
      signal_date TEXT NOT NULL, signal_score REAL NOT NULL, state TEXT NOT NULL,
      setup_reason TEXT, gate_snapshot TEXT NOT NULL,
      entry_date TEXT, entry_open REAL,
      signal_atr REAL, stop_atr_multiple REAL, target_r_multiple REAL,
      status TEXT NOT NULL DEFAULT 'PENDING_ENTRY', updated_at TEXT NOT NULL,
      UNIQUE(model, ticker, signal_date)
    )
    """)
    columns = {row['name'] for row in con.execute('PRAGMA table_info(signal_journal)')}
    for column, definition in (('signal_atr', 'REAL'), ('stop_atr_multiple', 'REAL'), ('target_r_multiple', 'REAL')):
        if column not in columns:
            con.execute(f'ALTER TABLE signal_journal ADD COLUMN {column} {definition}')


def register_new_signals(con, snapshot: dict) -> int:
    """Insert only genuinely new eligible signals; do not duplicate open ones."""
    gate = snapshot.get('market', {}).get('operational_gate', {})
    plan = snapshot.get('risk_plan', {})
    stop_multiple = plan.get('initial_stop_atr_multiple', 2.0)
    target_multiple = plan.get('take_profit_r_multiple', 2.0)
    inserted = 0
    candidates = []
    for asset in snapshot.get('assets', []):
        if asset.get('eligible_for_new_entry'):
            candidates.append(('CURRENT_GATE', asset, asset.get('score')))
        if asset.get('selective_setup_candidate'):
            candidates.append(('SELECTIVE_SETUP', asset, asset.get('selective_score_25_30_30_15')))
    for model, asset, score in candidates:
        if score is None:
            continue
        open_row = con.execute("SELECT 1 FROM signal_journal WHERE model=? AND ticker=? AND status IN ('PENDING_ENTRY','ACTIVE')", (model, asset['ticker'])).fetchone()
        if open_row:
            continue
        cursor = con.execute(
            """INSERT OR IGNORE INTO signal_journal(model,ticker,sector_etf,signal_date,signal_score,state,setup_reason,gate_snapshot,signal_atr,stop_atr_multiple,target_r_multiple,status,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (model, asset['ticker'], asset['sector_etf'], asset['date'], score, asset['state'], asset.get('selective_setup_reason'), json.dumps(gate, ensure_ascii=False, sort_keys=True), asset.get('atr14'), stop_multiple, target_multiple, 'PENDING_ENTRY', datetime.now(timezone.utc).isoformat()),
        )
        inserted += cursor.rowcount
    con.commit()
    return inserted


def register_manual_position(con, asset: dict, gate: dict, plan: dict, entry_price: float | None = None, entry_date: str | None = None) -> bool:
    """Create a user-directed paper position, optionally with its actual fill."""
    if (entry_price is None) != (entry_date is None):
        raise ValueError('Ingresá juntos el precio y la fecha reales de ejecución')
    existing = con.execute("SELECT 1 FROM signal_journal WHERE model='MANUAL_WATCHLIST' AND ticker=? AND status IN ('PENDING_ENTRY','ACTIVE')", (asset['ticker'],)).fetchone()
    if existing:
        return False
    status = 'ACTIVE' if entry_price is not None else 'PENDING_ENTRY'
    cursor = con.execute(
        """INSERT OR IGNORE INTO signal_journal(model,ticker,sector_etf,signal_date,signal_score,state,setup_reason,gate_snapshot,entry_date,entry_open,signal_atr,stop_atr_multiple,target_r_multiple,status,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ('MANUAL_WATCHLIST', asset['ticker'], asset['sector_etf'], asset['date'], asset['score'], asset['state'], 'MANUAL WATCHLIST', json.dumps(gate, ensure_ascii=False, sort_keys=True), entry_date, entry_price, asset.get('atr14'), plan.get('initial_stop_atr_multiple', 2.0), plan.get('take_profit_r_multiple', 2.0), status, datetime.now(timezone.utc).isoformat()),
    )
    con.commit()
    return bool(cursor.rowcount)


def _bar_by_date(rows: list[dict]) -> dict[str, dict]:
    return {row['date']: row for row in rows}


def _net_return(entry: float | None, close: float | None, cost_pct: float) -> float | None:
    if entry in (None, 0) or close is None:
        return None
    return round(100 * (close / entry - 1) - cost_pct, 4)


def _risk_tracking(asset_by_date: dict[str, dict], dates: list[str], entry_date: str, entry_open: float, signal_atr: float | None, stop_multiple: float | None, target_multiple: float | None) -> dict:
    """Evaluate a frozen stop/target from OHLC without choosing optimistic paths."""
    if not signal_atr or not stop_multiple or not target_multiple:
        return {'stop_price': None, 'target_price': None, 'risk_status': 'DATA UNAVAILABLE', 'risk_exit_date': None, 'risk_exit_price': None, 'r_multiple_to_date': None}
    risk_per_share = signal_atr * stop_multiple
    stop = entry_open - risk_per_share
    target = entry_open + risk_per_share * target_multiple
    for date in dates:
        bar = asset_by_date.get(date)
        if not bar:
            continue
        stop_hit, target_hit = bar['low'] <= stop, bar['high'] >= target
        if stop_hit and target_hit:
            return {'stop_price': round(stop, 4), 'target_price': round(target, 4), 'risk_status': 'AMBIGUOUS_STOP_FIRST', 'risk_exit_date': date, 'risk_exit_price': round(stop, 4), 'r_multiple_to_date': -1.0}
        if stop_hit:
            return {'stop_price': round(stop, 4), 'target_price': round(target, 4), 'risk_status': 'STOP HIT', 'risk_exit_date': date, 'risk_exit_price': round(stop, 4), 'r_multiple_to_date': -1.0}
        if target_hit:
            return {'stop_price': round(stop, 4), 'target_price': round(target, 4), 'risk_status': 'TARGET HIT', 'risk_exit_date': date, 'risk_exit_price': round(target, 4), 'r_multiple_to_date': round(target_multiple, 2)}
    latest = asset_by_date.get(dates[-1]) if dates else None
    return {'stop_price': round(stop, 4), 'target_price': round(target, 4), 'risk_status': 'OPEN', 'risk_exit_date': None, 'risk_exit_price': None, 'r_multiple_to_date': round((latest['close'] - entry_open) / risk_per_share, 2) if latest else None}


def update_journal(con, bars: dict[str, list[dict]], cfg: dict) -> list[dict]:
    """Resolve T+1 entries and same-calendar 5/10/20-session outcomes.

    A missing asset or sector quote remains explicit; no alternate date is used.
    """
    spy = bars.get(cfg['benchmark'], [])
    if not spy:
        return []
    spy_by_date = _bar_by_date(spy)
    spy_dates = [row['date'] for row in spy]
    index_by_date = {date: index for index, date in enumerate(spy_dates)}
    last_date = spy_dates[-1]
    cost_pct = cfg.get('backtest', {}).get('round_trip_cost_bps', 10) / 100
    rows = []
    for raw in con.execute('SELECT * FROM signal_journal ORDER BY signal_date DESC, ticker, model'):
        item = dict(raw)
        signal_index = index_by_date.get(item['signal_date'])
        asset_by_date = _bar_by_date(bars.get(item['ticker'], []))
        sector_by_date = _bar_by_date(bars.get(item['sector_etf'], []))
        status = item['status']
        entry_date, entry_open = item['entry_date'], item['entry_open']
        manual_fill = item['model'] == 'MANUAL_WATCHLIST' and entry_date is not None and entry_open is not None
        if manual_fill:
            # A discretionary fill is an observed user input, never replaced by a synthetic T+1 price.
            status = 'ACTIVE'
        elif signal_index is None:
            status = 'DATA UNAVAILABLE'
        elif signal_index + 1 >= len(spy_dates):
            status = 'PENDING_ENTRY'
        else:
            planned_entry_date = spy_dates[signal_index + 1]
            asset_entry = asset_by_date.get(planned_entry_date)
            if asset_entry is None:
                status = 'DATA UNAVAILABLE'
            else:
                entry_date, entry_open, status = planned_entry_date, asset_entry['open'], 'ACTIVE'
        metrics = {'current_return_pct': None, 'spy_return_pct': None, 'sector_return_pct': None, 'excess_vs_spy_pct': None, 'excess_vs_sector_pct': None}
        risk = {'stop_price': None, 'target_price': None, 'risk_status': 'PENDING ENTRY', 'risk_exit_date': None, 'risk_exit_price': None, 'r_multiple_to_date': None}
        for horizon in HORIZONS:
            metrics[f'return_{horizon}d_pct'] = None
            metrics[f'spy_{horizon}d_pct'] = None
            metrics[f'sector_{horizon}d_pct'] = None
            metrics[f'excess_spy_{horizon}d_pct'] = None
            metrics[f'excess_sector_{horizon}d_pct'] = None
        if entry_date and entry_open is not None:
            if entry_date <= last_date:
                latest_asset, latest_spy, latest_sector = asset_by_date.get(last_date), spy_by_date.get(last_date), sector_by_date.get(last_date)
                metrics['current_return_pct'] = _net_return(entry_open, latest_asset['close'], cost_pct) if latest_asset else None
                metrics['spy_return_pct'] = _net_return(spy_by_date[entry_date]['open'], latest_spy['close'], cost_pct) if latest_spy and entry_date in spy_by_date else None
                metrics['sector_return_pct'] = _net_return(sector_by_date[entry_date]['open'], latest_sector['close'], cost_pct) if latest_sector and entry_date in sector_by_date else None
                if metrics['current_return_pct'] is not None and metrics['spy_return_pct'] is not None:
                    metrics['excess_vs_spy_pct'] = round(metrics['current_return_pct'] - metrics['spy_return_pct'], 4)
                if metrics['current_return_pct'] is not None and metrics['sector_return_pct'] is not None:
                    metrics['excess_vs_sector_pct'] = round(metrics['current_return_pct'] - metrics['sector_return_pct'], 4)
                entry_index = index_by_date.get(entry_date)
                horizon_anchor = entry_index if manual_fill else signal_index
                for horizon in HORIZONS:
                    exit_index = horizon_anchor + horizon if horizon_anchor is not None else None
                    if exit_index is None or exit_index >= len(spy_dates):
                        continue
                    exit_date = spy_dates[exit_index]
                    asset_exit, sector_exit = asset_by_date.get(exit_date), sector_by_date.get(exit_date)
                    if asset_exit:
                        metrics[f'return_{horizon}d_pct'] = _net_return(entry_open, asset_exit['close'], cost_pct)
                    if entry_date in spy_by_date:
                        metrics[f'spy_{horizon}d_pct'] = _net_return(spy_by_date[entry_date]['open'], spy_by_date[exit_date]['close'], cost_pct)
                    if entry_date in sector_by_date and sector_exit:
                        metrics[f'sector_{horizon}d_pct'] = _net_return(sector_by_date[entry_date]['open'], sector_exit['close'], cost_pct)
                    if metrics[f'return_{horizon}d_pct'] is not None and metrics[f'spy_{horizon}d_pct'] is not None:
                        metrics[f'excess_spy_{horizon}d_pct'] = round(metrics[f'return_{horizon}d_pct'] - metrics[f'spy_{horizon}d_pct'], 4)
                    if metrics[f'return_{horizon}d_pct'] is not None and metrics[f'sector_{horizon}d_pct'] is not None:
                        metrics[f'excess_sector_{horizon}d_pct'] = round(metrics[f'return_{horizon}d_pct'] - metrics[f'sector_{horizon}d_pct'], 4)
                if metrics['return_20d_pct'] is not None:
                    status = 'CLOSED'
            # For manual fills we start on the following daily bar: an intraday fill has no
            # timestamp here, so using its same-day OHLC could create a false stop/target hit.
            entry_index = index_by_date.get(entry_date)
            risk_start = (entry_index + 1) if manual_fill and entry_index is not None else (signal_index + 1 if signal_index is not None else None)
            risk_dates = spy_dates[risk_start:] if risk_start is not None else []
            risk = _risk_tracking(asset_by_date, risk_dates, entry_date, entry_open, item.get('signal_atr'), item.get('stop_atr_multiple'), item.get('target_r_multiple'))
        con.execute('UPDATE signal_journal SET entry_date=?, entry_open=?, status=?, updated_at=? WHERE id=?', (entry_date, entry_open, status, datetime.now(timezone.utc).isoformat(), item['id']))
        rows.append({
            'id': item['id'], 'model': item['model'], 'ticker': item['ticker'], 'sector_etf': item['sector_etf'],
            'signal_date': item['signal_date'], 'signal_score': round(item['signal_score'], 2), 'state': item['state'],
            'setup_reason': item['setup_reason'], 'entry_date': entry_date, 'entry_open': round(entry_open, 4) if entry_open is not None else None,
            'as_of': last_date, 'status': status, **risk, **{key: round(value, 2) if value is not None else None for key, value in metrics.items()},
        })
    con.commit()
    return rows


def summarize_journal(rows: list[dict]) -> list[dict]:
    result = []
    for model in sorted({row['model'] for row in rows}):
        group = [row for row in rows if row['model'] == model]
        matured = [row for row in group if row['return_20d_pct'] is not None]
        result.append({
            'model': model, 'signals': len(group), 'active': sum(row['status'] == 'ACTIVE' for row in group),
            'pending_entry': sum(row['status'] == 'PENDING_ENTRY' for row in group), 'matured_20d': len(matured),
            'win_rate_20d_pct': round(100 * sum(row['return_20d_pct'] > 0 for row in matured) / len(matured), 2) if matured else None,
            'average_return_20d_pct': round(statistics.fmean(row['return_20d_pct'] for row in matured), 2) if matured else None,
            'average_excess_spy_20d_pct': round(statistics.fmean(row['excess_spy_20d_pct'] for row in matured if row['excess_spy_20d_pct'] is not None), 2) if any(row['excess_spy_20d_pct'] is not None for row in matured) else None,
        })
    return result
