"""Private desktop bridge and human-reviewed plan integration."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_running_coach.app.chat_bridge import ChatBridgeServer, REQUEST_LIMIT
from ai_running_coach.app.chat_runtime import ChatFailure
from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app.storage import atomic_write


class ChatTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.runtime = Runtime(Path(directory.name))

    def test_token_default_off_rotate_revoke_and_no_secret_status(self):
        self.assertFalse(self.runtime.status()['chat_bridge']['enabled'])
        self.assertFalse(self.runtime.chat_authorized('Bearer ' + 'x' * 43))
        first = self.runtime.configure_chat({'action': 'enable'})['token']
        self.assertTrue(self.runtime.chat_authorized('Bearer ' + first))
        self.assertNotIn(first, self.runtime.state_file.read_text())
        self.assertNotIn(first, json.dumps(self.runtime.status()))
        second = self.runtime.configure_chat({'action': 'rotate'})['token']
        self.assertFalse(self.runtime.chat_authorized('Bearer ' + first))
        self.assertTrue(self.runtime.chat_authorized('Bearer ' + second))
        self.runtime.configure_chat({'action': 'disable'})
        self.assertFalse(self.runtime.chat_authorized('Bearer ' + second))
        with self.assertRaises(ValueError):
            self.runtime.configure_chat([])

    def test_local_review_only_and_subprocess_result_validation(self):
        with patch.object(self.runtime, '_command') as command:
            for action in ('apply_proposal', 'reject_proposal', 'get_proposal', 'list_proposals'):
                with self.assertRaises(ValueError):
                    self.runtime.chat_dispatch(action, {}, remote=True)
            command.assert_not_called()
        for value in (None, [], 'bad'):
            with patch.object(self.runtime, '_command', return_value=(0, json.dumps({'ok': True, 'value': value}))):
                with self.assertRaises(ChatFailure):
                    self.runtime.chat_dispatch('list_proposals', {})
        self.runtime.set_schedule(False, '03:00', 'Pacific/Auckland')
        with patch.object(self.runtime, '_command', return_value=(0, '{"ok":true,"value":{}}')) as command:
            self.runtime.chat_dispatch('list_proposals', {})
        self.assertEqual(command.call_args.kwargs['timezone_name'], 'Pacific/Auckland')

    def test_applied_plan_result_survives_dashboard_render_or_publish_failure(self):
        atomic_write(self.runtime.public / 'dashboard.html', b'last good page')
        with patch.object(self.runtime, '_chat_call', return_value={'status': 'applied', 'dashboard_refreshed': False}):
            result = self.runtime.chat_dispatch('apply_proposal', {})
        self.assertEqual(result['status'], 'applied')
        self.assertFalse(result['dashboard_published'])
        self.assertEqual(self.runtime.dashboard(), b'last good page')
        atomic_write(self.runtime.workspace / 'dashboard.html', b'updated page')
        with patch.object(self.runtime, '_chat_call', return_value={'status': 'applied', 'dashboard_refreshed': True}):
            result = self.runtime.chat_dispatch('apply_proposal', {})
        self.assertTrue(result['dashboard_published'])
        self.assertEqual(self.runtime.dashboard(), b'updated page')
        self.assertTrue(self.runtime.status()['dashboard_revision'])

    def test_advanced_settings_save_has_no_ai_and_preserves_daily_cap(self):
        self.assertEqual(self.runtime.status()['config']['planning_mode'], 'economical')
        with patch.object(self.runtime, '_command') as command:
            status = self.runtime.save_config({'planning_mode': 'advanced'})
            command.assert_not_called()
        limits = status['config']['planning_limits']
        self.assertEqual(limits['max_output_tokens'], 16384)
        self.assertEqual(limits['max_input_chars'], 120000)
        self.assertEqual(limits['planning_days'], 28)
        self.assertEqual(status['config']['daily_request_limit'], 2)
        with self.assertRaises(ValueError):
            self.runtime.save_config({'planning_mode': 'unbounded'})
        for mode, timeout in [('economical', 150), ('advanced', 225)]:
            self.runtime.save_config({'planning_mode': mode})
            with patch.object(self.runtime, '_command', return_value=(0, '{"ok":true,"value":{}}')) as command:
                self.runtime._race_call('race_review', {})
            self.assertEqual(command.call_args.kwargs['timeout'], timeout)

    def test_bridge_auth_origin_remote_allowlist_and_request_limits(self):
        bridge = ChatBridgeServer(('127.0.0.1', 0), self.runtime)
        self.addCleanup(bridge.server_close)
        thread = threading.Thread(target=bridge.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(bridge.shutdown)
        def request(body='{}', *, authorization=None, path='/coach-chat', extra=None):
            connection = http.client.HTTPConnection('127.0.0.1', bridge.server_port, timeout=5)
            headers = {'Content-Type': 'application/json', **(extra or {})}
            if authorization:
                headers['Authorization'] = 'Bearer ' + authorization
            try:
                connection.request('POST', path, body, headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()
        self.assertEqual(request()[0], 401)
        token = self.runtime.configure_chat({'action': 'enable'})['token']
        self.assertEqual(request(authorization=token, extra={'Origin': 'https://example.test'})[0], 404)
        self.assertEqual(request(authorization=token, path='/coach-chat?token=' + token)[0], 404)
        self.assertEqual(request('x' * (REQUEST_LIMIT + 1), authorization=token)[0], 413)
        self.assertEqual(request('{"action":"apply_proposal","payload":{}}', authorization=token)[0], 400)
        with patch.object(self.runtime, 'chat_dispatch', return_value={'shared': 'context'}) as dispatch:
            status, value = request('{"action":"context","payload":{}}', authorization=token)
        self.assertEqual(status, 200)
        self.assertEqual(value, {'ok': True, 'value': {'shared': 'context'}})
        dispatch.assert_called_once_with('context', {}, remote=True)
        self.runtime.configure_chat({'action': 'disable'})
        self.assertEqual(request(authorization=token)[0], 401)


if __name__ == '__main__':
    unittest.main()
