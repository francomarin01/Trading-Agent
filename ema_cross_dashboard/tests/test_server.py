import unittest

from app import server


class ServerTests(unittest.TestCase):
    def test_refresh_status_is_a_copy(self):
        original = dict(server._refresh_state)
        try:
            server._refresh_state['status'] = 'READY'
            state = server.refresh_status()
            state['status'] = 'MUTATED'
            self.assertEqual(server.refresh_status()['status'], 'READY')
        finally:
            server._refresh_state.clear()
            server._refresh_state.update(original)
