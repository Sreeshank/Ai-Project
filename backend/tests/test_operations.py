import unittest
from unittest.mock import patch

from backend.operations import RequestLimits


class RequestLimitTests(unittest.TestCase):
    def test_limits_expire_and_clients_do_not_share_counters(self):
        limits=RequestLimits()
        with patch('backend.operations.time.monotonic',return_value=100):
            self.assertEqual(limits.check('first','chat',1),0)
            self.assertGreater(limits.check('first','chat',1),0)
            self.assertEqual(limits.check('second','chat',1),0)
        with patch('backend.operations.time.monotonic',return_value=162):
            self.assertEqual(limits.check('first','chat',1),0)
