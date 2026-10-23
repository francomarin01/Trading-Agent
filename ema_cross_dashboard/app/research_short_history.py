"""Retain valid short Yahoo series for research breadth, never production DB."""
from __future__ import annotations
import json
import urllib.parse
import urllib.request
from datetime import datetime,timedelta,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'short_history'

def refresh():
    from .regime_sensitivity import load_data,settings
    cfg=settings();bars,_,universe=load_data(cfg)
    missing=[s for s in universe if not bars.get(s)]
    end=int((datetime.fromisoformat(cfg['as_of']).replace(tzinfo=timezone.utc)+timedelta(days=1)).timestamp())
    start=int(datetime(2021,11,4,tzinfo=timezone.utc).timestamp())
    OUT.mkdir(parents=True,exist_ok=True)
    for symbol in missing:
        url=f'https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol,safe="^")}?period1={start}&period2={end}&interval=1d&events=history'
        try:
            with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 EMA-Cross-Research'}),timeout=30) as response:
                payload=json.loads(response.read().decode('utf-8'))
            result=payload['chart']['result'][0];quote=result['indicators']['quote'][0];rows=[]
            for stamp,op,hi,lo,cl,vol in zip(result['timestamp'],quote['open'],quote['high'],quote['low'],quote['close'],quote['volume']):
                date=datetime.fromtimestamp(stamp,tz=timezone.utc).date().isoformat()
                if date>cfg['as_of'] or any(v is None for v in (op,hi,lo,cl,vol)):continue
                if min(op,hi,lo,cl)<=0 or vol<0 or hi<max(op,lo,cl) or lo>min(op,cl,hi):raise ValueError(f'Invalid quote for {symbol} on {date}')
                rows.append({'date':date,'open':float(op),'high':float(hi),'low':float(lo),'close':float(cl),'volume':float(vol)})
            rows.sort(key=lambda b:b['date'])
            if not rows or len({b['date'] for b in rows})!=len(rows):raise ValueError('No valid unique dated quotes')
            data={'symbol':symbol,'as_of':cfg['as_of'],'source':url,'downloaded_at_utc':datetime.now(timezone.utc).isoformat(),'provider_name':result['meta'].get('longName'),'rows':rows,'use':'BREADTH_ONLY_UNTIL_MINIMUM_HISTORY','imputed':False}
            (OUT/f'{symbol}.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
            print(f'{symbol}: {len(rows)} actual daily bars from {rows[0]["date"]} to {rows[-1]["date"]}',flush=True)
        except Exception as exc:print(f'{symbol}: DATA UNAVAILABLE - {exc}',flush=True)

if __name__=='__main__':refresh()
