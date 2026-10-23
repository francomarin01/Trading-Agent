import json
import unittest
from pathlib import Path

from app.universe import ConstituentsTable, GICS_TO_ETF


class UniverseTests(unittest.TestCase):
    def test_constituents_parser_and_sector_map(self):
        html='<table id="constituents"><tr><th>Symbol</th></tr><tr><td>BRK.B</td><td>Berkshire</td><td>Financials</td></tr></table>'
        parser=ConstituentsTable(); parser.feed(html)
        self.assertEqual(parser.rows,[['BRK.B','Berkshire','Financials']])
        self.assertEqual(GICS_TO_ETF[parser.rows[0][2]],'XLF')


    def test_curated_lenses_are_unique_and_have_documented_benchmarks(self):
        root = Path(__file__).resolve().parents[1]
        cfg = json.loads((root / 'config' / 'universe.json').read_text(encoding='utf-8'))
        expected = {'SMH', 'NCLD', 'EWZ', 'ARGT'}
        self.assertTrue(expected.issubset(cfg['sectors']))
        self.assertTrue(expected.issubset(cfg['universe_groups']))
        owners = {}
        for etf, tickers in cfg['sectors'].items():
            for ticker in tickers:
                self.assertNotIn(ticker, owners, f'{ticker} is assigned to both {owners.get(ticker)} and {etf}')
                owners[ticker] = etf
        for etf in expected:
            group = cfg['universe_groups'][etf]
            self.assertEqual(group['benchmark'], etf)
            self.assertGreater(len(cfg['sectors'][etf]), 0)
        self.assertTrue({'NBIS', 'CRWV', 'IREN', 'HUT', 'WULF', 'APLD', 'CIFR', 'GLXY', 'CORZ', 'CLSK'}.issubset(cfg['sectors']['NCLD']))
