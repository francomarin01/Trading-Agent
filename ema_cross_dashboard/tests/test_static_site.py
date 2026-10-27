from pathlib import Path
import tempfile
import unittest

from app.static_site import prepare_static_site


class StaticSiteTests(unittest.TestCase):
    def test_static_bundle_marks_read_only_deployment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'web'
            (source / 'data').mkdir(parents=True)
            (source / 'index.html').write_text('<html lang="es"><body>ok</body></html>', encoding='utf-8')
            for name in ('dashboard.json', 'asset_rotation.json', 'backtest_summary.json', 'filter_research.json'):
                (source / 'data' / name).write_text('{}', encoding='utf-8')
            destination = prepare_static_site(source, root / 'dist')
            self.assertIn('data-deployment="static"', (destination / 'index.html').read_text(encoding='utf-8'))
            self.assertTrue((destination / 'data' / 'dashboard.json').exists())
            self.assertTrue((destination / 'data' / 'asset_rotation.json').exists())
            self.assertFalse((destination / 'data' / 'backtest_summary.json').exists())
            self.assertFalse((destination / 'data' / 'filter_research.json').exists())


if __name__ == '__main__':
    unittest.main()
