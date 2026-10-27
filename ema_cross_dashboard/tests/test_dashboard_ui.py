import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class DashboardUiTests(unittest.TestCase):
    def test_internal_analysis_tabs_are_not_part_of_public_navigation(self):
        html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
        for tab in ('backtest', 'research', 'technical'):
            self.assertNotIn(f'data-tab="{tab}"', html)
            self.assertNotIn(f'data-view="{tab}"', html)

    def test_fundamental_matrix_is_sector_industry_peer_comparison(self):
        html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
        js = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn('fundamentalSectorMap', html)
        self.assertIn('fundamentalRows', html)
        self.assertIn('PEG esperado', html)
        self.assertIn('Finviz', html)
        self.assertIn("asset.fundamental_peers", js)
        self.assertIn("asset.fundamentals?.sector", js)

    def test_html_loads_the_canonical_javascript_file(self):
        html = (ROOT / 'web' / 'index.html').read_text(encoding='utf-8')
        self.assertIn('<script src="app.js"></script>', html)
        self.assertNotIn('<script>const $ = id =>', html)

    def test_static_dashboard_is_read_only_and_uses_spy_for_excess(self):
        js = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
        updater = (ROOT / 'app' / 'updater.py').read_text(encoding='utf-8')
        self.assertIn("dataset.deployment === 'static'", js)
        self.assertIn("'benchmark_symbol':config()['benchmark']", updater)
        self.assertIn("'excess_return_pct':round(ret-bench", updater)

    def test_matrix_does_not_treat_sec_cache_as_yahoo_fundamentals(self):
        js = (ROOT / 'web' / 'app.js').read_text(encoding='utf-8')
        self.assertIn("asset.fundamentals?.source === 'Finviz CSV export'", js)
        self.assertIn('asset.fundamentals?.schema_version === 3', js)
        self.assertIn('CSV exportado desde Finviz', js)


if __name__ == '__main__':
    unittest.main()
