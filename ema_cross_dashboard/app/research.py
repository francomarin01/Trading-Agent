"""Walk-forward comparison of predeclared operational filters.

This is research only: it never changes the dashboard score or its thresholds.
Selection uses prior completed calendar years; reported test-year results are
therefore out of sample relative to that selection.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
BACKTEST=ROOT/'web'/'data'/'backtest.json'
FOUR_PILLAR_BACKTEST=ROOT/'web'/'data'/'backtest_four_pillar.json'
OUT=ROOT/'web'/'data'/'filter_research.json'

FILTERS={
    'BASELINE_SCORE_65': lambda r: r['score']>=65,
    'TREND_RS': lambda r: r['score']>=65 and r['trend_above_ema200'] and r['relative_strength_positive'],
    'TREND_RS_SECTOR': lambda r: r['score']>=65 and r['trend_above_ema200'] and r['relative_strength_positive'] and r['sector_outperforming'],
    'CONFIRMED_NOT_EXTENDED': lambda r: r['score']>=65 and r['trend_above_ema200'] and r['relative_strength_positive'] and r['sector_outperforming'] and r['volume_confirmed'] and r['not_extended'],
    # Mirrors the dashboard's quality gate but stays research-only until its
    # walk-forward evidence is stable across market regimes.
    'CLEAN_TREND': lambda r: r['score']>=65 and r['trend_above_ema200'] and r.get('ema_bullish', False) and r['relative_strength_positive'] and r['sector_outperforming'] and r['not_extended'],
    'CLEAN_TREND_VOLUME': lambda r: r['score']>=65 and r['trend_above_ema200'] and r.get('ema_bullish', False) and r['relative_strength_positive'] and r['sector_outperforming'] and r['not_extended'] and r['volume_confirmed'],
}


def metrics(rows: list[dict]) -> dict | None:
    if not rows: return None
    returns=[r['return_pct'] for r in rows]; excess=[r['excess_return_pct'] for r in rows]
    return {'signals':len(rows),'win_rate_pct':round(100*sum(x>0 for x in returns)/len(returns),2),'average_return_pct':round(statistics.fmean(returns),2),'median_return_pct':round(statistics.median(returns),2),'excess_return_pct':round(statistics.fmean(excess),2)}


def compare(signals: list[dict], horizon: int = 20, minimum_signals: int = 30) -> dict:
    scoped=[r for r in signals if r['horizon_sessions']==horizon]
    years=sorted({r['signal_year'] for r in scoped}); by_filter={}
    for name,predicate in FILTERS.items():
        rows=[r for r in scoped if predicate(r)]; annual=[]
        for year in years:
            result=metrics([r for r in rows if r['signal_year']==year])
            if result: annual.append({'year':year,**result})
        by_filter[name]={'aggregate':metrics(rows),'annual':annual}
    walk=[]
    for year in years:
        prior=[r for r in scoped if r['signal_year']<year]
        candidates=[]
        for name,predicate in FILTERS.items():
            train=metrics([r for r in prior if predicate(r)])
            if train and train['signals']>=minimum_signals: candidates.append((train['excess_return_pct'],train['win_rate_pct'],name))
        if not candidates: continue
        chosen=max(candidates); test=metrics([r for r in scoped if r['signal_year']==year and FILTERS[chosen[2]](r)])
        if test: walk.append({'test_year':year,'selected_filter':chosen[2],'training_excess_return_pct':chosen[0],'training_win_rate_pct':chosen[1],**test})
    return {'status':'OK','horizon_sessions':horizon,'method':'Predeclared filters; selection by prior-year excess return then win rate, subject to minimum sample. No score mutation.','filters':by_filter,'walk_forward':walk}


def refresh() -> dict:
    if not BACKTEST.exists() or not FOUR_PILLAR_BACKTEST.exists():
        result={'status':'DATA UNAVAILABLE','reason':'Run Backtest.ps1 first.'}
    else:
        legacy=compare(json.loads(BACKTEST.read_text(encoding='utf-8')).get('signals',[]))
        four_pillar=compare(json.loads(FOUR_PILLAR_BACKTEST.read_text(encoding='utf-8')).get('signals',[]))
        result={'status':'OK','method':'Predeclared filters; model comparison uses separate point-in-time signal series. No score mutation.','models':{'LEGACY':legacy,'FOUR_PILLAR':four_pillar}}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8'); return result


if __name__=='__main__':
    result=refresh(); print(f"Filter research {result['status']} · models {len(result.get('models',{}))}")
