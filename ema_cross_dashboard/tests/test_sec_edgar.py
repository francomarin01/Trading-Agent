import gzip
import json
import unittest
from datetime import date, timedelta

from app.sec_edgar import _decode_response, _ttm, company_fundamentals, valuation_metrics


def _quarter_rows(values):
    rows = []
    end = date(2024, 3, 31)
    for index, value in enumerate(values):
        start = end - timedelta(days=89)
        filed = end + timedelta(days=45)
        rows.append({'val': value, 'form': '10-Q', 'filed': filed.isoformat(), 'start': start.isoformat(), 'end': end.isoformat()})
        end = (end + timedelta(days=91))
    return rows


def _fixture_payload():
    quarters = {
        'Revenues': _quarter_rows([80, 80, 80, 80, 100, 100, 100, 100]),
        'NetIncomeLoss': _quarter_rows([20, 20, 20, 20, 30, 30, 30, 30]),
        'OperatingIncomeLoss': _quarter_rows([22, 22, 22, 22, 25, 25, 25, 25]),
        'NetCashProvidedByUsedInOperatingActivities': _quarter_rows([28, 28, 28, 28, 38, 38, 38, 38]),
        'PaymentsToAcquirePropertyPlantAndEquipment': _quarter_rows([5, 5, 5, 5, 8, 8, 8, 8]),
        'DepreciationDepletionAndAmortization': _quarter_rows([3, 3, 3, 3, 5, 5, 5, 5]),
    }
    us_gaap = {tag: {'units': {'USD': rows}} for tag, rows in quarters.items()}
    us_gaap['EarningsPerShareDiluted'] = {'units': {'USD / shares': _quarter_rows([.4, .4, .4, .4, .5, .5, .5, .5])}}
    us_gaap['CashAndCashEquivalentsAtCarryingValue'] = {'units': {'USD': [{'val': 25, 'form': '10-Q', 'filed': '2026-05-15', 'end': '2026-03-31'}]}}
    us_gaap['LongTermDebtNoncurrent'] = {'units': {'USD': [{'val': 45, 'form': '10-Q', 'filed': '2026-05-15', 'end': '2026-03-31'}]}}
    us_gaap['LongTermDebtCurrent'] = {'units': {'USD': [{'val': 5, 'form': '10-Q', 'filed': '2026-05-15', 'end': '2026-03-31'}]}}
    us_gaap['StockholdersEquity'] = {'units': {'USD': [{'val': 500, 'form': '10-Q', 'filed': '2026-05-15', 'end': '2026-03-31'}]}}
    dei = {'EntityCommonStockSharesOutstanding': {'units': {'shares': [{'val': 10, 'form': '10-Q', 'filed': '2026-05-15', 'end': '2026-03-31'}]}}}
    return {'facts': {'us-gaap': us_gaap, 'dei': dei}}


class SecEdgarTests(unittest.TestCase):
    def test_decodes_gzip_json(self):
        raw = gzip.compress(json.dumps({'ok': True}).encode('utf-8'))
        self.assertEqual(_decode_response(raw), '{"ok": true}')

    def test_ttm_uses_four_discrete_quarters_and_year_ago_comparison(self):
        current, prior = _ttm(_quarter_rows([80, 80, 80, 80, 100, 100, 100, 100]))
        self.assertEqual(current, 400)
        self.assertEqual(prior, 320)

    def test_ttm_does_not_treat_one_quarter_as_full_year(self):
        self.assertEqual(_ttm(_quarter_rows([100])[:1]), (None, None))

    def test_company_fundamentals_uses_filed_facts_and_derives_ttm_metrics(self):
        result = company_fundamentals('TEST', 1234, _fixture_payload())
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['revenue_ttm'], 400)
        self.assertEqual(result['revenue_growth_yoy_pct'], 25)
        self.assertEqual(result['net_income_ttm'], 120)
        self.assertEqual(result['eps_diluted_ttm'], 2)
        self.assertEqual(result['eps_growth_yoy_pct'], 25)
        self.assertEqual(result['free_cash_flow'], 120)
        self.assertEqual(result['debt'], 50)
        self.assertEqual(result['shareholders_equity'], 500)
        self.assertEqual(result['filed_at'], '2026-02-11')
        self.assertIn('0000001234', result['source_url'])

    def test_valuation_metrics_match_explicit_formula(self):
        record = {
            'shares_outstanding': 10, 'net_income_ttm': 100, 'eps_diluted_ttm': 5,
            'eps_growth_yoy_pct': 25, 'revenue_ttm': 500, 'ebitda_ttm': 125,
            'debt': 50, 'cash': 25, 'shareholders_equity': 500, 'free_cash_flow': 100,
        }
        result = valuation_metrics(record, 100)
        self.assertEqual(result['market_cap_indicative'], 1000)
        self.assertEqual(result['price_to_earnings'], 20)
        self.assertEqual(result['peg_historical'], .8)
        self.assertEqual(result['ev_to_sales'], 2.05)
        self.assertEqual(result['ev_to_ebitda'], 8.2)
        self.assertEqual(result['price_to_book'], 2)
        self.assertEqual(result['free_cash_flow_yield_pct'], 10)

    def test_missing_enterprise_value_components_are_not_silently_zeroed(self):
        result = valuation_metrics({'shares_outstanding': 10, 'net_income_ttm': 50, 'revenue_ttm': 200}, 100)
        self.assertIsNone(result['enterprise_value_indicative'])
        self.assertIsNone(result['ev_to_sales'])
        self.assertIsNone(result['ev_to_ebitda'])

    def test_no_xbrl_facts_is_explicitly_unavailable(self):
        result = company_fundamentals('TEST', 1234, {'facts': {}})
        self.assertEqual(result['status'], 'DATA UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
