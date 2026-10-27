import csv
import json
import tempfile
import unittest
from pathlib import Path

from app.fundamentals import FinvizImportError, import_finviz_csv, peer_context, refresh_fundamentals


class FinvizFundamentalTests(unittest.TestCase):
    def csv_file(self, folder, rows, headers=None):
        headers = headers or ['Ticker', 'Sector', 'Industry', 'P/E', 'Forward P/E', 'PEG', 'ROE', 'EV/Sales', 'EV/EBITDA', 'P/S', 'EPS growth next 5 years']
        path = Path(folder) / 'finviz.csv'
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_import_normalizes_financial_fields_and_forward_peg(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            source = self.csv_file(temp, [{
                'Ticker': 'MU', 'Sector': 'Technology', 'Industry': 'Semiconductors',
                'P/E': '30.2', 'Forward P/E': '18.4', 'PEG': '0.10', 'ROE': '21.5%',
                'EV/Sales': '-', 'EV/EBITDA': '12.4', 'P/S': '8.1', 'EPS growth next 5 years': '28.0%',
            }])
            output = Path(temp) / 'normalized.json'
            result = import_finviz_csv(source, '2026-10-23', output)
            record = result['records']['MU']
            self.assertEqual(result['source'], 'Finviz CSV export')
            self.assertEqual(record['metrics']['peg_forward'], 0.1)
            self.assertEqual(record['metrics']['roe_pct'], 21.5)
            self.assertIsNone(record['metrics']['ev_sales'])
            self.assertEqual(record['metrics']['ev_ebitda'], 12.4)
            self.assertEqual(record['metrics']['eps_growth_next_5y_pct'], 28.0)
            self.assertTrue(output.exists())

    def test_missing_required_columns_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            source = self.csv_file(temp, [{'Ticker': 'MU', 'Sector': 'Technology'}], ['Ticker', 'Sector'])
            with self.assertRaisesRegex(FinvizImportError, 'Industry'):
                import_finviz_csv(source, '2026-10-23', Path(temp) / 'out.json')

    def test_invalid_or_future_export_date_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            source = self.csv_file(temp, [{'Ticker': 'MU', 'Sector': 'Technology', 'Industry': 'Semiconductors', 'PEG': '0.1'}])
            with self.assertRaisesRegex(FinvizImportError, 'YYYY-MM-DD'):
                import_finviz_csv(source, '23/10/2026', Path(temp) / 'out.json')
            with self.assertRaisesRegex(FinvizImportError, 'futuro'):
                import_finviz_csv(source, '2999-01-01', Path(temp) / 'out.json')

    def test_duplicate_tickers_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            rows = [{'Ticker': 'MU', 'Sector': 'Technology', 'Industry': 'Semiconductors', 'PEG': '0.1'}] * 2
            source = self.csv_file(temp, rows)
            with self.assertRaisesRegex(FinvizImportError, 'duplicado'):
                import_finviz_csv(source, '2026-10-23', Path(temp) / 'out.json')

    def test_no_metrics_remain_missing_and_do_not_change_technical_score(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            source = self.csv_file(temp, [{'Ticker': 'MU', 'Sector': 'Technology', 'Industry': 'Semiconductors', 'PEG': '-'}])
            output = Path(temp) / 'normalized.json'
            import_finviz_csv(source, '2026-10-23', output)
            asset = {'ticker': 'MU', 'score': 80, 'opportunity_score': 75}
            result = refresh_fundamentals(None, [asset], {'fundamentals': {'snapshot_path': str(output)}})
            self.assertEqual(result['covered_count'], 1)
            self.assertEqual(asset['fundamentals']['status'], 'DATA UNAVAILABLE')
            self.assertIsNone(asset['valuation']['peg_forward'])
            self.assertEqual((asset['score'], asset['opportunity_score']), (80, 75))

    def test_missing_snapshot_is_data_unavailable(self):
        asset = {'ticker': 'MU', 'score': 80}
        result = refresh_fundamentals(None, [asset], {'fundamentals': {'snapshot_path': 'not-found.json'}})
        self.assertEqual(result['status'], 'DATA UNAVAILABLE')
        self.assertEqual(asset['fundamentals']['status'], 'DATA UNAVAILABLE')

    def test_peer_means_exclude_subject_and_use_industry_first(self):
        assets = [
            {'ticker': 'MU', 'fundamentals': {'sector': 'Technology', 'industry': 'Semiconductors', 'metrics': {'peg_forward': 0.1, 'roe_pct': 20}}},
            {'ticker': 'SNDK', 'fundamentals': {'sector': 'Technology', 'industry': 'Semiconductors', 'metrics': {'peg_forward': 0.25, 'roe_pct': 30}}},
            {'ticker': 'AAPL', 'fundamentals': {'sector': 'Technology', 'industry': 'Consumer Electronics', 'metrics': {'peg_forward': 2.0, 'roe_pct': 150}}},
        ]
        context = peer_context(assets)
        self.assertEqual(context['MU']['peer_group'], 'Semiconductors')
        self.assertEqual(context['MU']['averages']['peg_forward'], 0.25)
        self.assertEqual(context['MU']['averages']['roe_pct'], 30)
        self.assertEqual(context['MU']['peer_count'], 1)


if __name__ == '__main__':
    unittest.main()
