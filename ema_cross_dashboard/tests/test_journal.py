import sqlite3
import unittest

from app.journal import create_journal_schema, register_new_signals, summarize_journal, update_journal


def bars(symbol_scale=1.0, sessions=30):
    output = []
    for index in range(sessions):
        close = 100 + symbol_scale * index
        output.append({'date': f'2026-01-{index + 1:02d}', 'open': close - .25, 'high': close + 1, 'low': close - 1, 'close': close, 'volume': 1000})
    return output


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(':memory:')
        self.con.row_factory = sqlite3.Row
        create_journal_schema(self.con)
        self.cfg = {'benchmark': 'SPY', 'backtest': {'round_trip_cost_bps': 10}}
        self.bars = {'SPY': bars(1), 'XLK': bars(.5), 'AAA': bars(2)}
        self.asset = {'ticker': 'AAA', 'sector_etf': 'XLK', 'date': '2026-01-05', 'score': 72, 'state': 'ARMED', 'atr14': 3, 'eligible_for_new_entry': True, 'selective_setup_candidate': False, 'selective_score_25_30_30_15': 70, 'selective_setup_reason': None}
        self.snapshot = {'assets': [self.asset], 'market': {'operational_gate': {'status': 'FULL'}}}

    def tearDown(self):
        self.con.close()

    def test_registers_once_then_resolves_t_plus_one_and_twenty_day_outcome(self):
        self.assertEqual(register_new_signals(self.con, self.snapshot), 1)
        self.assertEqual(register_new_signals(self.con, self.snapshot), 0)
        rows = update_journal(self.con, self.bars, self.cfg)
        self.assertEqual(len(rows), 1)
        item = rows[0]
        self.assertEqual(item['entry_date'], '2026-01-06')
        self.assertEqual(item['status'], 'CLOSED')
        self.assertIsNotNone(item['return_5d_pct'])
        self.assertIsNotNone(item['return_10d_pct'])
        self.assertIsNotNone(item['return_20d_pct'])
        self.assertIsNotNone(item['excess_spy_20d_pct'])
        self.assertIsNotNone(item['stop_price'])
        self.assertIsNotNone(item['target_price'])
        self.assertEqual(item['risk_status'], 'TARGET HIT')
        summary = summarize_journal(rows)
        self.assertEqual(summary[0]['matured_20d'], 1)

    def test_pending_entry_is_not_imputed_when_next_session_does_not_exist(self):
        self.asset['date'] = '2026-01-30'
        self.assertEqual(register_new_signals(self.con, self.snapshot), 1)
        row = update_journal(self.con, self.bars, self.cfg)[0]
        self.assertEqual(row['status'], 'PENDING_ENTRY')
        self.assertIsNone(row['entry_date'])
        self.assertIsNone(row['current_return_pct'])

    def test_stop_is_detected_from_daily_low_without_optimistic_fill(self):
        self.assertEqual(register_new_signals(self.con, self.snapshot), 1)
        # Signal Jan-05, entry Jan-06; a later daily low reaches the frozen stop.
        self.bars['AAA'][6]['low'] = 1
        row = update_journal(self.con, self.bars, self.cfg)[0]
        self.assertEqual(row['risk_status'], 'STOP HIT')
        self.assertEqual(row['r_multiple_to_date'], -1.0)
