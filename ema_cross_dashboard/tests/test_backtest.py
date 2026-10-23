import unittest
from app.backtest import run
from app.engine import four_pillar_score


def bars(n=285, start=100, step=1):
    return [{'date':f'2025-{i//28+1:02d}-{i%28+1:02d}','open':start+i*step,'high':start+i*step+1,'low':start+i*step-1,'close':start+i*step+.5,'volume':1000} for i in range(n)]


class BacktestTests(unittest.TestCase):
    def cfg(self): return {'benchmark':'SPY','score_thresholds':{'watch':65,'armed':75,'high_priority':85},'backtest':{'horizons_sessions':[5,10],'round_trip_cost_bps':0,'minimum_history_sessions':252},'sectors':{'XLK':['AAA']}}
    def test_backtest_enters_after_signal_without_transaction_costs(self):
        data={'SPY':bars(step=.2),'XLK':bars(step=.3),'AAA':bars(step=1)}
        data['AAA'][251]['volume']=2000
        result=run(data,self.cfg())
        self.assertEqual(result['status'],'OK')
        self.assertTrue(result['signals'])
        self.assertGreater(result['signals'][0]['entry_date'],result['signals'][0]['signal_date'])
        self.assertTrue(result['summary'])
    def test_insufficient_history_is_explicit(self):
        self.assertEqual(run({'SPY':bars(10)},self.cfg())['status'],'DATA UNAVAILABLE')

    def test_four_pillar_model_is_identified_and_point_in_time(self):
        data={'SPY':bars(step=.2),'XLK':bars(step=.3),'AAA':bars(step=1)}
        result=run(data,self.cfg(),four_pillar_score,'FOUR_PILLAR')
        self.assertEqual(result['status'],'OK')
        self.assertEqual(result['model'],'FOUR_PILLAR')
        for signal in result['signals']:
            self.assertGreater(signal['entry_date'],signal['signal_date'])
