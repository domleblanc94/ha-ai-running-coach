from contextlib import closing, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_running_coach.app import diagnostics
from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app.storage import atomic_json


PROFILE = '# Athlete\n**Heart Rate Zones:** Not configured\n**Weekly Availability:** 3 days\n'
PRIVATE = 'private-athlete-note-and-key-must-not-be-logged'


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'
        self.app = Runtime(self.data)
        self.app.save_config({'gemini_api_key': 'synthetic-not-a-real-key', 'source': 'offline'})
        self.app.save_profile(PROFILE)

    def ledger(self, rows):
        path = self.app.workspace / 'ai_coach_master.sqlite'
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS coach_api_requests (
                id INTEGER PRIMARY KEY, started_at TEXT, status TEXT, error TEXT, request_kind TEXT)''')
            conn.executemany('INSERT INTO coach_api_requests VALUES (?, ?, ?, ?, ?)', rows)
        return path

    def test_existing_failure_recovers_only_matching_request_without_writes_or_network(self):
        started = datetime.now(timezone.utc) - timedelta(minutes=4)
        finished = started + timedelta(minutes=1)
        ledger = self.ledger([
            (1, (started - timedelta(seconds=1)).isoformat(), 'failed', 'gemini API HTTP 401: old', 'daily'),
            (2, (started + timedelta(seconds=1)).isoformat(), 'failed', 'gemini API HTTP 429: ' + PRIVATE, 'daily'),
            (3, (finished + timedelta(seconds=1)).isoformat(), 'failed', 'gemini API HTTP 404: later', 'daily'),
        ])
        state = self.app._state()
        state['last_run'] = {'action': 'new_plan', 'status': 'failed', 'code': 'pipeline_failed',
                             'message': 'Coach run did not complete.', 'started_at': started.isoformat(),
                             'finished_at': finished.isoformat()}
        atomic_json(self.app.state_file, state)
        files = [ledger, self.app.state_file, self.app.workspace / 'athlete.md', self.app.workspace / '.env']
        before = {path: path.read_bytes() for path in files}
        log = io.StringIO()
        with patch.object(Runtime, '_command', side_effect=AssertionError('No subprocess permitted')), redirect_stdout(log):
            recovered = Runtime(self.data)
            run = recovered.status()['last_run']
            self.assertEqual(recovered.health()['status'], 'error')
        self.assertEqual(run['code'], 'provider_quota')
        self.assertEqual(run['stage'], 'coaching')
        self.assertEqual(run['request_attempts'], 1)
        self.assertIn('not been published yet', run['message'])
        self.assertIn('[coaching/provider_quota]', log.getvalue())
        self.assertNotIn(PRIVATE, json.dumps(run) + log.getvalue())
        self.assertEqual(before, {path: path.read_bytes() for path in files})

    def test_current_pipeline_and_request_take_precedence_over_later_stderr(self):
        started = datetime.now(timezone.utc)
        stamp = started.isoformat()
        report = self.app.workspace / 'pipeline_status.json'
        old_auth = {'code': 'key_rejected', 'state': 'action_required',
                    'checked_at': (started - timedelta(days=1)).isoformat()}
        atomic_json(report, {'attempted_at': stamp, 'errors': ['Activity sync failed; showing saved data'],
                             'garmin_auth': old_auth})
        code, _ = self.app._failure(started, None, command_failure='workspace_permissions')
        self.assertEqual(code, 'activity_sync_failed')
        old_auth['checked_at'] = stamp
        atomic_json(report, {'attempted_at': stamp, 'errors': ['Activity sync failed; showing saved data'],
                             'garmin_auth': old_auth})
        self.assertEqual(self.app._failure(started, None)[0], 'garmin_auth')
        atomic_json(report, {'attempted_at': stamp, 'errors': [
            'Coach review pending; previous review retained', 'Dashboard generation failed; previous HTML retained']})
        request = (stamp, 'failed', 'gemini API HTTP 400: Daily coaching API request budget exhausted;')
        self.assertEqual(self.app._failure(started, request, command_failure='workspace_permissions')[0], 'provider_request')
        self.assertIsNone(diagnostics.command_failure('ERROR:root:ai_coach.py failed: ' + request[2]))
        for envelope in ('response was not JSON', 'unexpected response envelope', 'text response was missing'):
            with self.subTest(envelope=envelope):
                self.assertEqual(diagnostics.request_failure('failed', 'gemini API: ' + envelope), 'response_invalid')

    def fixture_provider(self, mode):
        # Modify only this test's installed workspace. The pinned source remains
        # untouched. The exact isolated adapter/engine/rendering code still runs.
        module = self.app.workspace / 'model_provider.py'
        module.write_text(module.read_text(encoding='utf-8') + '''

