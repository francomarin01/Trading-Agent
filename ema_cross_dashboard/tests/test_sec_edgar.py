import gzip
import json
import unittest

from app.sec_edgar import _decode_response, company_fundamentals


class SecEdgarTests(unittest.TestCase):
    def test_decodes_gzip_json(self):
        raw = gzip.compress(json.dumps({'ok': True}).encode('utf-8'))
        self.assertEqual(_decode_response(raw), '{"ok": true}')

    def test_company_fundamentals_uses_filing_date_not_access_date(self):
        payload = {
            'facts': {
                'us-gaap': {
                    'Revenues': {'units': {'USD': [
                        {'val': 100, 'form': '10-Q', 'filed': '2026-05-01', 'start': '2026-01-01', 'end': '2026-03-31'},
                        {'val': 90, 'form': '10-Q', 'filed': '2025-05-01', 'start': '2025-01-01', 'end': '2025-03-31'},
                    ]}},
                    'NetIncomeLoss': {'units': {'USD': [
                        {'val': 10, 'form': '10-Q', 'filed': '2026-05-01', 'start': '2026-01-01', 'end': '2026-03-31'},
                    ]}},
                    'NetCashProvidedByUsedInOperatingActivities': {'units': {'USD': [
                        {'val': 30, 'form': '10-Q', 'filed': '2026-05-01', 'start': '2026-01-01', 'end': '2026-03-31'},
                    ]}},
                    'PaymentsToAcquirePropertyPlantAndEquipment': {'units': {'USD': [
                        {'val': 7, 'form': '10-Q', 'filed': '2026-05-01', 'start': '2026-01-01', 'end': '2026-03-31'},
                    ]}},
                }
            }
        }
        result = company_fundamentals('TEST', 1234, payload)
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['filed_at'], '2026-05-01')
        self.assertEqual(result['revenue'], 100.0)
        self.assertEqual(result['free_cash_flow'], 23.0)
        self.assertIn('0000001234', result['source_url'])

    def test_no_xbrl_facts_is_explicitly_unavailable(self):
        result = company_fundamentals('TEST', 1234, {'facts': {}})
        self.assertEqual(result['status'], 'DATA UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
