import unittest
import json
from unittest.mock import patch
from datetime import date,timedelta
import numpy as np
from app.cboe_put_call import parse_page
from app.regime_sensitivity import breadth_bounds,eligibility,features,market_context,metrics,prepare_assets,select_candidate,settings,signal_indices,sums,trials,weights_grid

def bars(n=330):
    rows=[]
    for i in range(n):
        close=100+i*.25+2*np.sin(i/8)
        rows.append({"date":(date(2023,1,1)+timedelta(days=i)).isoformat(),"open":close-.2,"high":close+1,"low":close-1,"close":close,"volume":1000+300*(i%9==0)})
    return rows

class SensitivityTests(unittest.TestCase):
    def test_weight_grid_respects_all_constraints(self):
        cfg=settings();grid=weights_grid(cfg)
        self.assertEqual(len(grid),50)
        self.assertIn([25,30,30,15],grid)
        self.assertTrue(all(sum(w)==100 for w in grid))
        self.assertEqual(sum(t["grid"] for t in trials(cfg)),800)

    def test_invalid_settings_fail_explicitly(self):
        cfg=settings();cfg['entry_policy']='unknown'
        with patch('app.regime_sensitivity.Path.read_text',return_value=json.dumps(cfg)):
            with self.assertRaises(ValueError):settings()

    def test_features_and_pivots_are_prefix_stable(self):
        cfg=settings();rows=bars();spy={r["date"]:r["close"]*.8 for r in rows}
        full=features(rows,spy,cfg);cut=290;prefix=features(rows[:cut],{r["date"]:spy[r["date"]] for r in rows[:cut]},cfg)
        for key in ["components","penalty","setup","vcp_base","episodic","breakout","rs_ratio","rs_momentum","ema200_event"]:
            np.testing.assert_equal(full[key][:cut],prefix[key])

    def test_breadth_reports_uncertainty_without_fill(self):
        ambiguous=breadth_bounds(5,10,10,2)
        self.assertEqual(ambiguous["status"],"AMBIGUOUS")
        self.assertFalse(ambiguous["pass"])
        safe=breadth_bounds(9,10,10,2)
        self.assertTrue(safe["pass"])
        self.assertEqual(safe["lower_pct"],75)
        self.assertEqual(breadth_bounds(0,0,0)["status"],"DATA UNAVAILABLE")

    def test_full_gate_uses_only_previous_put_call_session(self):
        cfg=settings();rows=bars();vix=[{**r,"close":15} for r in rows]
        data={"SPY":rows,"QQQ":rows,"AAA":rows,"^VIX":vix}
        last=rows[-1]["date"];prior=rows[-2]["date"]
        context=market_context(data,["AAA"],cfg,{last:9,prior:.9})
        self.assertTrue(context[-1]["FULL"])
        self.assertEqual(context[-1]["put_call_source_date"],prior)
        self.assertEqual(context[-1]["put_call"],.9)
        missing=market_context(data,["AAA"],cfg,{last:.1})[-1]
        self.assertFalse(missing["FULL"])
        self.assertTrue(missing["PARTIAL_NO_PC"])
        self.assertEqual(missing["full_status"],"DATA UNAVAILABLE")

    def test_rrg_constant_ratio_does_not_create_roundoff_bonuses(self):
        rows=bars();spy={r['date']:r['close']*.8 for r in rows}
        f=features(rows,spy,settings())
        self.assertEqual(f['rs_ratio'][-1],100)
        self.assertEqual(f['rs_momentum'][-1],100)
        self.assertEqual(f['components'][-1,1],0)

    def test_new_outcomes_enter_next_spy_open_without_skipping_missing_day(self):
        cfg=settings();rows=bars();data={'SPY':rows,'XLK':rows,'AAA':rows}
        u={'sectors':{'XLK':['AAA']}}
        asset=prepare_assets(data,u,cfg,[])["AAA"]
        expected=100*(rows[272]['close']/rows[253]['open']-1)
        self.assertAlmostEqual(asset['gross'][252,2],expected)
        self.assertAlmostEqual(asset['mae'][252,2],100*(min(r['low'] for r in rows[253:273])/rows[253]['open']-1))
        data['AAA']=rows[:253]+rows[254:]
        missing=prepare_assets(data,u,cfg,[])["AAA"]
        self.assertTrue(np.isnan(missing['gross'][252,2]))

    def test_vcp_requires_atr_minimum_and_dry_current_volume(self):
        rows=bars()
        for i,r in enumerate(rows):
            cl=100+i*.2;r.update(open=cl,close=cl,high=cl+1,low=cl-1,volume=1000)
        rows[-1]['volume']=500
        f=features(rows,{r['date']:r['close'] for r in rows},settings())
        self.assertTrue(f['compressed'][-1]);self.assertTrue(f['dry'][-1]);self.assertTrue(f['vcp_base'][-1])
        rows[-1]['volume']=800
        f=features(rows,{r['date']:r['close'] for r in rows},settings())
        self.assertFalse(f['dry'][-1]);self.assertFalse(f['vcp_base'][-1])

    def test_episodic_gap_is_strictly_above_eight_percent(self):
        rows=bars();prev=rows[-2]['close'];op=prev*1.09
        rows[-1].update(open=op,close=op+.5,high=op+1,low=op-.5,volume=5000)
        f=features(rows,{r['date']:r['close'] for r in rows},settings())
        self.assertTrue(f['episodic'][-1]);self.assertTrue(f['setup'][-1]);self.assertEqual(f['penalty'][-1],5)
        op=prev*1.08;rows[-1].update(open=op,close=op+.5,high=op+1,low=op-.5)
        f=features(rows,{r['date']:r['close'] for r in rows},settings())
        self.assertFalse(f['episodic'][-1])

    def test_gate_is_prefix_stable_including_put_call_availability(self):
        cfg=settings();rows=bars();data={s:rows for s in ['SPY','QQQ','AAA']};data['^VIX']=[{**r,'close':15} for r in rows]
        pc={r['date']:.9 for r in rows};full=market_context(data,['AAA'],cfg,pc)
        cut=300;prefix=market_context({s:r[:cut] for s,r in data.items()},['AAA'],cfg,pc)
        self.assertEqual(full[:cut],prefix)

    def test_put_call_parser_rejects_wrong_date_and_missing_ratio(self):
        html='selectedDate":"2023-01-03" <td>TOTAL PUT/CALL RATIO</td><td>0.99</td>'
        self.assertEqual(parse_page(html,'2023-01-03'),.99)
        with self.assertRaises(ValueError):parse_page(html,'2023-01-04')
        with self.assertRaises(ValueError):parse_page('selectedDate":"2023-01-03"','2023-01-03')

    def test_profit_factor_and_matching_spy(self):
        m=metrics(sums([2,-1,0],[1,-.5,0]))
        self.assertEqual(m["profit_factor"],2)
        self.assertEqual(m["spy_profit_factor"],2)
        self.assertEqual(m["excess_profit_factor"],2)
        self.assertEqual(metrics(sums([1,2],[0,0]))["profit_factor"],None)
        self.assertEqual(metrics(sums([],[]))["n"],0)

    def test_signal_cooldown_and_future_mask_do_not_rewrite_past(self):
        mask=np.zeros(50,dtype=bool);mask[[1,5,22,43]]=True
        self.assertEqual(signal_indices(mask,np.arange(50),0,20).tolist(),[1,22,43])
        self.assertEqual(signal_indices(mask[:30],np.arange(30),0,20).tolist(),[1,22])

    def test_reopening_gate_does_not_manufacture_new_score_cross(self):
        asset={'current':np.array([60,70,70,70,60,70],dtype=float),'calendar':np.arange(6),'f':{'setup':np.ones(6,dtype=bool)}}
        gates={'FULL':np.array([False,False,True,True,True,True])}
        trial={'model':'CURRENT','threshold':65,'gate':'FULL','require_setup':False,'entry_policy':'SCORE_CROSS_FILTERED_BY_GATE'}
        self.assertEqual(np.flatnonzero(eligibility(asset,trial,gates)).tolist(),[5])
        trial['entry_policy']='ELIGIBILITY_CROSS'
        self.assertEqual(signal_indices(eligibility(asset,trial,gates),np.arange(6),0,1).tolist(),[2,5])

    def test_weight_selector_does_not_see_test_or_future_years(self):
        cfg=settings();ts=[t for t in trials(cfg) if t['grid'] and t['gate']=='FULL' and t['threshold']==65 and not t['require_setup']][:2]
        years=[2023,2024,2025];acc=np.zeros((2,3,3,14));dates=[{"date":f'2023-01-{i:02d}'} for i in range(1,21)]
        hits=np.ones((2,len(dates)),dtype=bool)
        acc[0,2,0]=sums([2]*100+[-1]*50,[.2]*100+[-.1]*50)
        acc[1,2,0]=sums([3]*100+[-1]*50,[.2]*100+[-.1]*50)
        picked=select_candidate(ts,acc,hits,years,dates,2024,'PF_EXCESS',cfg)
        acc[0,2,1]=sums([100]*1000,[0]*1000);acc[0,2,2]=sums([1000]*1000,[0]*1000)
        again=select_candidate(ts,acc,hits,years,dates,2024,'PF_EXCESS',cfg)
        self.assertEqual(picked['trial_index'],again['trial_index'])

if __name__=='__main__':unittest.main()
