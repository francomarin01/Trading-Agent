import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.updater import retain_previous_valid_snapshot, summarize_track_record


class TrackRecordSummaryTests(unittest.TestCase):
    def test_sector_summary_uses_only_valid_sector_benchmarked_returns(self):
        rows=[
            {'ticker':'AAA','sector_etf':'XLK','return_pct':5.0,'benchmark_return_pct':2.0,'excess_return_pct':3.0},
            {'ticker':'BBB','sector_etf':'XLK','return_pct':-1.0,'benchmark_return_pct':1.0,'excess_return_pct':-2.0},
            {'ticker':'CCC','sector_etf':'XLE','return_pct':None,'benchmark_return_pct':None,'excess_return_pct':None},
        ]
        result={x['sector_etf']:x for x in summarize_track_record(rows)}
        self.assertEqual(result['XLK']['included'],2)
        self.assertEqual(result['XLK']['valid_returns'],2)
        self.assertEqual(result['XLK']['win_rate_pct'],50.0)
        self.assertEqual(result['XLK']['excess_return_pct'],0.5)
        self.assertEqual(result['XLE']['valid_returns'],0)
        self.assertIsNone(result['XLE']['average_return_pct'])

    def test_failed_refresh_preserves_previous_valid_snapshot(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            path = Path(temp) / 'dashboard.json'
            path.write_text(json.dumps({'status': 'OK', 'as_of': '2026-10-13', 'assets': [{'ticker': 'AAA'}]}), encoding='utf-8')
            failed = {'status': 'DATA UNAVAILABLE', 'assets': [], 'refresh': {'errors': ['provider down']}}
            with patch('app.updater.SNAPSHOT', path):
                retained = retain_previous_valid_snapshot(failed)
            self.assertEqual(retained['as_of'], '2026-10-13')
            self.assertEqual(retained['assets'][0]['ticker'], 'AAA')
            self.assertTrue(retained['refresh']['retained_previous_snapshot'])

    def test_publish_separates_large_asset_rotation_history(self):
        import app.updater as updater
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as temp:
            root = Path(temp)
            dashboard = root / 'dashboard.json'
            rotation = root / 'asset_rotation.json'
            payload = {'status': 'OK', 'as_of': '2026-10-13', 'assets': [], 'asset_rotation_history': [{'date': '2026-10-13', 'ticker': 'AAA'}]}
            with patch.object(updater, 'SNAPSHOT', dashboard), patch.object(updater, 'ASSET_ROTATION', rotation):
                updater.publish_snapshot(payload)
            self.assertNotIn('asset_rotation_history', json.loads(dashboard.read_text()))
            self.assertEqual(json.loads(rotation.read_text())['rows'][0]['ticker'], 'AAA')
