"""The offline guard permits asyncio wake-up pairs, never arbitrary connects."""
import socket
import unittest
from unittest.mock import patch
from run_offline_tests import denied, local_socketpair


class OfflineGuardTests(unittest.TestCase):
    def test_local_pair_and_outbound_guards(self):
        with patch.object(socket.socket,'connect',denied), patch.object(socket.socket,'connect_ex',denied), patch.object(socket,'create_connection',denied):
            first,second=local_socketpair()
            try:
                first.settimeout(1);second.settimeout(1)
                first.sendall(b'wakeup')
                self.assertEqual(second.recv(6),b'wakeup')
            finally:first.close();second.close()
            with socket.socket() as probe:
                with self.assertRaisesRegex(RuntimeError,'Network is disabled'):probe.connect(('203.0.113.1',443))
                with self.assertRaisesRegex(RuntimeError,'Network is disabled'):probe.connect_ex(('203.0.113.1',443))
            with self.assertRaisesRegex(RuntimeError,'Network is disabled'):socket.create_connection(('203.0.113.1',443))


if __name__=='__main__':unittest.main()
