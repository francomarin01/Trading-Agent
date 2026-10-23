import sqlite3
import unittest

from app.journal import create_journal_schema, update_journal
from app.watchlist import add_to_watchlist, open_watchlist_position, remove_from_watchlist, render_watchlist


def bars(scale=1.0, sessions=30):
    return [
        {'date': f'2026-01-{index + 1:02d}', 'open': 100 + scale * index - .25,
         'high': 101 + scale * index, 'low': 99 + scale * index,
         'close': 100 + scale * index, 'volume': 1000}
        for index in range(sessions)
    ]


class WatchlistTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        create_journal_schema(self.con)
        self.con.execute('''CREATE TABLE watchlist (
            ticker TEXT PRIMARY KEY, sector_etf TEXT NOT NULL, added_date TEXT NOT NULL,
            added_at TEXT NOT NULL, position_status TEXT NOT NULL DEFAULT 'WATCHING'
        )''')
        self.asset = {'ticker': 'AAA', 'sector_etf': 'XLK', 'date': '2026-01-05', 'score': 72.5,
                      'state': 'WATCH', 'atr14': 3.0, 'close': 108, 'alerts': []}
        self.snapshot = {
            'as_of': '2026-01-05', 'assets': [self.asset],
            'market': {'operational_gate': {'status': 'BLOCKED'}},
            'risk_plan': {'initial_stop_atr_multiple': 2.0, 'take_profit_r_multiple': 2.0},
        }
        self.cfg = {'benchmark': 'SPY', 'backtest': {'round_trip_cost_bps': 10}}
        self.bars = {'SPY': bars(1), 'XLK': bars(.5), 'AAA': bars(2)}

    def tearDown(self):
        self.con.close()

    def test_manual_watchlist_is_persistent_and_does_not_duplicate(self):
        self.assertTrue(add_to_watchlist(self.con, self.snapshot, 'aaa'))
        self.assertFalse(add_to_watchlist(self.con, self.snapshot, 'AAA'))
        rows = render_watchlist(self.con, self.snapshot, [])
        self.assertEqual(rows[0]['ticker'], 'AAA')
        self.assertEqual(rows[0]['position_status'], 'WATCHING')
        self.assertEqual(rows[0]['score'], 72.5)

    def test_manual_execution_uses_actual_price_and_date(self):
        add_to_watchlist(self.con, self.snapshot, 'AAA')
        self.assertTrue(open_watchlist_position(self.con, self.snapshot, 'AAA', 120.0, '2026-01-06'))
        self.assertFalse(open_watchlist_position(self.con, self.snapshot, 'AAA', 120.0, '2026-01-06'))
        journal = update_journal(self.con, self.bars, self.cfg)
        manual = [row for row in journal if row['model'] == 'MANUAL_WATCHLIST'][0]
        self.assertEqual(manual['entry_date'], '2026-01-06')
        self.assertEqual(manual['entry_open'], 120.0)
        self.assertEqual(manual['stop_price'], 114.0)
        self.assertEqual(manual['target_price'], 132.0)
        self.assertIsNotNone(manual['current_return_pct'])
        rows = render_watchlist(self.con, self.snapshot, journal)
        self.assertEqual(rows[0]['entry_date'], '2026-01-06')
        self.assertEqual(rows[0]['position_status'], 'CLOSED')

    def test_manual_execution_rejects_invalid_price_or_future_date(self):
        add_to_watchlist(self.con, self.snapshot, 'AAA')
        with self.assertRaisesRegex(ValueError, 'precio'):
            open_watchlist_position(self.con, self.snapshot, 'AAA', 0, '2026-01-06')
        with self.assertRaisesRegex(ValueError, 'fecha'):
            open_watchlist_position(self.con, self.snapshot, 'AAA', 100, 'bad-date')

    def test_removal_deletes_watchlist_item(self):
        add_to_watchlist(self.con, self.snapshot, 'AAA')
        self.assertTrue(remove_from_watchlist(self.con, 'AAA'))
        self.assertFalse(remove_from_watchlist(self.con, 'AAA'))
        self.assertEqual(render_watchlist(self.con, self.snapshot, []), [])


if __name__ == '__main__':
    unittest.main()
