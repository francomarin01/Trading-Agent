"""Approved regime/score sensitivity, isolated from the operational dashboard."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import math
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .cboe_put_call import load_ratios, META as PC_META, OUT as PC_FILE
from .engine import _ema_series
from .score_research import baseline_score_series, indicators

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports" / "regime_sensitivity"

def settings() -> dict:
    cfg=json.loads((ROOT / "config" / "regime_sensitivity.json").read_text())
    if cfg.get('entry_policy') not in ('SCORE_CROSS_FILTERED_BY_GATE','ELIGIBILITY_CROSS'):
        raise ValueError('entry_policy must name an explicit supported policy')
    if cfg['baseline_weights']!=[25,30,30,15]:raise ValueError('Keep approved reference weights25/30/30/15; vary the grid to compare candidates')
    if cfg['weight_step']<=0 or len(cfg['weight_ranges'])!=4 or any(lo<=0 or hi<lo for lo,hi in cfg['weight_ranges']):raise ValueError('Invalid weight grid')
    if cfg['baseline_weights'] not in weights_grid(cfg):raise ValueError('Weight grid must include the reference tuple25/30/30/15')
    if not {5,10,20}.issubset(cfg['horizons']) or cfg['cooldown_sessions']<max(cfg['horizons']):raise ValueError('Include5/10/20 horizons and cooldown at least the longest horizon')
    if not {65,70,75,80}.issubset(cfg['thresholds']) or any(t<0 or t>100 for t in cfg['thresholds']):raise ValueError('Include reference thresholds65/70/75/80, bounded0–100')
    if cfg['minimum_history']<220:raise ValueError('Insufficient warm-up for SMA200 slope')
    if datetime.strptime(cfg['as_of'],'%Y-%m-%d').date().isoformat()!=cfg['as_of']:raise ValueError('Use YYYY-MM-DD cutoff')
    return cfg

def mean(values, length):
    a = np.asarray(values, dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) >= length:
        out[length-1:] = np.convolve(a, np.ones(length)/length, mode="valid")
    return out

def rolling_min(values, length):
    a = np.asarray(values, dtype=float)
    out = np.full(len(a), np.nan)
    if len(a) >= length:
        out[length-1:] = np.min(np.lib.stride_tricks.sliding_window_view(a,length),axis=1)
    return out

def ema(values,length):
    return np.asarray(_ema_series(list(values),length),dtype=float)

def weights_grid(cfg):
    ranges=[range(lo,hi+1,cfg["weight_step"]) for lo,hi in cfg["weight_ranges"]]
    return [list(w) for w in itertools.product(*ranges) if sum(w)==100]

def features(bars, spy_map, cfg):
    f=cfg["features"]; n=len(bars)
    c=np.array([b["close"] for b in bars]); hi=np.array([b["high"] for b in bars]); lo=np.array([b["low"] for b in bars])
    op=np.array([b["open"] for b in bars]); vol=np.array([b["volume"] for b in bars])
    e9,e21,e200=ema(c,9),ema(c,21),ema(c,200)
    s50,s200,v50=mean(c,50),mean(c,200),mean(vol,50)
    tr=np.full(n,np.nan)
    if n>1: tr[1:]=np.maximum(hi[1:]-lo[1:],np.maximum(abs(hi[1:]-c[:-1]),abs(lo[1:]-c[:-1])))
    atr=mean(tr,14)
    prior_close=np.r_[np.nan,c[:-1]]; prior_e200=np.r_[np.nan,e200[:-1]]
    event=((c>e200)&(prior_close<=prior_e200)) | ((lo<=e200)&(c>e200)&(prior_close>=prior_e200))
    recent=np.array([event[max(0,i-f["ema200_event_age"]+1):i+1].any() for i in range(n)])
    old_s200=np.r_[np.full(f["sma200_slope_sessions"],np.nan),s200[:-f["sma200_slope_sessions"]]] if n>=f["sma200_slope_sessions"] else np.full(n,np.nan)
    aligned=(c>e21)&(e21>s50)&(s50>s200)
    slope=s200>old_s200
    trend=.6*aligned+.2*slope+.2*recent
    spy=np.array([spy_map.get(b["date"],np.nan) for b in bars])
    ratio=c/spy
    rs_ratio=np.round(100*ratio/mean(ratio,f["rs_ratio_sma"]),10)
    rs_momentum=np.round(100*rs_ratio/mean(rs_ratio,f["rs_momentum_sma"]),10)
    rs=((rs_ratio>100).astype(float)+(rs_momentum>100).astype(float)+(rs_momentum>f["rs_acceleration"]).astype(float))/3
    atr_pct=atr/c
    compressed=atr_pct<=rolling_min(atr_pct,f["atr_minimum_sessions"])
    dry=vol<f["dry_volume_fraction"]*v50
    vcp=.5*compressed+.5*dry
    base=compressed&dry
    prior_base=np.array([base[max(0,i-f["vcp_base_lookback"]):i].any() for i in range(n)])
    extra=indicators(bars)
    resistance=np.array(extra["pivot_resistance"],dtype=float)
    previous_resistance=np.r_[np.nan,resistance[:-1]]
    breakout=(c>previous_resistance)&(prior_close<=previous_resistance)&prior_base
    gap=np.round(op/prior_close-1,10)
    episodic=(gap>f["episodic_gap"])&(vol>f["episodic_volume_multiple"]*v50)
    setup=episodic|breakout
    penalty=np.where((c-s50)>f["climax_atr"]*atr,f["climax_penalty"],0.0)
    valid=np.isfinite(old_s200)&np.isfinite(rs_ratio)&np.isfinite(rs_momentum)&np.isfinite(atr)&np.isfinite(v50)&(v50>0)&(atr>0)
    component=np.column_stack([trend,rs,vcp,setup.astype(float)])
    component[~valid]=np.nan
    quadrant=np.where((rs_ratio>100)&(rs_momentum>100),"LEADING",np.where((rs_ratio>100)&(rs_momentum<100),"WEAKENING",np.where((rs_ratio<100)&(rs_momentum<100),"LAGGING",np.where((rs_ratio<100)&(rs_momentum>100),"IMPROVING","NEUTRAL"))))
    high20=np.full(n,np.nan)
    for i in range(20,n): high20[i]=max(c[i-20:i])
    breakout20=(c>high20)&(prior_close<=np.r_[np.nan,high20[:-1]])
    ma_cross=(e9>e21)&(np.r_[np.nan,e9[:-1]]<=np.r_[np.nan,e21[:-1]])
    reclaim=(c>s50)&(prior_close<=np.r_[np.nan,s50[:-1]])
    fresh=np.array([(breakout20|ma_cross|reclaim)[max(0,i-f["fresh_age_sessions"]+1):i+1].any() for i in range(n)])
    return {"components":component,"penalty":penalty,"setup":setup,"vcp_base":base,"compressed":compressed,"dry":dry,"episodic":episodic,"breakout":breakout,"valid":valid,"ema200_event":recent,"aligned":aligned,"slope":slope,"rs_ratio":rs_ratio,"rs_momentum":rs_momentum,"quadrant":quadrant,"rsi14":np.asarray(extra["rsi14"],dtype=float),"sma50":s50,"sma200":s200,"ema21":e21,"ema200":e200,"atr14":atr,"volume_ratio":vol/v50,"fresh":fresh,"gap":gap,"dates":[b["date"] for b in bars]}

def breadth_bounds(above, valid, eligible, unknown_symbols=0, threshold=50):
    denominator=eligible+unknown_symbols
    unknown=denominator-valid
    if denominator<=0 or valid<=0:
        return {"pct":None,"lower_pct":None,"upper_pct":None,"pass":False,"status":"DATA UNAVAILABLE"}
    lower=100*above/denominator; upper=100*(above+unknown)/denominator
    return {"pct":100*above/valid,"lower_pct":lower,"upper_pct":upper,"pass":lower>threshold,"status":"COMPLETE" if unknown==0 else "AMBIGUOUS" if lower<=threshold<upper else "INCOMPLETE_BOUNDED"}

def market_context(bars, universe, cfg, pc):
    spy=bars.get("SPY",[]); dates=[b["date"] for b in spy]; n=len(dates)
    date_index={d:i for i,d in enumerate(dates)}
    above=np.zeros(n,dtype=int); valid=np.zeros(n,dtype=int); eligible=np.zeros(n,dtype=int)
    missing=[t for t in universe if not bars.get(t)]
    for ticker in universe:
        rows=bars.get(ticker,[])
        if len(rows)<50: continue
        close=np.array([b["close"] for b in rows]); s50=mean(close,50)
        eligible += np.array(dates)>=rows[49]["date"]
        for i,b in enumerate(rows):
            j=date_index.get(b["date"])
            if j is not None and np.isfinite(s50[i]):
                valid[j]+=1; above[j]+=int(b["close"]>s50[i])
    trend_maps={}
    for symbol in ["SPY","QQQ"]:
        rows=bars.get(symbol,[]); c=[b["close"] for b in rows]; m50,m200=mean(c,50),mean(c,200)
        trend_maps[symbol]={b["date"]:(bool(b["close"]>m50[i]>m200[i]) if np.isfinite(m200[i]) else None) for i,b in enumerate(rows)}
    vix={b["date"]:b["close"] for b in bars.get("^VIX",[])}
    out=[]
    for i,date in enumerate(dates):
        bounds=breadth_bounds(int(above[i]),int(valid[i]),int(eligible[i]),len(missing),cfg["gate"]["breadth_threshold_pct"])
        source_date=dates[i-1] if i>0 else None
        put_call=pc.get(source_date)
        spy_trend,qqq_trend=trend_maps["SPY"].get(date),trend_maps["QQQ"].get(date)
        vx=vix.get(date)
        trend=spy_trend is True and qqq_trend is True
        breadth=trend and bounds["pass"]
        partial=breadth and vx is not None and vx<cfg["gate"]["vix_max"]
        full=partial and put_call is not None and put_call<cfg["gate"]["put_call_max"]
        out.append({"date":date,"spy_trend":spy_trend,"qqq_trend":qqq_trend,
            "breadth_pct":bounds["pct"],"breadth_lower_pct":bounds["lower_pct"],"breadth_upper_pct":bounds["upper_pct"],"breadth_status":bounds["status"],"breadth_valid":int(valid[i]),"breadth_eligible":int(eligible[i])+len(missing),"missing_symbols":len(missing),"vix":vx,"put_call":put_call,"put_call_source_date":source_date,
            "NO_GATE":True,"TREND_ONLY":trend,"TREND_BREADTH":breadth,"PARTIAL_NO_PC":partial,"FULL":full,
            "full_status":"DATA UNAVAILABLE" if put_call is None or vx is None or spy_trend is None or qqq_trend is None or bounds["status"] in ("DATA UNAVAILABLE","AMBIGUOUS") else "FAVORABLE" if full else "BLOCKED"})
    return out

def trials(cfg):
    out=[]
    for w,threshold,gate,require_setup in itertools.product(weights_grid(cfg),cfg["thresholds"],["FULL","PARTIAL_NO_PC"],cfg["require_setup_variants"]):
        out.append({"id":f"T{w[0]}_R{w[1]}_V{w[2]}_S{w[3]}_TH{threshold}_{gate}_SETUP{int(require_setup)}","model":"PROPOSED","weights":w,"threshold":threshold,"gate":gate,"require_setup":require_setup,"grid":True})
    for model,gate in itertools.product(["CURRENT","PROPOSED"],["NO_GATE","TREND_ONLY","TREND_BREADTH","PARTIAL_NO_PC","FULL"]):
        if model=="PROPOSED" and gate in ("FULL","PARTIAL_NO_PC"): continue
        out.append({"id":f"{model}_BASE_{gate}","model":model,"weights":[30,25,20,25] if model=="CURRENT" else cfg["baseline_weights"],"threshold":65,"gate":gate,"require_setup":False,"grid":False})
    for t in out:t['entry_policy']=cfg.get('entry_policy','SCORE_CROSS_FILTERED_BY_GATE')
    return out

def signal_indices(mask, calendar_indices, minimum_index=251, cooldown=20):
    crosses=np.flatnonzero(mask & ~np.r_[False,mask[:-1]])
    selected=[]; last=-10_000
    for i in crosses:
        if i>=minimum_index and calendar_indices[i]-last>=cooldown:
            selected.append(i); last=calendar_indices[i]
    return np.array(selected,dtype=int)

def score_array(asset, trial):
    if trial["model"]=="CURRENT":return asset["current"]
    key=tuple(trial["weights"])
    cache=asset.get("_score_cache")
    if cache is not None and key in cache:return cache[key]
    value=np.round(np.clip(asset["f"]["components"] @ np.asarray(trial["weights"],dtype=float)-asset["f"]["penalty"],0,100),8)
    if cache is not None:cache[key]=value
    return value

def eligibility(asset, trial, gate_masks):
    scores=score_array(asset,trial)
    score_mask=np.isfinite(scores)&(scores>=trial["threshold"])
    if trial.get('entry_policy','SCORE_CROSS_FILTERED_BY_GATE')=='SCORE_CROSS_FILTERED_BY_GATE':
        mask=score_mask&~np.r_[False,score_mask[:-1]]
    else:
        mask=score_mask
    mask &= gate_masks[trial["gate"]][asset["calendar"]]
    if trial["require_setup"]:mask &= asset["f"]["setup"]
    return mask

def metrics(s):
    n=int(s[0])
    if not n:return {"n":0,"win_rate_pct":None,"profit_factor":None,"spy_profit_factor":None,"excess_profit_factor":None,"mean_return_pct":None,"mean_spy_pct":None,"mean_excess_pct":None,"excess_win_rate_pct":None,"pf_missing_reason":"NO_EVENTS"}
    pf=lambda positive,negative: round(float(positive/negative),4) if negative>0 else None
    return {"n":n,"win_rate_pct":round(100*s[1]/n,3),"profit_factor":pf(s[6],s[7]),"spy_profit_factor":pf(s[8],s[9]),"excess_profit_factor":pf(s[12],s[13]),"mean_return_pct":round(s[2]/n,4),"mean_spy_pct":round(s[4]/n,4),"mean_excess_pct":round(s[10]/n,4),"excess_win_rate_pct":round(100*s[11]/n,3),"pf_missing_reason":"NO_LOSSES" if not s[7] else None}

def sums(a,b):
    a=np.asarray(a); b=np.asarray(b); x=a-b
    return np.array([len(a),np.sum(a>0),a.sum(),np.square(a).sum(),b.sum(),np.sum(b>0),a[a>0].sum(),-a[a<0].sum(),b[b>0].sum(),-b[b<0].sum(),x.sum(),np.sum(x>0),x[x>0].sum(),-x[x<0].sum()],dtype=float)

def load_data(cfg):
    universe_cfg=json.loads((ROOT/"config"/"universe.json").read_text())
    universe=sorted({t for rows in universe_cfg["sectors"].values() for t in rows})
    symbols=set(universe)|set(universe_cfg["sectors"])|{"SPY","QQQ","^VIX"}
    con=sqlite3.connect((ROOT/"data"/"market.sqlite3").resolve().as_uri()+"?mode=ro",uri=True)
    con.row_factory=sqlite3.Row
    bars={s:[dict(r) for r in con.execute("SELECT date,open,high,low,close,volume FROM bars WHERE symbol=? AND date<=? ORDER BY date",(s,cfg["as_of"]))] for s in sorted(symbols)}
    con.close()
    for symbol in symbols:
        short_file=ROOT/"data"/"research"/"short_history"/f"{symbol}.json"
        if not bars.get(symbol) and short_file.exists():
            data=json.loads(short_file.read_text())
            if data.get("symbol")!=symbol or data.get("imputed") is not False or not data.get("source","").startswith("https://query1.finance.yahoo.com/v8/finance/chart/"):
                raise ValueError(f"Invalid short-history provenance: {symbol}")
            rows=data["rows"]
            if len({r['date'] for r in rows})!=len(rows) or rows!=sorted(rows,key=lambda r:r['date']):raise ValueError(f"Unsorted/duplicated short history {symbol}")
            if any(min(r['open'],r['high'],r['low'],r['close'])<=0 or r['volume']<0 or r['high']<max(r['open'],r['close'],r['low']) or r['low']>min(r['open'],r['close'],r['high']) for r in rows):raise ValueError(f"Invalid short-history OHLCV {symbol}")
            bars[symbol]=[r for r in rows if r['date']<=cfg['as_of']]
    return bars,universe_cfg,universe

def prepare_assets(bars,universe_cfg,cfg,calendar):
    spy=bars["SPY"]; spy_map={b["date"]:b["close"] for b in spy}; spy_date_index={b["date"]:i for i,b in enumerate(spy)}
    sector_map={t:s for s,ts in universe_cfg["sectors"].items() for t in ts}
    assets={}
    for ticker,sector in sector_map.items():
        rows=bars.get(ticker,[])
        if len(rows)<cfg["minimum_history"]:continue
        f=features(rows,spy_map,cfg)
        indexes=np.array([spy_date_index.get(b["date"],-1) for b in rows])
        if np.any(indexes<0):
            rows=[b for b in rows if b["date"] in spy_date_index];f=features(rows,spy_map,cfg);indexes=np.array([spy_date_index[b["date"]] for b in rows])
        current=np.array([score if score is not None else np.nan for score,_ in baseline_score_series(rows,spy,bars.get(sector,[]))])
        row_lookup={b["date"]:b for b in rows}
        gross=np.full((len(rows),len(cfg["horizons"])),np.nan); benchmark=np.full_like(gross,np.nan)
        mae=np.full_like(gross,np.nan);mfe=np.full_like(gross,np.nan)
        exit_year=np.zeros_like(gross,dtype=int); entry_year=np.zeros(len(rows),dtype=int)
        pending=np.zeros_like(gross,dtype=bool)
        for i,j in enumerate(indexes):
            if j+1>=len(spy):pending[i,:]=True;continue
            entry=spy[j+1]; entry_year[i]=int(entry["date"][:4]); asset_entry=row_lookup.get(entry["date"])
            for hidx,h in enumerate(cfg["horizons"]):
                if j+h>=len(spy):pending[i,hidx]=True;continue
                exit_bar=spy[j+h];exit_year[i,hidx]=int(exit_bar["date"][:4]);asset_exit=row_lookup.get(exit_bar["date"])
                if asset_entry and asset_exit:
                    gross[i,hidx]=100*(asset_exit["close"]/asset_entry["open"]-1)
                    benchmark[i,hidx]=100*(exit_bar["close"]/entry["open"]-1)
                    path=[row_lookup.get(spy[d]["date"]) for d in range(j+1,j+h+1)]
                    if all(p is not None for p in path):
                        mae[i,hidx]=100*(min(p['low'] for p in path)/asset_entry['open']-1)
                        mfe[i,hidx]=100*(max(p['high'] for p in path)/asset_entry['open']-1)
        assets[ticker]={"ticker":ticker,"sector":sector,"bars":rows,"f":f,"calendar":indexes,"current":current,"gross":gross,"benchmark":benchmark,"mae":mae,"mfe":mfe,"exit_year":exit_year,"entry_year":entry_year,"pending":pending,"year":np.array([int(b["date"][:4]) for b in rows])}
    return assets

def select_candidate(trial_list, accumulator, dates_hit, years, calendar, test_year, objective, cfg):
    before=np.array(years)<test_year
    available_dates=np.array([int(d["date"][:4])<test_year for d in calendar])
    hidx=cfg["horizons"].index(20)
    candidates=[]
    for k,t in enumerate(trial_list):
        if not t["grid"] or t["gate"]!="FULL" or t["threshold"]!=65 or t["require_setup"]:continue
        m=metrics(accumulator[k,hidx,before].sum(axis=0)); count=int(dates_hit[k,available_dates].sum())
        if m["n"]<cfg["training_minimum_events"] or count<cfg["training_minimum_entry_dates"] or m["profit_factor"] is None or m["profit_factor"]<=1 or m["mean_excess_pct"]<=0 or m["excess_profit_factor"] is None:continue
        distance=sum(abs(a-b) for a,b in zip(t["weights"],cfg["baseline_weights"]))
        key=(m["excess_profit_factor"],m["win_rate_pct"],-distance) if objective=="PF_EXCESS" else (m["win_rate_pct"],m["excess_profit_factor"],-distance)
        candidates.append((key,k,m,count))
    if not candidates:return None
    _,k,m,count=max(candidates,key=lambda x:x[0])
    return {"trial_index":k,"trial":trial_list[k],"training":m,"training_entry_dates":count}

def evaluate(assets,trial_list,calendar,cfg):
    years=sorted({int(r["date"][:4]) for r in calendar[cfg["minimum_history"]-1:]})
    acc=np.zeros((len(trial_list),len(cfg["horizons"]),len(years),14))
    dates_hit=np.zeros((len(trial_list),len(calendar)),dtype=bool)
    pending=np.zeros((len(trial_list),len(cfg["horizons"])),dtype=int)
    missing=np.zeros_like(pending);purged=np.zeros_like(pending)
    gate_masks={g:np.array([r[g] for r in calendar],dtype=bool) for g in ["NO_GATE","TREND_ONLY","TREND_BREADTH","PARTIAL_NO_PC","FULL"]}
    hashes=[hashlib.sha256() for _ in trial_list]
    for count,asset in enumerate(assets.values(),1):
        asset["_score_cache"]={}
        patterns=defaultdict(list)
        for k,t in enumerate(trial_list):
            mask=eligibility(asset,t,gate_masks)
            patterns[np.packbits(mask).tobytes()].append(k)
        for packed,ids in patterns.items():
            for k in ids:hashes[k].update(asset["ticker"].encode()+b"\0"+packed)
            mask=eligibility(asset,trial_list[ids[0]],gate_masks)
            ix=signal_indices(mask,asset["calendar"],cfg["minimum_history"]-1,cfg["cooldown_sessions"])
            if not len(ix):continue
            for hidx,h in enumerate(cfg["horizons"]):
                is_pending=asset["pending"][ix,hidx]
                observed=np.isfinite(asset["gross"][ix,hidx])&np.isfinite(asset["benchmark"][ix,hidx])
                same_year=(asset["year"][ix]==asset["exit_year"][ix,hidx])&(asset["year"][ix]==asset["entry_year"][ix])
                pending[ids,hidx]+=int(is_pending.sum());missing[ids,hidx]+=int((~observed&~is_pending).sum());purged[ids,hidx]+=int((observed&~same_year).sum())
                for yidx,year in enumerate(years):
                    selected=ix[observed&same_year&(asset["year"][ix]==year)]
                    if not len(selected):continue
                    cost=cfg["round_trip_cost_bps"]/100
                    a=asset["gross"][selected,hidx]-cost;b=asset["benchmark"][selected,hidx]-cost
                    acc[ids,hidx,yidx,:]+=sums(a,b)
                    if h==20:
                        dates_hit[np.ix_(ids,asset["calendar"][selected]+1)]=True
        asset.pop("_score_cache",None)
        if count%50==0:print(f"Sensitivity {count}/{len(assets)} assets",flush=True)
    return acc,dates_hit,years,pending,missing,purged,gate_masks,[h.hexdigest() for h in hashes]

def collect_events(assets, selected_trials, calendar,cfg,gate_masks):
    rows=[]
    for t in selected_trials:
        for asset in assets.values():
            ix=signal_indices(eligibility(asset,t,gate_masks),asset["calendar"],cfg["minimum_history"]-1,cfg["cooldown_sessions"])
            scores=score_array(asset,t)
            for i in ix:
                j=asset["calendar"][i]
                for hi,h in enumerate(cfg["horizons"]):
                    if not np.isfinite(asset["gross"][i,hi]) or asset["year"][i]!=asset["exit_year"][i,hi] or asset["year"][i]!=asset["entry_year"][i]:continue
                    cost=cfg["round_trip_cost_bps"]/100;a=asset["gross"][i,hi]-cost;b=asset["benchmark"][i,hi]-cost
                    rows.append({"trial":t["id"],"ticker":asset["ticker"],"sector_etf":asset["sector"],"signal_date":asset["bars"][i]["date"],"entry_date":calendar[j+1]["date"],"exit_date":calendar[j+h]["date"],"horizon":h,"score":round(float(scores[i]),4),"return_pct":float(a),"spy_return_pct":float(b),"excess_pct":float(a-b),"mae_pct":_num(asset['mae'][i,hi]),"mfe_pct":_num(asset['mfe'][i,hi]),"put_call_source_date":calendar[j]["put_call_source_date"],"put_call":calendar[j]["put_call"],"setup":bool(asset["f"]["setup"][i])})
    return rows

def clustered_bootstrap(rows,iterations=2000,seed=20261013):
    if not rows:return {"status":"DATA UNAVAILABLE"}
    groups=defaultdict(list)
    for r in rows:groups[r["entry_date"][:7]].append(r)
    stats=np.array([sums([r["return_pct"] for r in g],[r["spy_return_pct"] for r in g]) for g in groups.values()])
    if len(stats)<3:return {"status":"INSUFFICIENT_MONTH_CLUSTERS","months":len(stats)}
    rng=np.random.default_rng(seed);samples=[]
    for _ in range(iterations):
        m=metrics(stats[rng.integers(0,len(stats),len(stats))].sum(axis=0))
        samples.append([m["win_rate_pct"],m["mean_excess_pct"],m["excess_profit_factor"] if m["excess_profit_factor"] is not None else np.nan])
    samples=np.array(samples)
    pf_values=samples[:,2][np.isfinite(samples[:,2])]
    return {"status":"OK","months":len(stats),"iterations":iterations,"seed":seed,"method":"Resample whole calendar entry-month clusters; signals within a cluster remain together","win_rate_ci95_pct":np.percentile(samples[:,0],[2.5,97.5]).tolist(),"mean_excess_ci95_pct":np.percentile(samples[:,1],[2.5,97.5]).tolist(),"excess_pf_ci95":np.percentile(pf_values,[2.5,97.5]).tolist() if len(pf_values) else None,"probability_mean_excess_negative":float(np.mean(samples[:,1]<0))}

def _csv(path,rows):
    if not rows:
        path.write_text('',encoding='utf-8')
        return
    with path.open("w",encoding="utf-8-sig",newline="") as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)

def _num(value):return float(value) if value is not None and np.isfinite(value) else None

def refresh():
    cfg=settings();OUT.mkdir(parents=True,exist_ok=True)
    bars,universe_cfg,universe=load_data(cfg);pc=load_ratios();calendar=market_context(bars,universe,cfg,pc)
    if len(calendar)<cfg['minimum_history']:raise ValueError('SPY history after cutoff is insufficient')
    assets=prepare_assets(bars,universe_cfg,cfg,calendar)
    print(f"Prepared {len(assets)} assets · {len(weights_grid(cfg))} weight tuples",flush=True)
    trial_list=trials(cfg);acc,datehits,years,pending,missing,purged,gates,decision_hashes=evaluate(assets,trial_list,calendar,cfg)
    results=[];flat=[]
    for k,t in enumerate(trial_list):
        record={**t,"decision_sha256":decision_hashes[k],"horizons":{}}
        for hi,h in enumerate(cfg["horizons"]):
            record["horizons"][str(h)]={"all":metrics(acc[k,hi].sum(axis=0)),"annual":{str(y):metrics(acc[k,hi,yi]) for yi,y in enumerate(years)},"pending":int(pending[k,hi]),"missing_prices":int(missing[k,hi]),"purged_year_boundary":int(purged[k,hi])}
            for yi,y in enumerate(years):flat.append({"trial":t["id"],"model":t["model"],"trend_weight":t["weights"][0],"rs_weight":t["weights"][1],"vcp_weight":t["weights"][2],"setup_weight":t["weights"][3],"threshold":t["threshold"],"gate":t["gate"],"require_setup":t["require_setup"],"horizon":h,"year":y,**metrics(acc[k,hi,yi])})
        results.append(record)
    walk=[];selected_ids={t["id"] for t in trial_list if not t["grid"]}
    base_id="T25_R30_V30_S15_TH65_FULL_SETUP0";partial_id="T25_R30_V30_S15_TH65_PARTIAL_NO_PC_SETUP0"
    selected_ids.update([base_id,partial_id,"T25_R30_V30_S15_TH65_FULL_SETUP1"])
    for year in years[1:]:
        for objective in ["PF_EXCESS","WIN_RATE"]:
            pick=select_candidate(trial_list,acc,datehits,years,calendar,year,objective,cfg)
            if pick is None:walk.append({"test_year":year,"objective":objective,"status":"NO_TRAINING_CANDIDATE"});continue
            k=pick["trial_index"];selected_ids.add(pick["trial"]["id"])
            neighbors=[]
            for nk,nt in enumerate(trial_list):
                if nt['grid'] and nt['gate']=='FULL' and nt['threshold']==65 and not nt['require_setup'] and sum(abs(a-b) for a,b in zip(nt['weights'],pick['trial']['weights']))<=10:
                    nm=metrics(acc[nk,cfg['horizons'].index(20),np.array(years)<year].sum(axis=0))
                    if nm['n']>=cfg['training_minimum_events']:neighbors.append(nm)
            walk.append({"test_year":year,"objective":objective,"status":"OK","selected_trial":pick["trial"]["id"],"weights":pick["trial"]["weights"],"training":pick["training"],"training_entry_dates":pick["training_entry_dates"],"neighbor_training":{"count":len(neighbors),"positive_excess_fraction":sum(m['mean_excess_pct']>0 for m in neighbors)/len(neighbors) if neighbors else None,"mean_win_rate_pct":float(np.mean([m['win_rate_pct'] for m in neighbors])) if neighbors else None},"test":{str(h):metrics(acc[k,hi,years.index(year)]) for hi,h in enumerate(cfg["horizons"])},"test_label":"RETROSPECTIVE_PREVIOUSLY_VIEWED" if year==2026 else "TEMPORAL_TEST"})
    pareto=[]
    for k,t in enumerate(trial_list):
        if t['grid'] and t['gate']=='FULL' and t['threshold']==65 and not t['require_setup']:
            m=metrics(acc[k,cfg['horizons'].index(20),np.array(years)<=2024].sum(axis=0))
            if m['n']>=cfg['training_minimum_events'] and m['excess_profit_factor'] is not None:pareto.append({'trial':t['id'],'weights':t['weights'],**m})
    frontier=[p for p in pareto if not any(q['win_rate_pct']>=p['win_rate_pct'] and q['excess_profit_factor']>=p['excess_profit_factor'] and (q['win_rate_pct']>p['win_rate_pct'] or q['excess_profit_factor']>p['excess_profit_factor']) for q in pareto)]
    events=collect_events(assets,[t for t in trial_list if t["id"] in selected_ids],calendar,cfg,gates)
    boot={};cost_sensitivity=[]
    last_year=max(years)
    for trial_id in sorted(selected_ids):
        rows=[r for r in events if r["trial"]==trial_id and r["horizon"]==20 and int(r["signal_date"][:4])==last_year]
        boot[trial_id]=clustered_bootstrap(rows)
        for cost in [10,25,50]:
            delta=(cost-cfg["round_trip_cost_bps"])/100
            cost_sensitivity.append({"trial":trial_id,"year":last_year,"round_trip_cost_bps":cost,**metrics(sums([r["return_pct"]-delta for r in rows],[r["spy_return_pct"]-delta for r in rows]))})
    ranking=[];latest=calendar[-1]
    base_trial=next(t for t in trial_list if t["id"]==base_id)
    for ticker,asset in assets.items():
        i=len(asset["bars"])-1;f=asset["f"];score=score_array(asset,base_trial)[i];rsi=_num(f["rsi14"][i]);date=asset["bars"][i]["date"]
        ranking.append({"ticker":ticker,"sector_etf":asset["sector"],"date":date,"score":_num(score),"data_reason":"OK" if np.isfinite(score) else "MISSING_FEATURES","gate_full":latest["FULL"] if date==latest["date"] else False,"gate_status":latest["full_status"] if date==latest["date"] else "STALE","opportunity":bool(np.isfinite(score) and score>=65 and latest["FULL"] and date==latest["date"]),"rs_ratio":_num(f["rs_ratio"][i]),"rs_momentum":_num(f["rs_momentum"][i]),"quadrant":str(f["quadrant"][i]),"trend_points":_num(f["components"][i,0]*25),"rs_points":_num(f["components"][i,1]*30),"vcp_points":_num(f["components"][i,2]*30),"setup_points":_num(f["components"][i,3]*15),"climax_penalty":float(f["penalty"][i]),"rsi14":rsi,"rsi_notice":"OVERBOUGHT" if rsi is not None and rsi>70 else "OVERSOLD" if rsi is not None and rsi<30 else "NORMAL" if rsi is not None else "DATA UNAVAILABLE","episodic_pivot":bool(f["episodic"][i]),"vcp_breakout":bool(f["breakout"][i]),"ema200_event":bool(f["ema200_event"][i])})
    sector_for_ticker={t:s for s,ts in universe_cfg['sectors'].items() for t in ts}
    for ticker in universe:
        if ticker in assets:continue
        rows=bars.get(ticker,[])
        entry={k:None for k in ranking[0]}
        entry.update({'ticker':ticker,'sector_etf':sector_for_ticker[ticker],'date':rows[-1]['date'] if rows else None,'score':None,'data_reason':f'INSUFFICIENT_HISTORY_{len(rows)}_OF_{cfg["minimum_history"]}' if rows else 'SOURCE_UNAVAILABLE','gate_full':False,'gate_status':'DATA UNAVAILABLE','opportunity':False,'quadrant':'DATA UNAVAILABLE','rsi_notice':'DATA UNAVAILABLE','episodic_pivot':False,'vcp_breakout':False,'ema200_event':False})
        ranking.append(entry)
    ranking.sort(key=lambda r:(-(r["score"] if r["score"] is not None else -1),r["ticker"]))
    board=[];spy_map={b["date"]:b["close"] for b in bars["SPY"]}
    for sector in universe_cfg["sectors"]:
        rows=bars.get(sector,[])
        if not rows:continue
        f=features(rows,spy_map,cfg);i=len(rows)-1
        board.append({"etf":sector,"date":rows[i]["date"],"fresh_liftoff":bool(f["fresh"][i]),"volume_ratio":_num(f["volume_ratio"][i]),"unusual_volume":bool(f["volume_ratio"][i]>2),"fresh_and_unusual":bool(f["fresh"][i] and f["volume_ratio"][i]>2),"rs_ratio":_num(f["rs_ratio"][i]),"rs_momentum":_num(f["rs_momentum"][i]),"quadrant":str(f["quadrant"][i]),"rsi14":_num(f["rsi14"][i])})
    hash_files=[ROOT/"config"/"universe.json",ROOT/"config"/"regime_sensitivity.json",Path(__file__),ROOT/"app"/"engine.py",ROOT/"app"/"score_research.py",ROOT/"app"/"cboe_put_call.py",PC_FILE,*sorted((ROOT/"data"/"research"/"short_history").glob('*.json'))]
    data_hash=hashlib.sha256(json.dumps(bars,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    output={"status":"OK","research_only":True,"as_of":cfg["as_of"],"generated_at_utc":datetime.now(timezone.utc).isoformat(),"config":cfg,"data":{"source":"Local Yahoo OHLCV SQLite + dated official Cboe TOTAL Put/Call","configured_tickers":len(universe),"assets_with_minimum_history":len(assets),"missing_symbols":[t for t in universe if not bars.get(t)],"sessions":len(calendar),"first_date":calendar[0]["date"],"last_date":calendar[-1]["date"],"breadth_type":"BREADTH_PROXY_CURRENT_UNIVERSE_SURVIVOR_BIASED","put_call_provenance":json.loads(PC_META.read_text()) if PC_META.exists() else {"status":"DATA UNAVAILABLE"},"hashes":{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in hash_files if p.exists()}},"gate_coverage":{g:{"favorable_sessions_after_warmup":int(gates[g][cfg["minimum_history"]-1:].sum()),"total_sessions_after_warmup":len(calendar)-cfg["minimum_history"]+1} for g in gates},"latest_market":latest,"years":years,"trial_count":len(trial_list),"weight_combinations":len(weights_grid(cfg)),"results":results,"walk_forward":walk,"bootstrap_2026":boot,"cost_sensitivity_2026":cost_sensitivity,"ranking":ranking,"sector_board":board,"method_notes":["Purged entries/exits spanning calendar-year splits","2026 already viewed; no claim of untouched out of sample","Current universe survivorship bias and no total-return/dividend series","Weight optimization only; fixed cutoff65 and setup not obligatory for selection","PF vs SPY uses identical dates and 10bp round-trip on both series","No missing OHLCV or Put/Call imputed","Gate affects new entries only"]}
    output['data']['ohlcv_content_sha256']=data_hash
    for gate in gates:
        output['gate_coverage'][gate]['annual']={str(year):{'favorable':sum(r[gate] for r in calendar[cfg['minimum_history']-1:] if int(r['date'][:4])==year),'sessions':sum(int(r['date'][:4])==year for r in calendar[cfg['minimum_history']-1:])} for year in years}
    required_pc={r['put_call_source_date'] for r in calendar[cfg['minimum_history']-1:] if r['put_call_source_date']}
    missing_pc=sorted(required_pc-set(pc))
    output['data']['missing_required_put_call_dates']=missing_pc
    output['status']='PARTIAL_DATA_FULL_GATE_FAIL_CLOSED' if missing_pc else 'OK'
    output['data']['coverage']={s:{'bars':len(rows),'first':rows[0]['date'] if rows else None,'last':rows[-1]['date'] if rows else None} for s,rows in bars.items()}
    output['data']['minimum_history_exclusions']={s:len(bars.get(s,[])) for s in universe if s not in assets}
    output['data']['short_history_sources']=[{k:v for k,v in json.loads(p.read_text()).items() if k!='rows'} for p in sorted((ROOT/'data'/'research'/'short_history').glob('*.json'))]
    output['data']['cutoff_reason']='Last download on Oct13 at18:09UTC preceded NY cash close20:00UTC; exclude Oct13 intraday quotes'
    output['pareto_training_2022_2024']=frontier
    output['distinct_decision_sets']={'all_trials':len(set(decision_hashes)),'primary_50_weight_trials':len({decision_hashes[k] for k,t in enumerate(trial_list) if t['grid'] and t['gate']=='FULL' and t['threshold']==65 and not t['require_setup']})}
    sector_results=[]
    for tid in ['CURRENT_BASE_NO_GATE','CURRENT_BASE_FULL',base_id]:
        for sector in universe_cfg['sectors']:
            rows=[r for r in events if r['trial']==tid and r['horizon']==20 and r['sector_etf']==sector]
            sector_results.append({'trial':tid,'sector_etf':sector,**metrics(sums([r['return_pct'] for r in rows],[r['spy_return_pct'] for r in rows]))})
    output['sector_results']=sector_results
    downside=[]
    for tid in sorted(selected_ids):
        rows=[r for r in events if r['trial']==tid and r['horizon']==20 and int(r['signal_date'][:4])==last_year]
        excursions=[r['mae_pct'] for r in rows if r['mae_pct'] is not None]
        downside.append({'trial':tid,'year':last_year,'n':len(rows),'valid_mae':len(excursions),'return_p05_pct':float(np.percentile([r['return_pct'] for r in rows],5)) if rows else None,'return_p95_pct':float(np.percentile([r['return_pct'] for r in rows],95)) if rows else None,'mean_mae_pct':float(np.mean(excursions)) if excursions else None,'mae_p05_pct':float(np.percentile(excursions,5)) if excursions else None})
    output['downside_2026']=downside
    output['previous_research_correction']='reports/score_research/score_research_report.md now uses CASH_SHARES_LEDGER_V2; original portfolio results superseded and archived'
    _csv(OUT/"pareto_training.csv",frontier);_csv(OUT/'sector_results.csv',sector_results)
    _csv(OUT/"sensitivity.csv",flat);_csv(OUT/"events.csv",events);_csv(OUT/"market_gate.csv",calendar);_csv(OUT/"ranking.csv",ranking);_csv(OUT/"sector_board.csv",board)
    (OUT/"results.json").write_text(json.dumps(output,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    (OUT/"report.md").write_text(render_report(output),encoding="utf-8")
    print(f"Completed {len(trial_list)} configurations · {len(events)} audited outcome rows",flush=True)
    return output

def render_report(result):
    by_id={r["id"]:r for r in result["results"]};base="T25_R30_V30_S15_TH65_FULL_SETUP0"
    ids=["CURRENT_BASE_NO_GATE","CURRENT_BASE_FULL","PROPOSED_BASE_NO_GATE","PROPOSED_BASE_TREND_ONLY","PROPOSED_BASE_TREND_BREADTH","T25_R30_V30_S15_TH65_PARTIAL_NO_PC_SETUP0",base,"T25_R30_V30_S15_TH65_FULL_SETUP1"]
    lines=["# Sensibilidad de régimen y score", "",f"Corte: {result['as_of']} · configuraciones: {result['trial_count']} · tuplas de pesos: {result['weight_combinations']}","","## Resultado base y ablaciones","","Win rate indica retorno neto >0; PF del exceso mide ganancias/pérdidas relativas frente a SPY en los mismos intervalos. No es una probabilidad ni un retorno de cartera.","","| Modelo | Ruedas | N | WR % | PF | PF SPY | PF exceso | Ret. medio % | Exceso medio % |","|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    policy='Cruce nuevo del score al cierre, aceptado/bloqueado por gate y setup opcional. Abrir el gate no fabrica una entrada cuando el score ya estaba arriba.' if result['config']['entry_policy']=='SCORE_CROSS_FILTERED_BY_GATE' else 'VARIANTE: cruce de elegibilidad combinada. Abrir el gate puede generar una entrada aun cuando el score ya estuviera arriba. No atribuir ese cambio de fecha exclusivamente al filtrado de señales.'
    lines[6:6]=['','Política: '+policy+' Política alternativa archivada: entry_policy_eligibility_cross/results.json.','']
    def val(v):return "DATA UNAVAILABLE" if v is None else f"{v:.3f}"
    aliases=['Actual sin gate','Actual gate completo','Propuesto sin gate','Propuesto tendencia','Propuesto tendencia+breadth','Propuesto gate sin PC','Propuesto gate completo','Propuesto gate+setup']
    for tid,label in zip(ids,aliases):
        for h in [5,10,20]:
            m=by_id[tid]["horizons"][str(h)]["all"]
            lines.append(f"| {label} | {h} | {m['n']} | {val(m['win_rate_pct'])} | {val(m['profit_factor'])} | {val(m['spy_profit_factor'])} | {val(m['excess_profit_factor'])} | {val(m['mean_return_pct'])} | {val(m['mean_excess_pct'])} |")
    lines += ["","## Cobertura del gate","","| Gate | Favorables | Ruedas tras warm-up |","|---|---:|---:|"]
    for gate,c in result["gate_coverage"].items():lines.append(f"| {gate} | {c['favorable_sessions_after_warmup']} | {c['total_sessions_after_warmup']} |")
    lines += ['','| Año | Ruedas observadas | Gate FULL favorable | Gate sin PC favorable |','|---|---:|---:|---:|']
    for year in result['years']:
        full=result['gate_coverage']['FULL']['annual'][str(year)];partial=result['gate_coverage']['PARTIAL_NO_PC']['annual'][str(year)]
        lines.append(f"| {year} | {full['sessions']} | {full['favorable']} | {partial['favorable']} |")
    latest=result['latest_market']
    lines += ['',f"Al corte {latest['date']}: SPY tendencia={latest['spy_trend']}, QQQ tendencia={latest['qqq_trend']}; breadth observado={val(latest['breadth_pct'])}%, límites={val(latest['breadth_lower_pct'])}–{val(latest['breadth_upper_pct'])}%; VIX={val(latest['vix'])}; Put/Call={val(latest['put_call'])} de {latest['put_call_source_date']}; gate={latest['full_status']}.",'']
    lines += ['## Frontera de sensibilidad en entrenamiento 2022–2024','',f"Entre50 pesos, hay {result['distinct_decision_sets']['primary_50_weight_trials']} conjuntos distintos de decisiones con umbral65 y gate FULL. Varias tuplas pueden producir las mismas entradas porque los subcriterios son binarios. Frontera Pareto de win rate y PF del exceso, sin usar2025/2026 para construirla.",'','| Pesos T/RS/VCP/S | N train | WR train % | PF exceso train | Exceso medio train % |','|---|---:|---:|---:|---:|']
    for p in result['pareto_training_2022_2024']:
        lines.append(f"| {'/'.join(map(str,p['weights']))} | {p['n']} | {val(p['win_rate_pct'])} | {val(p['excess_profit_factor'])} | {val(p['mean_excess_pct'])} |")
    lines += ["","## Selección walk-forward: sólo pesos, umbral65","","Cada peso se elige con años anteriores; los trades que atraviesan un cambio de año se purgan. Mínimo100 eventos y12 fechas de entrada en training. Dos objetivos se comparan explícitamente. 2026 ya se vio en investigaciones previas: es test temporal retrospectivo.","","| Año test | Objetivo | Pesos T/RS/VCP/S | N train | PF exceso train | N test | WR test | PF test | PF exceso test | Exceso test % |","|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for w in result["walk_forward"]:
        if w["status"]!="OK":lines.append(f"| {w['test_year']} | {w['objective']} | NO CANDIDATE | 0 | — | 0 | — | — | — | — |");continue
        m=w["test"]["20"];train=w["training"]
        lines.append(f"| {w['test_year']} | {w['objective']} | {'/'.join(map(str,w['weights']))} | {train['n']} | {val(train['excess_profit_factor'])} | {m['n']} | {val(m['win_rate_pct'])} | {val(m['profit_factor'])} | {val(m['excess_profit_factor'])} | {val(m['mean_excess_pct'])} |")
    lines += ["","## Sensibilidad de umbral del modelo base, gate FULL, 20 ruedas","","| Umbral | Setup requerido | N | WR % | PF | PF exceso | Exceso medio % |","|---|---|---:|---:|---:|---:|---:|"]
    for threshold,setup in itertools.product([65,70,75,80],[False,True]):
        tid=f"T25_R30_V30_S15_TH{threshold}_FULL_SETUP{int(setup)}";m=by_id[tid]["horizons"]["20"]["all"]
        lines.append(f"| {threshold} | {setup} | {m['n']} | {val(m['win_rate_pct'])} | {val(m['profit_factor'])} | {val(m['excess_profit_factor'])} | {val(m['mean_excess_pct'])} |")
    lines += ["","## Incertidumbre por meses de entrada (2026, 20 ruedas)","","Bootstrap de2000 muestras por meses completos. Mantiene juntos los eventos del mismo mes; no elimina todas las dependencias de mercado ni corrige selección de modelo.","","| Candidato | Meses | WR IC95% | Exceso medio IC95% | Prob. exceso medio negativo |","|---|---:|---|---|---:|"]
    for tid,b in result["bootstrap_2026"].items():
        if b["status"]=="OK":lines.append(f"| {tid} | {b['months']} | {b['win_rate_ci95_pct'][0]:.2f}–{b['win_rate_ci95_pct'][1]:.2f}% | {b['mean_excess_ci95_pct'][0]:.3f}–{b['mean_excess_ci95_pct'][1]:.3f}% | {100*b['probability_mean_excess_negative']:.1f}% |")
    lines += ["","## Datos y límites","",f"{result['data']['configured_tickers']} tickers configurados, {result['data']['assets_with_minimum_history']} con warm-up suficiente. Faltantes: {', '.join(result['data']['missing_symbols'])}. Breadth BREADTH_PROXY: universo actual retrospectivo; rangos inferior/superior revelan incertidumbre de componentes sin dato.","","Put/Call TOTAL de Cboe por fecha; se usa la rueda inmediatamente anterior por ausencia de timestamp histórico de publicación. VIX de la serie local ^VIX. No se rellena ningún dato faltante.","","El gate puede mejorar WR por seleccionar mercado alcista y reducir muestra. Su aportación debe leerse contra SPY en las mismas fechas, PF del exceso y cobertura. No se comparan sólo retornos absolutos de exposiciones distintas.","","ATR14 usa media simple de TR como el motor existente. RRG es proxy normalizado documentado, no JdK propietario. Bases VCP y pivotes son observaciones causales; el breakout usa resistencia conocida en la barra anterior.","","Los 800 experimentos aumentan el riesgo de selección múltiple. No hay holdout intacto ni datos de constituyentes históricos, dividendos, execution impact o delistings. Un ganador in-sample no justifica promoción operativa.","","## Archivos y ejecución","","python -m app.cboe_put_call --as-of 2026-10-12","","python -m app.regime_sensitivity","","sensitivity.csv: todas las métricas por año/configuración/horizonte. events.csv: candidatos auditados. market_gate.csv: gates diarios y fuentes. ranking.csv: clasificación del universo. sector_board.csv: ETF y cartelera. results.json: configuración, huellas, bootstrap y costes10/25/50pb.","","Reglas: docs/regime_sensitivity_blueprint.md. Código: app/regime_sensitivity.py. El dashboard, sus pesos y su Pine activo permanecen ajenos a esta investigación.",""]
    return "\n".join(lines)

if __name__=="__main__":refresh()
