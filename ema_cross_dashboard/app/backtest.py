"""Point-in-time event backtest for the EMA Cross dashboard.

Signals use only closes available at T0. Entries are the asset and sector ETF
opens on T+1. Exits are the common close after the configured holding period.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

from .engine import _atr, _score, four_pillar_score
from .market import config, pct_change
from .storage import connect, load_bars

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'web' / 'data' / 'backtest.json'
FOUR_PILLAR_OUT = ROOT / 'web' / 'data' / 'backtest_four_pillar.json'
SUMMARY_OUT = ROOT / 'web' / 'data' / 'backtest_summary.json'


def state(score: float | None, thresholds: dict) -> str:
    if score is None: return 'NEUTRAL'
    if score >= thresholds['high_priority']: return 'HIGH PRIORITY'
    if score >= thresholds['armed']: return 'ARMED'
    if score >= thresholds['watch']: return 'SCORE_65'
    return 'NEUTRAL'


def _summary(rows: list[dict]) -> list[dict]:
    result=[]
    for (event, horizon), values in sorted(_group(rows).items()):
        returns=[x['return_pct'] for x in values]; benchmarks=[x['benchmark_return_pct'] for x in values]; excess=[x['excess_return_pct'] for x in values]
        result.append({'event':event,'horizon_sessions':horizon,'signals':len(values),'win_rate_pct':round(100*sum(x>0 for x in returns)/len(returns),2),'average_return_pct':round(statistics.fmean(returns),2),'median_return_pct':round(statistics.median(returns),2),'benchmark_return_pct':round(statistics.fmean(benchmarks),2),'excess_return_pct':round(statistics.fmean(excess),2)})
    return result


def _group(rows):
    grouped=defaultdict(list)
    for row in rows: grouped[(row['event'],row['horizon_sessions'])].append(row)
    return grouped


def legacy_score(bars: list[dict], spy: list[float], sector: list[float]):
    return _score([bar['close'] for bar in bars], [bar['volume'] for bar in bars], spy, sector)


def run(bars_by_symbol: dict[str, list[dict]], cfg: dict, scorer=legacy_score, model: str = 'LEGACY') -> dict:
    rules=cfg['backtest']; horizons=rules['horizons_sessions']; min_history=rules['minimum_history_sessions']; costs=rules['round_trip_cost_bps']/100
    spy=bars_by_symbol.get(cfg['benchmark'], []); signals=[]
    if len(spy)<min_history: return {'status':'DATA UNAVAILABLE','reason':'SPY history is insufficient','summary':[],'signals':[]}
    for sector, tickers in cfg['sectors'].items():
        etf=bars_by_symbol.get(sector, []); etf_by_date={x['date']:x for x in etf}
        for ticker in tickers:
            bars=bars_by_symbol.get(ticker, []); prior='NEUTRAL'
            for index in range(min_history-1, len(bars)-1):
                date=bars[index]['date']; spy_prefix=[x['close'] for x in spy if x['date']<=date]; etf_prefix=[x['close'] for x in etf if x['date']<=date]
                if len(spy_prefix)<21 or len(etf_prefix)<21: continue
                prefix=bars[:index+1]; closes=[x['close'] for x in prefix]; volumes=[x['volume'] for x in prefix]
                score,details=scorer(prefix,spy_prefix,etf_prefix); current=state(score,cfg['score_thresholds'])
                # One event per state transition.  A ticker must fall back
                # below the threshold before it can generate the same event.
                if current==prior or current=='NEUTRAL': prior=current; continue
                entry=bars[index+1]; etf_entry=etf_by_date.get(entry['date'])
                if not etf_entry: prior=current; continue
                for horizon in horizons:
                    exit_index=index+horizon
                    if exit_index>=len(bars): continue
                    exit_bar=bars[exit_index]; etf_exit=etf_by_date.get(exit_bar['date'])
                    if not etf_exit: continue
                    gross=(exit_bar['close']/entry['open']-1)*100; benchmark=(etf_exit['close']/etf_entry['open']-1)*100
                    net=gross-costs
                    atr=_atr(bars[:index+1])
                    extension=(closes[-1]-details['ema21'])/atr if atr else None
                    signals.append({'model':model,'ticker':ticker,'sector_etf':sector,'event':current,'signal_date':date,'signal_year':int(date[:4]),'entry_date':entry['date'],'exit_date':exit_bar['date'],'horizon_sessions':horizon,'score':round(score,2),'trend_above_ema200':closes[-1]>details['ema200'],'ema_bullish':details['ema9']>details['ema21'],'relative_strength_positive':(details['relative_strength'] or 0)>0,'sector_outperforming':(details['sector_return20'] or 0)>(pct_change(spy_prefix,20) or 0),'volume_confirmed':volumes[-1]>details['volume_sma50'],'not_extended':extension is not None and extension<=2,'return_pct':round(net,2),'benchmark_return_pct':round(benchmark-costs,2),'excess_return_pct':round(net-(benchmark-costs),2)})
                prior=current
    return {'status':'OK','model':model,'method':('Signal close T0 → entry open T+1 → exit close T+h; retorno bruto sin costes de transacción.' if rules['round_trip_cost_bps'] == 0 else 'Signal close T0 → entry open T+1 → exit close T+h; costes incluidos en activo y ETF.'),'round_trip_cost_bps':rules['round_trip_cost_bps'],'summary':_summary(signals),'signals':signals}


def refresh() -> dict:
    cfg=config(); con=connect(); symbols={cfg['benchmark']}
    symbols.update(cfg['sectors'].keys())
    for group in cfg['sectors'].values(): symbols.update(group)
    bars={symbol:load_bars(con,symbol) for symbol in symbols}
    result=run(bars,cfg,legacy_score,'LEGACY')
    four_pillar=run(bars,cfg,four_pillar_score,'FOUR_PILLAR')
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    FOUR_PILLAR_OUT.write_text(json.dumps(four_pillar,ensure_ascii=False),encoding='utf-8')
    # Browser only needs the compact aggregate.  Detailed events stay local
    # for audit and do not make the dashboard fetch a large file.
    compact={'status':'OK','method':result['method'],'models':[{key:value for key,value in item.items() if key != 'signals'} for item in (result,four_pillar)]}
    SUMMARY_OUT.write_text(json.dumps(compact,ensure_ascii=False,indent=2),encoding='utf-8')
    con.close()
    return result


if __name__=='__main__':
    result=refresh(); print(f"Backtest {result['status']} · {len(result.get('signals', []))} matured signals")
