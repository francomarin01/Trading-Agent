import json
import sqlite3
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.fundamentals import refresh_fundamentals


class FundamentalPipelineTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.execute('CREATE TABLE sec_ticker_map(source TEXT, fetched_at TEXT, payload TEXT)')
        self.con.execute('''CREATE TABLE fundamental_snapshots(
            ticker TEXT, source TEXT, fetched_at TEXT, status TEXT, filed_at TEXT,
            period_end TEXT, form TEXT, payload TEXT, PRIMARY KEY(ticker, source))''')
        self.asset = {'ticker': 'TEST', 'score': 80, 'opportunity_score': 75, 'close': 100.0, 'date': '2026-10-20'}

    def tearDown(self):
        self.con.close()

    def test_missing_sec_identity_is_explicit_and_does_not_change_technical_fields(self):
        original = dict(self.asset)
        with patch.dict('os.environ', {'SEC_USER_AGENT': ''}):
            result = refresh_fundamentals(self.con, [self.asset], {'fundamentals': {'enabled': True}})
        self.assertEqual(result['covered_count'], 0)
        self.assertEqual(self.asset['score'], original['score'])
        self.assertEqual(self.asset['opportunity_score'], original['opportunity_score'])
        self.assertEqual(self.asset['fundamentals']['status'], 'DATA UNAVAILABLE')
        self.assertIsNone(self.asset['valuation']['price_to_earnings'])

    def test_cached_fundamentals_revalue_at_latest_close_without_rewriting_score(self):
        now = datetime.now(timezone.utc).isoformat()
        record = {
            'schema_version': 2, 'ticker': 'TEST', 'status': 'OK', 'source': 'SEC EDGAR',
            'filed_at': '2026-10-01', 'period_end': '2026-09-30', 'form': '10-Q',
            'shares_outstanding': 10, 'net_income_ttm': 100, 'eps_diluted_ttm': 5,
            'eps_growth_yoy_pct': 25, 'revenue_ttm': 500, 'ebitda_ttm': 125,
            'debt': 50, 'cash': 25, 'shareholders_equity': 500, 'free_cash_flow': 100,
        }
        self.con.execute('INSERT INTO fundamental_snapshots VALUES(?,?,?,?,?,?,?,?)',
                         ('TEST', 'SEC EDGAR', now, 'OK', record['filed_at'], record['period_end'], record['form'], json.dumps(record)))
        self.asset['close'] = 120.0
        with patch.dict('os.environ', {'SEC_USER_AGENT': ''}):
            refresh_fundamentals(self.con, [self.asset], {'fundamentals': {'enabled': True}})
        self.assertEqual(self.asset['valuation']['market_cap_indicative'], 1200)
        self.assertEqual(self.asset['valuation']['price_to_earnings'], 24)
        self.assertEqual(self.asset['score'], 80)
        self.assertEqual(self.asset['opportunity_score'], 75)


if __name__ == '__main__':
    unittest.main()
