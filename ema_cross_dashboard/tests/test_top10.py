import sqlite3
import unittest

from app.top10 import current_top10, register_top10_events, summarize_current_top10, update_top10_outcomes


def bars(step=1.0, sessions=30):
    return [
        {'date': f'2026-01-{index + 1:02d}', 'open': 100 + step * index - .25,
         'high': 101 + step * index, 'low': 99 + step * index,
         'close': 100 + step * index, 'volume': 1000}
        for index in range(sessions)
    ]


class TopTenTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        self.con.executescript('''
            CREATE TABLE top10_events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT NOT NULL, sector_etf TEXT NOT NULL,
              signal_date TEXT NOT NULL, signal_score REAL NOT NULL, entry_rank INTEGER NOT NULL,
              entry_date TEXT, entry_open REAL, status TEXT NOT NULL DEFAULT 'PENDING_ENTRY',
              updated_at TEXT NOT NULL, UNIQUE(ticker, signal_date));
            CREATE TABLE top10_membership (ticker TEXT PRIMARY KEY, is_top10 INTEGER NOT NULL, last_date TEXT NOT NULL);
        ''')
        self.assets = [{'ticker': f'A{index:02d}', 'sector_etf': 'XLK', 'opportunity_score': 100 - index, 'state': 'WATCH'} for index in range(11)]
        self.snapshot = {'as_of': '2026-01-05', 'assets': self.assets}
        self.market = {'SPY': bars(1), 'XLK': bars(.5)}
        self.market.update({asset['ticker']: bars(2 + index / 10) for index, asset in enumerate(self.assets)})

    def tearDown(self):
        self.con.close()

    def test_records_exactly_top_ten_once_and_reports_horizons_from_entry(self):
        self.assertEqual(register_top10_events(self.con, self.snapshot), 10)
        self.assertEqual(register_top10_events(self.con, self.snapshot), 0)
        events = update_top10_outcomes(self.con, self.market, {'benchmark': 'SPY', 'backtest': {'round_trip_cost_bps': 10}})
        self.assertEqual(len(events), 10)
        self.assertTrue(all(event['entry_date'] == '2026-01-06' for event in events))
        self.assertTrue(all(event['return_5d_pct'] is not None for event in events))
        board = current_top10(self.snapshot, events)
        self.assertEqual([row['ticker'] for row in board], [f'A{index:02d}' for index in range(10)])
        summary = summarize_current_top10(board)
        self.assertEqual([item['horizon_sessions'] for item in summary], [5, 10, 20])
        self.assertEqual(summary[0]['valid'], 10)

    def test_reentry_after_leaving_top_ten_creates_a_new_event(self):
        register_top10_events(self.con, self.snapshot)
        removed = {'as_of': '2026-01-06', 'assets': self.assets[1:]}
        register_top10_events(self.con, removed)
        reentered = {'as_of': '2026-01-07', 'assets': self.assets}
        self.assertEqual(register_top10_events(self.con, reentered), 1)
        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM top10_events WHERE ticker='A00'").fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
