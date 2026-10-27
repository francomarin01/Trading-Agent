from __future__ import annotations

import json
import statistics
from datetime import datetime, timezone
from pathlib import Path

from .engine import build_snapshot
from .fundamentals import refresh_fundamentals
from .journal import register_new_signals, summarize_journal, update_journal
from .market import config, download_daily, symbols
from .operational_put_call import load_operational_ratios, refresh_previous_session
from .storage import connect, load_bars, upsert_bars
from .top10 import current_top10, register_top10_events, summarize_current_top10, update_top10_outcomes
from .watchlist import render_watchlist

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'web' / 'data' / 'dashboard.json'
ASSET_ROTATION = ROOT / 'web' / 'data' / 'asset_rotation.json'


def _write_json(target: Path, payload: dict) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(target)


def publish_snapshot(snapshot: dict) -> None:
    """Publish the fast dashboard first and lazy-load the large equity map."""
    equity_history = snapshot.pop('asset_rotation_history', [])
    rotation_payload = {
        'status': snapshot.get('status'), 'as_of': snapshot.get('as_of'),
        'rows': equity_history,
    }
    _write_json(ASSET_ROTATION, rotation_payload)
    _write_json(SNAPSHOT, snapshot)


def update_track_record(con, snapshot: dict, bars: dict[str, list[dict]]) -> list[dict]:
    """Register each eligible ticker once; compare its return with SPY from the same close."""
    threshold = config()['score_thresholds']['watch']
    spy_bars = bars.get(config()['benchmark'], [])
    spy_by_date = {bar['date']: bar['close'] for bar in spy_bars}
    for asset in snapshot['assets']:
        if asset['score'] < threshold: continue
        exists = con.execute('SELECT 1 FROM inclusions WHERE ticker=?', (asset['ticker'],)).fetchone()
        if not exists:
            etf_bars = bars.get(asset['sector_etf'], [])
            etf_close = etf_bars[-1]['close'] if etf_bars and etf_bars[-1]['date'] == asset['date'] else None
            event = asset['state'] if asset['state'] != 'NEUTRAL' else 'SCORE_65'
            con.execute('INSERT INTO inclusions(ticker,sector_etf,event,inclusion_date,entry_close,entry_etf_close) VALUES(?,?,?,?,?,?)', (asset['ticker'], asset['sector_etf'], event, asset['date'], asset['close'], etf_close))
    con.commit(); rows=[]
    for inclusion in con.execute('SELECT * FROM inclusions ORDER BY inclusion_date,ticker'):
        item=dict(inclusion); ticker_bars=bars.get(item['ticker'], [])
        ticker_by_date={x['date']:x['close'] for x in ticker_bars}
        common=sorted(set(ticker_by_date).intersection(spy_by_date)); last=common[-1] if common else None
        close=ticker_by_date.get(last) if last else None; spy_close=spy_by_date.get(last) if last else None
        entry_spy_close=spy_by_date.get(item['inclusion_date'])
        ret=(close/item['entry_close']-1)*100 if close else None
        bench=(spy_close/entry_spy_close-1)*100 if spy_close and entry_spy_close else None
        rows.append({'ticker':item['ticker'],'sector_etf':item['sector_etf'],'benchmark_symbol':config()['benchmark'],'event':item['event'],'inclusion_date':item['inclusion_date'],'as_of':last,'return_pct':round(ret,2) if ret is not None else None,'benchmark_return_pct':round(bench,2) if bench is not None else None,'excess_return_pct':round(ret-bench,2) if ret is not None and bench is not None else None})
    return rows


def summarize_track_record(rows: list[dict]) -> list[dict]:
    summary=[]
    for sector in sorted({r['sector_etf'] for r in rows}):
        group=[r for r in rows if r['sector_etf']==sector and r['return_pct'] is not None and r['benchmark_return_pct'] is not None]
        if not group:
            summary.append({'sector_etf':sector,'benchmark_symbol':config()['benchmark'],'included':sum(r['sector_etf']==sector for r in rows),'valid_returns':0,'win_rate_pct':None,'average_return_pct':None,'benchmark_return_pct':None,'excess_return_pct':None}); continue
        summary.append({'sector_etf':sector,'benchmark_symbol':config()['benchmark'],'included':sum(r['sector_etf']==sector for r in rows),'valid_returns':len(group),'win_rate_pct':round(100*sum(r['return_pct']>0 for r in group)/len(group),2),'average_return_pct':round(statistics.fmean(r['return_pct'] for r in group),2),'benchmark_return_pct':round(statistics.fmean(r['benchmark_return_pct'] for r in group),2),'excess_return_pct':round(statistics.fmean(r['excess_return_pct'] for r in group),2)})
    return summary


