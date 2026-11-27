"""Private desktop bridge and human-reviewed plan integration."""
import http.client
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_running_coach.app.chat_bridge import ChatBridgeServer, REQUEST_LIMIT, RESPONSE_LIMIT
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

    def test_activity_details_uses_bounded_local_dispatch(self):
        payload = {'activity_id': -42, 'include_streams': True, 'offset': 100, 'limit': 50}
        details = {'activity': {'activity_id': -42}, 'streams': {'status': 'available', 'items': []}}
        envelope = json.dumps({'ok': True, 'value': details})
        with patch.object(self.runtime, '_command', return_value=(0, envelope)) as command:
            self.assertEqual(self.runtime.chat_dispatch('activity_details', payload, remote=True), details)
        self.assertEqual(command.call_args.args, ('chat_dispatch',))
        self.assertEqual(json.loads(command.call_args.kwargs['input_text']),
                         {'action': 'activity_details', 'payload': payload})
        self.assertEqual(command.call_args.kwargs['timeout'], 20)
        with self.assertRaises(ValueError):
            self.runtime.chat_dispatch('activity_details', payload)
        for code in ('not_found', 'invalid_input', 'source_policy', 'unavailable'):
            with self.subTest(code=code):
                envelope = json.dumps({'ok': False, 'code': code, 'message': 'private provider response'})
                with patch.object(self.runtime, '_command', return_value=(0, envelope)):
                    with self.assertRaises(ChatFailure) as caught:
                        self.runtime.chat_dispatch('activity_details', payload, remote=True)
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('private provider response', str(caught.exception))

    def test_review_summary_and_explicit_session_links_use_authenticated_dispatch(self):
        for action in ('session_review', 'save_session_link', 'weekly_summary'):
            with self.subTest(action=action), patch.object(self.runtime, '_command', return_value=(0, '{"ok":true,"value":{}}')) as command:
                self.runtime.chat_dispatch(action, {}, remote=True)
                self.assertEqual(json.loads(command.call_args.kwargs['input_text'])['action'], action)
        with patch.object(self.runtime, '_command') as command:
            for action in ('activity_details_backfill', 'sync', 'run_now'):
                with self.assertRaises(ValueError):
                    self.runtime.chat_dispatch(action, {}, remote=True)
            command.assert_not_called()

    def test_explicit_detail_import_is_bounded_and_never_invokes_coaching(self):
        for payload in ({}, {'activity_id': True}, {'activity_id': 0}, {'activity_id': -1},
                        {'activity_id': '12'}, {'activity_id': 2**63}, {'activity_id': 12, 'sync': True}):
            with self.subTest(payload=payload), patch.object(self.runtime, '_command') as command:
                with self.assertRaises(ValueError):
                    self.runtime.backfill_activity_details(payload)
                command.assert_not_called()
        with self.assertRaises(ValueError):
            self.runtime.backfill_activity_details({'activity_id': 12})
        self.runtime.save_config({'source': 'garmin', 'garmin_api_key': 'synthetic-key'})
        result = {'activity_id': 12, 'status': 'updated', 'stream_rows': 2, 'laps': 1,
                  'activity_hr_zones_status': 'recorded'}
        with patch.object(self.runtime, '_command', return_value=(0, json.dumps({'ok': True, 'value': result}))) as command:
            self.assertEqual(self.runtime.backfill_activity_details({'activity_id': 12}), result)
        self.assertEqual(command.call_args.args, ('activity_details_backfill',))
        self.assertEqual(command.call_args.kwargs['timeout'], 150)
        with patch.object(self.runtime, '_command', return_value=(0, '{"ok":false,"code":"rate_limited","message":"PRIVATE-SECRET"}')):
            with self.assertRaises(RuntimeError) as caught:
                self.runtime.backfill_activity_details({'activity_id': 12})
        self.assertNotIn('PRIVATE-SECRET', str(caught.exception))
        self.assertIn('limiting requests', str(caught.exception))
        with patch.object(self.runtime, '_command', return_value=(0, json.dumps({'ok': True, 'value': {**result, 'activity_id': 13}}))):
            with self.assertRaises(RuntimeError):
                self.runtime.backfill_activity_details({'activity_id': 12})
        state = self.runtime._state()
        state['queued'] = {'action': 'run_now'}
        self.runtime._write_state(state)
        with patch.object(self.runtime, '_command') as command:
            with self.assertRaises(RuntimeError):
                self.runtime.backfill_activity_details({'activity_id': 12})
            command.assert_not_called()

    def test_backfill_runner_locks_and_bounds_official_reads(self):
        import contextlib
        import io
        from types import SimpleNamespace
        from unittest.mock import Mock
        from ai_running_coach.app.core_runner import activity_details_backfill_operation
        class PersonalError(RuntimeError):
            code = 'rate_limited'
        class ConfigurationError(ValueError):
            code = 'account_binding_required'
        client = SimpleNamespace(timeout=120, max_requests=500, close=Mock())
        result = {'activity_id': 12, 'status': 'updated', 'stream_rows': 2, 'laps': 1}
        sync = SimpleNamespace(get_garmin_client=Mock(return_value=client),
                               backfill_activity_details=Mock(return_value=result),
                               record_auth_error=Mock(), AUTH_STATUS_FILE='private-status')
        locks = []
        @contextlib.contextmanager
        def lock(path):
            locks.append(Path(path).name)
            yield
        modules = {'garmin_sync': sync, 'garmin_personal_api': SimpleNamespace(
            GarminConfigurationError=ConfigurationError, GarminPersonalError=PersonalError),
            'cron_daily_update': SimpleNamespace(pipeline_lock=lock)}
        output = io.StringIO()
        with patch.dict(sys.modules, modules), patch('sys.stdin', io.StringIO('{"activity_id":12}')), contextlib.redirect_stdout(output):
            self.assertEqual(activity_details_backfill_operation(self.runtime.workspace), 0)
        self.assertEqual(json.loads(output.getvalue()), {'ok': True, 'value': result})
        self.assertEqual(locks, ['.pipeline.lock', '.garmin_personal_sync.lock'])
        self.assertEqual((client.timeout, client.max_requests), (15, 8))
        client.close.assert_called_once()
        sync.backfill_activity_details.assert_called_once_with(12, client=client)
        sync.backfill_activity_details.side_effect = PersonalError('PRIVATE-SECRET')
        output = io.StringIO()
        with patch.dict(sys.modules, modules), patch('sys.stdin', io.StringIO('{"activity_id":12}')), contextlib.redirect_stdout(output):
            activity_details_backfill_operation(self.runtime.workspace)
        self.assertEqual(json.loads(output.getvalue()), {'ok': False, 'code': 'rate_limited'})
        self.assertNotIn('PRIVATE-SECRET', output.getvalue())
        sync.record_auth_error.assert_called_once()

    def test_detail_import_subprocess_gets_only_bounded_garmin_configuration(self):
        self.runtime.save_config({'source': 'garmin', 'garmin_api_key': 'synthetic-garmin',
                                  'gemini_api_key': 'synthetic-coach'})
        with patch('ai_running_coach.app.runtime.subprocess.Popen') as popen:
            process = popen.return_value.__enter__.return_value
            process.communicate.return_value = ('{}', None)
            process.returncode = 0
            self.runtime._command('activity_details_backfill', input_text='{"activity_id":12}', capture=True)
            env = popen.call_args.kwargs['env']
        self.assertEqual(env['GARMIN_PERSONAL_API_KEY'], 'synthetic-garmin')
        self.assertEqual(env['GARMIN_MAX_REQUESTS_PER_RUN'], '8')
        self.assertEqual(env['GARMIN_HTTP_TIMEOUT_SECONDS'], '15')
        self.assertEqual(env['COACH_PUBLISH_ENABLED'], 'false')
        self.assertNotIn('GEMINI_API_KEY', env)
        self.assertNotIn('COACH_PROVIDER', env)
        self.assertNotIn('COACH_MODELS', env)

    def test_bundled_activity_details_read_saved_data_without_plan_or_profile(self):
        seed = """
import json, sqlite3
from database_manager import ensure_schema
with sqlite3.connect('ai_coach_master.sqlite') as conn:
    ensure_schema(conn)
    conn.execute('INSERT INTO activities(id,source,type,start_date,distance_m,avg_heartrate,raw_splits_json,downsampled_streams_json) VALUES (?,?,?,?,?,?,?,?)',
                 (-42,'local:fixture','Run','2026-11-25',5000,150,
                  json.dumps([{'split':1,'distance':1000,'latitude':'GPS-SECRET'}, {'split':2,'distance':1000}]),
                  json.dumps([{'time':0,'heartrate':140,'token':'PRIVATE-SECRET'}, {'time':30,'heartrate':150,'latlng':['GPS-SECRET']}])) )
"""
        subprocess.check_call([sys.executable, '-B', '-c', seed], cwd=self.runtime.workspace, timeout=20)
        database = self.runtime.workspace / 'ai_coach_master.sqlite'
        before = database.read_bytes()
        self.assertFalse((self.runtime.workspace / 'athlete.md').exists())
        self.assertFalse((self.runtime.workspace / 'training_plan.json').exists())
        result = self.runtime.chat_dispatch('activity_details',
                                            {'activity_id': -42, 'include_streams': True, 'limit': 1}, remote=True)
        self.assertEqual(result['activity']['distance_m'], 5000)
        self.assertEqual(result['splits']['next_offset'], 1)
        self.assertEqual(result['streams']['next_offset'], 1)
        self.assertEqual(result['laps']['status'], 'not_stored')
        page = self.runtime.chat_dispatch('activity_details',
                                          {'activity_id': -42, 'include_streams': True, 'offset': 1, 'limit': 1}, remote=True)
        self.assertEqual(page['streams']['items'][0]['heartrate_bpm'], 150)
        self.assertIsNone(page['streams']['next_offset'])
        self.assertNotIn('GPS-SECRET', json.dumps(result) + json.dumps(page))
        self.assertNotIn('PRIVATE-SECRET', json.dumps(result) + json.dumps(page))
        self.assertEqual(database.read_bytes(), before)
        with self.assertRaises(ChatFailure) as caught:
            self.runtime.chat_dispatch('activity_details', {'activity_id': 999}, remote=True)
        self.assertEqual(caught.exception.code, 'not_found')

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
        details_request = json.dumps({'action': 'activity_details', 'payload': {'activity_id': 42}})
        with patch.object(self.runtime, 'chat_dispatch', return_value={'activity': {'activity_id': 42}}) as dispatch:
            self.assertEqual(request(details_request)[0], 401)
            dispatch.assert_not_called()
            status, value = request(details_request, authorization=token)
        self.assertEqual(status, 200)
        self.assertEqual(value, {'ok': True, 'value': {'activity': {'activity_id': 42}}})
        dispatch.assert_called_once_with('activity_details', {'activity_id': 42}, remote=True)
        for code in ('not_found', 'invalid_input', 'source_policy', 'unavailable'):
            with self.subTest(code=code):
                with patch.object(self.runtime, 'chat_dispatch', side_effect=ChatFailure(code)):
                    status, value = request(details_request, authorization=token)
                self.assertEqual(status, 409)
                self.assertEqual(value['code'], code)
                self.assertFalse(value['ok'])
        with patch.object(self.runtime, 'chat_dispatch', side_effect=Exception('private database path and token')):
            status, value = request(details_request, authorization=token)
        self.assertEqual((status, value), (500, {'ok': False, 'code': 'unavailable'}))
        with patch.object(self.runtime, 'chat_dispatch', return_value={'activity': 'x' * RESPONSE_LIMIT}):
            status, value = request(details_request, authorization=token)
        self.assertEqual((status, value), (500, {'ok': False, 'code': 'unavailable'}))
        self.runtime.configure_chat({'action': 'disable'})
        self.assertEqual(request(authorization=token)[0], 401)


if __name__ == '__main__':
    unittest.main()
