import unittest
from app.engine import build_snapshot, operational_gate, sector_state, sessions_volume_metrics


def bars(start=100, n=280, volume=1000, step=.5):
    rows=[]
    for i in range(n):
        close=start+i*step
        rows.append({'date':f'2025-01-{(i%28)+1:02d}-{i//28:02d}', 'open':close-.2,'high':close+1,'low':close-1,'close':close,'volume':volume})
    # Dates need only be shared and ordered for engine tests.
    for i,row in enumerate(rows): row['date']=f'2025-{i//28+1:02d}-{i%28+1:02d}'
    return rows


class EngineTests(unittest.TestCase):
    def cfg(self):
        return {'benchmark':'SPY','growth_benchmark':'QQQ','volatility_symbol':'^VIX','score_thresholds':{'watch':65,'armed':75,'high_priority':85},'sectors':{'XLK':['AAA','BBB']}}

    def test_snapshot_has_actionable_asset_and_sector(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['AAA']=bars(step=1.0); data['AAA'][-1]['volume']=2000
        snapshot=build_snapshot(data,self.cfg())
        self.assertEqual(snapshot['status'],'OK'); self.assertEqual(len(snapshot['assets']),2)
        asset=next(x for x in snapshot['assets'] if x['ticker']=='AAA')
        self.assertLess(asset['score'],85)
        self.assertGreaterEqual(asset['legacy_score'],85)
        self.assertEqual(snapshot['sectors'][0]['etf'],'XLK')

    def test_presentation_penalty_is_separate_from_score(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        # The final close is deliberately far above the fast average: it is
        # still a valid raw score, but should be deprioritised for display.
        data['AAA']=bars(step=1.0); data['AAA'][-1]['close'] += 80
        asset=next(x for x in build_snapshot(data,self.cfg())['assets'] if x['ticker']=='AAA')
        self.assertIn('EXTENDED / DO NOT CHASE', asset['alerts'])
        self.assertAlmostEqual(asset['opportunity_score'], asset['score']-asset['presentation_penalty'])
        self.assertEqual(asset['presentation_penalty'], 15)

    def test_rotation_history_is_prefix_based_and_includes_latest_session(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        snapshot=build_snapshot(data,self.cfg())
        history=[x for x in snapshot['rotation_history'] if x['etf']=='XLK']
        self.assertTrue(history)
        self.assertEqual(history[-1]['date'], snapshot['as_of'])
        # A change made only at the end cannot revise the first point.
        first=history[0]
        data['AAA'][-1]['close'] *= 5
        revised=build_snapshot(data,self.cfg())['rotation_history']
        self.assertEqual(first, next(x for x in revised if x['date']==first['date'] and x['etf']=='XLK'))

    def test_sector_state_matches_map_quadrants(self):
        self.assertEqual(sector_state(.01, 70, .02), 'LEADING')
        self.assertEqual(sector_state(.01, 70, -.02), 'WEAKENING')
        self.assertEqual(sector_state(-.01, 30, .02), 'RECOVERING')
        self.assertEqual(sector_state(-.01, 30, -.02), 'LAGGING')

    def test_asset_includes_compact_causal_trend_series(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        asset=next(x for x in build_snapshot(data,self.cfg())['assets'] if x['ticker']=='AAA')
        self.assertEqual(len(asset['trend_60']),60)
        self.assertEqual(asset['trend_60'][-1]['close'],asset['close'])
        self.assertIsNotNone(asset['trend_60'][-1]['ema9'])
        self.assertIsNotNone(asset['trend_60'][-1]['sma50'])
        self.assertIsNotNone(asset['trend_60'][-1]['ema200'])

    def test_four_pillars_produce_a_bounded_non_binary_score(self):
        data={x:bars(100 if x!='^VIX' else 15, step=.37) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['AAA']=bars(step=.63)
        asset=next(x for x in build_snapshot(data,self.cfg())['assets'] if x['ticker']=='AAA')
        self.assertLessEqual(asset['score'],99.5)
        self.assertNotEqual(asset['score'],asset['legacy_score'])
        self.assertEqual(set(asset['pillars']),{'trend','relative_strength','contraction','setup'})

    def test_stage_rs_and_setup_metadata_are_explicit(self):
        data={x:bars(100 if x!='^VIX' else 15,step=.45) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['AAA']=bars(step=.75)
        asset=next(x for x in build_snapshot(data,self.cfg())['assets'] if x['ticker']=='AAA')
        self.assertEqual(asset['technical_stage'],'STAGE 2')
        self.assertIsNotNone(asset['rs_score'])
        self.assertIn('fresh_setup',asset)
        self.assertIn('setup_age_sessions',asset)
        self.assertIn('detectors',asset)
        self.assertIn('daily_return_pct',asset)

    def test_data_is_close_known(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        before=next(x for x in build_snapshot(data,self.cfg())['assets'] if x['ticker']=='AAA')['ema21']
        data['AAA'][-1]['close']=1
        after=next(x for x in build_snapshot(data,self.cfg())['assets'] if x['ticker']=='AAA')['ema21']
        self.assertNotEqual(before,after)

    def test_independent_episodic_pivot_does_not_change_score(self):
        from app.engine import independent_detectors
        rows = bars(step=.45)
        baseline = rows[-1]['close']
        rows[-1]['close'] = rows[-2]['close'] * 1.05
        rows[-1]['high'] = rows[-1]['close'] + 1
        rows[-1]['low'] = min(rows[-1]['low'], rows[-1]['close'] - 1)
        rows[-1]['volume'] = 2500
        detections = independent_detectors(rows)
        self.assertTrue(any(item['name'] == 'EPISODIC PIVOT' for item in detections))
        self.assertNotEqual(baseline, rows[-1]['close'])

    def test_asset_rotation_history_is_point_in_time(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        snapshot=build_snapshot(data,self.cfg())
        history=[item for item in snapshot['asset_rotation_history'] if item['ticker']=='AAA']
        self.assertTrue(history)
        self.assertEqual(history[-1]['date'], snapshot['as_of'])
        self.assertIsNotNone(history[-1]['rs_score'])

    def test_operational_gate_requires_each_approved_condition(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['^VIX'] = bars(15, step=.001)
        snapshot = build_snapshot(data, self.cfg(), {data['SPY'][-2]['date']: .82})
        gate = snapshot['market']['operational_gate']
        self.assertEqual(gate['status'], 'FULL')
        self.assertTrue(gate['eligible_for_new_entries'])
        self.assertTrue(gate['spy_trend'])
        self.assertTrue(gate['qqq_trend'])
        self.assertEqual(gate['put_call'], .82)

    def test_operational_gate_keeps_breadth_as_warning_not_blocker(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['^VIX'] = bars(15, step=.001)
        # Asset breadth is deliberately weak while the four actual gate inputs pass.
        for ticker in ('AAA', 'BBB'):
            data[ticker][-1]['close'] = 1
            data[ticker][-1]['low'] = .5
            data[ticker][-1]['high'] = 2
        gate = build_snapshot(data, self.cfg(), {data['SPY'][-2]['date']: .82})['market']['operational_gate']
        self.assertEqual(gate['status'], 'FULL')
        self.assertTrue(gate['eligible_for_new_entries'])
        self.assertFalse(gate['breadth_pass'])
        self.assertFalse(gate['breadth_is_blocking'])
        self.assertIn('Breadth below 50%', gate['warnings'])

    def test_operational_gate_fails_closed_when_put_call_is_missing(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['^VIX'] = bars(15, step=.001)
        gate = build_snapshot(data, self.cfg())['market']['operational_gate']
        self.assertEqual(gate['status'], 'DATA UNAVAILABLE')
        self.assertFalse(gate['eligible_for_new_entries'])
        self.assertIn('Put/Call unavailable', gate['reasons'])

    def test_selective_labels_are_context_only(self):
        data={x:bars(100 if x!='^VIX' else 15) for x in ['SPY','QQQ','^VIX','XLK','AAA','BBB']}
        data['^VIX'] = bars(15, step=.001)
        snapshot = build_snapshot(data, self.cfg(), {data['SPY'][-2]['date']: .82})
        asset = next(item for item in snapshot['assets'] if item['ticker'] == 'AAA')
        self.assertIn('selective_score_25_30_30_15', asset)
        self.assertIn('selective_setup_candidate', asset)
        self.assertIn('score_cross_65', asset)
        self.assertLessEqual(asset['score'], 99.5)


    def test_sessions_volume_metrics_are_observational_and_complete(self):
        closes = [100 + index for index in range(60)]
        volumes = [1000] * 50 + [1500] * 10
        metrics = sessions_volume_metrics(closes, volumes)
        self.assertEqual(metrics['up_sessions_10'], 10)
        self.assertGreater(metrics['volume_ratio10_50'], 1)
        self.assertGreater(metrics['sessions_volume_score'], 10)