# Synthetic test transport: any accidental real socket use fails closed.
import json as _fixture_json
import socket as _fixture_socket
from pathlib import Path as _FixturePath
from datetime import date as _FixtureDate
def _fixture_no_network(*args, **kwargs):
    raise AssertionError('External network forbidden in diagnostic test')
_fixture_socket.socket.connect = _fixture_no_network
_fixture_socket.create_connection = _fixture_no_network
def _fixture_post(self, path, payload, headers):
    mode = _FixturePath(__file__).with_name('provider_fixture.json').read_text()
    if mode == 'quota':
        raise ProviderError(self.provider, 'request rejected or service unavailable', 429)
    if mode == 'transport':
        raise ProviderError(self.provider, 'transport failure')
    today = _FixtureDate.today().isoformat()
    review = {
        'summary': 'Synthetic review: private-athlete-note-and-key-must-not-be-logged',
        'trajectory': 'insufficient_evidence', 'reviews': [],
        'plan_changes': [{'op': 'add', 'session_id': 'synthetic-easy-run',
            'session': {'id': 'synthetic-easy-run', 'date': today, 'workout_type': 'Easy run',
                        'amount': '20 minutes', 'target': 'Comfortable conversational effort'},
            'reason': 'Synthetic starter run'}],
    }
    return {'candidates': [{'content': {'parts': [{'text': _fixture_json.dumps(review)}]},
                           'finishReason': 'MAX_TOKENS' if mode == 'truncated' else 'STOP'}]}
ProviderClient._post = _fixture_post
''', encoding='utf-8')
        (self.app.workspace / 'provider_fixture.json').write_text(mode, encoding='utf-8')

    def test_actual_isolated_first_plan_succeeds_and_failure_retains_dashboard(self):
        self.fixture_provider('success')
        self.app.save_profile(PROFILE + '**HR Zone Learning:** Enabled\n')
        self.app.enqueue('new_plan')
        log = io.StringIO()
        with redirect_stdout(log):
            self.assertTrue(self.app.tick())
        first = self.app.status()['last_run']
        self.assertEqual(first['status'], 'success', first)
        self.assertEqual(first['request_attempts'], 1)
        dashboard = self.app.dashboard()
        self.assertGreater(len(dashboard), 1000)
        self.assertNotIn(PRIVATE, log.getvalue())
        (self.app.workspace / 'provider_fixture.json').write_text('truncated', encoding='utf-8')
        self.app.enqueue('new_plan')
        with redirect_stdout(log):
            self.assertTrue(self.app.tick())
        failure = self.app.status()['last_run']
        self.assertEqual(failure['code'], 'provider_truncated', failure)
        self.assertEqual(failure['request_attempts'], 1)
        self.assertEqual(self.app.dashboard(), dashboard)
        self.assertIn('remains available', failure['message'])
        self.assertIn('[coaching/provider_truncated]', log.getvalue())
        self.assertNotIn(PRIVATE, json.dumps(failure) + log.getvalue())

    def test_actual_provider_failure_and_budget_exhaustion_do_not_silently_retry(self):
        self.fixture_provider('transport')
        for expected_code, attempts in [('provider_transport', 1), ('provider_transport', 1), ('daily_budget', 0)]:
            self.app.enqueue('new_plan')
            log = io.StringIO()
            with redirect_stdout(log):
                self.assertTrue(self.app.tick())
            last = self.app.status()['last_run']
            self.assertEqual(last['code'], expected_code, last)
            self.assertEqual(last['request_attempts'], attempts)
            self.assertEqual(last['stage'], 'coaching')
            self.assertIsNone(self.app.dashboard())
            self.assertIn('Request attempts: ' + str(attempts), log.getvalue())
        with closing(sqlite3.connect(self.app.workspace / 'ai_coach_master.sqlite')) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM coach_api_requests').fetchone()[0], 2)

    def test_actual_off_day_plan_reports_availability_without_publishing(self):
        self.fixture_provider('success')
        weekdays = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
        tomorrow = weekdays[(datetime.now(timezone.utc).weekday() + 1) % 7]
        self.app.save_profile(PROFILE.replace('3 days', tomorrow))
        profile = self.app.profile()
        self.app.enqueue('new_plan')
        log = io.StringIO()
        with redirect_stdout(log):
            self.assertTrue(self.app.tick())
        last = self.app.status()['last_run']
        self.assertEqual(last['code'], 'availability_conflict', last)
        self.assertEqual(last['stage'], 'coaching')
        self.assertEqual(last['request_attempts'], 1)
        self.assertIn('selected days', last['message'])
        self.assertIsNone(self.app.dashboard())
        self.assertEqual(self.app.profile(), profile)
        self.assertNotIn(PRIVATE, json.dumps(last) + log.getvalue())
        self.assertIn('[coaching/availability_conflict]', log.getvalue())


if __name__ == '__main__':
    unittest.main()
