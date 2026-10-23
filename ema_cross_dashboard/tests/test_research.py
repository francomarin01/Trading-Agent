import unittest
from app.research import compare


def signal(year, value=1, **flags):
    return {'horizon_sessions':20,'signal_year':year,'score':80,'return_pct':value,'excess_return_pct':value-.1,'trend_above_ema200':True,'relative_strength_positive':True,'sector_outperforming':True,'volume_confirmed':True,'not_extended':True,**flags}


class ResearchTests(unittest.TestCase):
    def test_filters_and_walk_forward_do_not_use_future_year(self):
        rows=[signal(2023) for _ in range(35)]+[signal(2024,-1) for _ in range(35)]
        result=compare(rows,minimum_signals=30)
        self.assertEqual(result['status'],'OK')
        self.assertEqual(result['filters']['BASELINE_SCORE_65']['aggregate']['signals'],70)
        self.assertEqual(result['walk_forward'][0]['test_year'],2024)
    def test_filter_excludes_failed_context(self):
        result=compare([signal(2024,relative_strength_positive=False)],minimum_signals=1)
        self.assertEqual(result['filters']['TREND_RS']['aggregate'],None)

    def test_clean_trend_rejects_weakening_and_extended_signals(self):
        result=compare([signal(2024,ema_bullish=False),signal(2024,not_extended=False)],minimum_signals=1)
        self.assertIsNone(result['filters']['CLEAN_TREND']['aggregate'])