def retain_previous_valid_snapshot(snapshot: dict) -> dict:
    """Keep the last screen when a refresh cannot build a valid replacement."""
    if snapshot.get('status') == 'OK' or not SNAPSHOT.exists():
        return snapshot
    try:
        previous = json.loads(SNAPSHOT.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return snapshot
    if previous.get('status') != 'OK':
        return snapshot
    previous['refresh'] = snapshot.get('refresh', {})
    previous['refresh']['retained_previous_snapshot'] = True
    return previous


def refresh() -> dict:
    cfg = config(); con = connect(); errors = []; succeeded = 0
    # Newer sector/theme ETFs are valid contextual benchmarks once they have
    # enough completed sessions for the 20-day model features. Equities remain
    # subject to the full 252-session production requirement.
    benchmark_symbols = {cfg['benchmark'], cfg['growth_benchmark'], cfg['volatility_symbol'], *cfg['sectors'].keys()}
    for symbol in symbols(cfg):
        try:
            minimum_bars = 21 if symbol in benchmark_symbols else cfg.get('backtest', {}).get('minimum_history_sessions', 252)
            upsert_bars(con, symbol, download_daily(symbol, cfg['lookback_days'], minimum_bars=minimum_bars))
            succeeded += 1
        except Exception as exc:
            errors.append(f'{symbol}: {exc}')
    bars = {symbol: load_bars(con, symbol) for symbol in symbols(cfg)}
    attempted_at = datetime.now(timezone.utc).isoformat()
    try:
        put_call_refresh = refresh_previous_session(bars.get(cfg['benchmark'], []))
        snapshot = build_snapshot(bars, cfg, load_operational_ratios())
        snapshot['risk_plan'] = cfg.get('risk_plan', {})
        snapshot['fundamentals'] = refresh_fundamentals(con, snapshot['assets'], cfg)
        snapshot['market']['operational_gate']['put_call_refresh'] = put_call_refresh
        snapshot['track_record'] = update_track_record(con, snapshot, bars)
        snapshot['track_record_sector_summary'] = summarize_track_record(snapshot['track_record'])
        snapshot['top10_new_entries'] = register_top10_events(con, snapshot)
        top10_events = update_top10_outcomes(con, bars, cfg)
        snapshot['top10'] = current_top10(snapshot, top10_events)
        snapshot['top10_summary'] = summarize_current_top10(snapshot['top10'])
        snapshot['journal_new_signals'] = register_new_signals(con, snapshot)
        snapshot['journal'] = update_journal(con, bars, cfg)
        snapshot['journal_summary'] = summarize_journal(snapshot['journal'])
        snapshot['watchlist'] = render_watchlist(con, snapshot, snapshot['journal'])
    except Exception as exc:
        errors.append(str(exc))
        snapshot = {'status': 'DATA UNAVAILABLE', 'as_of': None, 'source': cfg['source'], 'assets': [], 'sectors': [], 'rotation_history': [], 'asset_rotation_history': [], 'track_record': [], 'track_record_sector_summary': [], 'top10': [], 'top10_summary': [], 'journal': [], 'journal_summary': [], 'watchlist': [], 'market': {'regime': 'DATA UNAVAILABLE', 'qqq_spy_ratio': None, 'qqq_vs_spy_20d_pct': None, 'vix': None, 'vix_percentile252': None, 'vix_state': 'DATA UNAVAILABLE'}}
    snapshot['refresh'] = {'attempted_at': attempted_at, 'downloaded_symbols': succeeded, 'errors': errors}
    # A temporary provider outage must not erase a previously usable screen.
    snapshot = retain_previous_valid_snapshot(snapshot)
    publish_snapshot(snapshot)
    con.execute('INSERT INTO refresh_log(updated_at,status,message) VALUES(?,?,?)', (snapshot['refresh']['attempted_at'], snapshot['status'] if errors else 'OK', '; '.join(errors) or 'All symbols refreshed'))
    con.commit(); con.close()
    return snapshot


def rebuild_from_local() -> dict:
    """Regenerate the UI snapshot from the stored bars without a network call.

    Useful after a dashboard or calculation-field upgrade.  It never appends,
    fills, or changes OHLCV observations.
    """
    cfg = config()
    con = connect()
    attempted_at = datetime.now(timezone.utc).isoformat()
    bars = {symbol: load_bars(con, symbol) for symbol in symbols(cfg)}
    errors: list[str] = []
    try:
        snapshot = build_snapshot(bars, cfg, load_operational_ratios())
        snapshot['risk_plan'] = cfg.get('risk_plan', {})
        snapshot['fundamentals'] = refresh_fundamentals(con, snapshot['assets'], cfg)
        snapshot['track_record'] = update_track_record(con, snapshot, bars)
        snapshot['track_record_sector_summary'] = summarize_track_record(snapshot['track_record'])
        snapshot['top10_new_entries'] = register_top10_events(con, snapshot)
        top10_events = update_top10_outcomes(con, bars, cfg)
        snapshot['top10'] = current_top10(snapshot, top10_events)
        snapshot['top10_summary'] = summarize_current_top10(snapshot['top10'])
        snapshot['journal_new_signals'] = register_new_signals(con, snapshot)
        snapshot['journal'] = update_journal(con, bars, cfg)
        snapshot['journal_summary'] = summarize_journal(snapshot['journal'])
        snapshot['watchlist'] = render_watchlist(con, snapshot, snapshot['journal'])
    except Exception as exc:
        errors.append(str(exc))
        snapshot = {'status': 'DATA UNAVAILABLE', 'as_of': None, 'source': cfg['source'], 'assets': [], 'sectors': [], 'rotation_history': [], 'asset_rotation_history': [], 'track_record': [], 'track_record_sector_summary': [], 'top10': [], 'top10_summary': [], 'journal': [], 'journal_summary': [], 'watchlist': [], 'market': {'regime': 'DATA UNAVAILABLE', 'qqq_spy_ratio': None, 'qqq_vs_spy_20d_pct': None, 'vix': None, 'vix_percentile252': None, 'vix_state': 'DATA UNAVAILABLE'}}
    snapshot['refresh'] = {'attempted_at': attempted_at, 'downloaded_symbols': 0, 'errors': errors, 'rebuilt_from_local': True}
    snapshot = retain_previous_valid_snapshot(snapshot)
    publish_snapshot(snapshot)
    con.execute('INSERT INTO refresh_log(updated_at,status,message) VALUES(?,?,?)', (attempted_at, snapshot['status'] if errors else 'OK', '; '.join(errors) or 'Snapshot rebuilt from local data'))
    con.commit()
    con.close()
    return snapshot


if __name__ == '__main__':
    result = refresh()
    print(f"Snapshot {result['status']} as of {result['as_of']} · {len(result['assets'])} assets · {len(result['refresh']['errors'])} download errors")
