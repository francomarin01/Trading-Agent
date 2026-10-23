import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from app import storage


class StorageTests(unittest.TestCase):
    def test_upsert_and_load(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(storage,'DATA',Path(temp)), patch.object(storage,'DATABASE',Path(temp)/'db.sqlite3'):
            con=storage.connect(); storage.upsert_bars(con,'AAA',[('2025-01-01',1,2,.5,1.5,100)])
            self.assertEqual(storage.load_bars(con,'AAA')[0]['close'],1.5)
            con.close()
