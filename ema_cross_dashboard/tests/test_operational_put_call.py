import csv
import tempfile
import unittest
from pathlib import Path

from app.cboe_put_call import FIELDS, URL
from app.operational_put_call import load_operational_ratios


class OperationalPutCallTests(unittest.TestCase):
    def write_rows(self, rows):
        directory = tempfile.TemporaryDirectory()
        path = Path(directory.name) / 'put_call.csv'
        with path.open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            writer.writerows(rows)
        self.addCleanup(directory.cleanup)
        return path

    def test_cache_reads_dated_verified_observation(self):
        path = self.write_rows([{'Date': '2026-10-12', 'PutCall': '.91', 'Source': URL + '2026-10-12', 'DownloadedAtUTC': '2026-10-14T00:00:00+00:00', 'PageSHA256': 'a' * 64}])
        self.assertEqual(load_operational_ratios(path), {'2026-10-12': .91})

    def test_cache_rejects_silent_wrong_source_or_duplicates(self):
        path = self.write_rows([
            {'Date': '2026-10-12', 'PutCall': '.91', 'Source': 'wrong', 'DownloadedAtUTC': '2026-10-14T00:00:00+00:00', 'PageSHA256': 'a' * 64},
        ])
        with self.assertRaises(ValueError):
            load_operational_ratios(path)
