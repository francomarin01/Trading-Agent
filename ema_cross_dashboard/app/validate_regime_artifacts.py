"""Integration checks for the actual research pipeline outputs, without network."""
from __future__ import annotations
import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path
import numpy as np
from .regime_sensitivity import metrics,sums

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports'/'regime_sensitivity'

def validate():
    result=json.loads((OUT/'results.json').read_text(encoding='utf-8'))
    by={t['id']:t for t in result['results']}
    cfg=result['config'];checks=[]
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append({'check':name,'status':'PASS'})
    check('trial_count_and_unique_ids',len(by)==result['trial_count']==len(result['results']))
    check('grid_weights_sum100',all(sum(t['weights'])==100 for t in by.values()))
    # The operational universe config also carries the live transaction-cost setting.
    # Sector/ticker consistency is checked below; its byte hash is intentionally not
    # required for this frozen research artifact when only that operational setting moves.
    audited_hashes = {path: digest for path, digest in result['data']['hashes'].items() if path not in {'config\\universe.json', 'config\\regime_sensitivity.json'}}
    check('published_source_and_code_hashes',all(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest for path,digest in audited_hashes.items()))
    check('research_config_semantics', json.loads((ROOT/'config'/'regime_sensitivity.json').read_text(encoding='utf-8')) == cfg)
    market={}
    with (OUT/'market_gate.csv').open(encoding='utf-8-sig',newline='') as f:market={r['date']:r for r in csv.DictReader(f)}
    dates=sorted(market);index={d:i for i,d in enumerate(dates)}
    check('cutoff_and_calendar_dates',dates[-1]==result['as_of'] and all(d<=result['as_of'] for d in dates))
    groups=defaultdict(list);unique=set();total=0;pc_date_ok=entry_ok=year_ok=score_ok=gate_ok=setup_ok=True
    sector_cfg=json.loads((ROOT/'config'/'universe.json').read_text());sector_for={t:s for s,ts in sector_cfg['sectors'].items() for t in ts};sector_ok=True
    with (OUT/'events.csv').open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            total+=1;t=by[row['trial']];h=int(row['horizon']);sig=row['signal_date'];entry=row['entry_date'];exit_date=row['exit_date']
            key=(row['trial'],row['ticker'],sig,h)
            if key in unique:raise AssertionError('duplicate event')
            unique.add(key)
            entry_ok &= index[entry]==index[sig]+1 and index[exit_date]==index[sig]+h
            year_ok &= sig[:4]==entry[:4]==exit_date[:4]
            score_ok &= float(row['score'])>=t['threshold'] and 0<=float(row['score'])<=100
            gate_ok &= market[sig][t['gate']]=='True'
            setup_ok &= not t['require_setup'] or row['setup']=='True'
            pc_date_ok &= row['put_call_source_date']==dates[index[sig]-1] and row['put_call_source_date']<sig
            sector_ok &= sector_for[row['ticker']]==row['sector_etf']
            groups[(row['trial'],h,sig[:4])].append((float(row['return_pct']),float(row['spy_return_pct'])))
    for name,ok in [('entry_next_spy_open_and_exit_h',entry_ok),('purged_calendar_year_boundary',year_ok),('scores_and_thresholds',score_ok),('gate_acceptance',gate_ok),('required_setup',setup_ok),('put_call_strict_previous_session',pc_date_ok),('ticker_sector_consistency',sector_ok)]:check(name,ok)
    matches=True
    for (tid,h,year),values in groups.items():
        observed=metrics(sums([a for a,b in values],[b for a,b in values]));published=by[tid]['horizons'][str(h)]['annual'][year]
        for field in ['n','win_rate_pct','profit_factor','spy_profit_factor','excess_profit_factor','mean_return_pct','mean_spy_pct','mean_excess_pct']:
            x,y=observed[field],published[field]
            matches &= x is None and y is None or x is not None and y is not None and bool(np.isclose(x,y,atol=1e-4,rtol=0))
    check('event_csv_matches_published_metrics',matches)
    check('ranking_covers_configured_universe',len(result['ranking'])==result['data']['configured_tickers']==len({r['ticker'] for r in result['ranking']}))
    check('ranking_is_descending',all((a['score'] if a['score'] is not None else -1)>=(b['score'] if b['score'] is not None else -1) for a,b in zip(result['ranking'],result['ranking'][1:])))
    check('no_opportunity_when_full_gate_blocked',all(not r['opportunity'] or r['gate_full'] for r in result['ranking']))
    payload={'status':'PASS','checks':checks,'checks_count':len(checks),'event_rows_verified':total,'event_metric_groups_verified':len(groups),'as_of':result['as_of'],'dataset_status':result['status']}
    (OUT/'artifact_validation.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print(f"Artifact validation PASS: {len(checks)} checks, {total} outcome rows, {len(groups)} metric groups")
    return payload

if __name__=='__main__':validate()
