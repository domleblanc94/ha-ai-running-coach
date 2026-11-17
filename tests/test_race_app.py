from datetime import datetime, timezone
import base64
import copy
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app import diagnostics
from ai_running_coach.app.race_runtime import RaceFailure, GPX_LIMIT
from ai_running_coach.app.server import CoachServer, profile_fields, profile_markdown
from ai_running_coach.app.storage import atomic_json


def course():
    return {'schema_version': 1, 'route_hash': 'a' * 64, 'name': '<script>Fictional course</script>', 'sport': 'running',
            'points': [{'lat': 51, 'lon': -1, 'elevation_m': None, 'distance_m': 0, 'segment': 0}],
            'summary': {'distance_m': 10000, 'ascent_m': None, 'descent_m': None, 'elevation_coverage': 0,
                        'point_count': 2, 'segment_count': 1, 'discontinuities': 0, 'max_point_gap_m': 100,
                        'warnings': ['Elevation unknown'], 'sections': []},
            'preview': [{'lat': 51, 'lon': -1, 'elevation_m': None, 'distance_m': 0, 'segment': 0}]}


class RaceRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = Runtime(Path(directory.name))
        self.app.save_config({'gemini_api_key': 'synthetic-key'})
        self.app.save_profile('**Athlete Name:** Example\n**Heart Rate Zones:** Not configured\n')
        atomic_json(self.app.data / 'race-course.json', course())
        self.payload = {'route_hash': 'a' * 64, 'question': 'Keep the start comfortable.', 'race_name': 'Fictional race', 'race_date': '2027-05-16'}

    def daily_state(self):
        state = self.app._state()
        state['last_run'] = {'status': 'success', 'finished_at': '2026-11-17T03:00:00+00:00', 'code': 'complete'}
        state['last_success_at'] = '2026-11-17T03:00:00+00:00'
        state['schedule'] = {'enabled': True, 'time': '03:00', 'timezone': 'UTC', 'last_date': '2026-11-17'}
        self.app._write_state(state)
        (self.app.public / 'dashboard.html').write_bytes(b'last good private dashboard')
        (self.app.workspace / 'training_plan.json').write_bytes(b'{"sessions": []}')
        return copy.deepcopy({key: state[key] for key in ('last_run', 'last_success_at', 'schedule')})

    def test_profile_review_detail_default_roundtrip_validation_and_preservation(self):
        original = '**Athlete Name:** Example\n**Heart Rate Zones:** Not configured\n**Review Detail:** detailed\n**Coaching Style:** Explain the reasons.\n'
        self.assertEqual(profile_fields('')['review_detail'], 'standard')
        self.assertEqual(profile_fields(original)['review_detail'], 'detailed')
        updated = profile_markdown({'name': 'Changed'}, original)
        self.assertEqual(updated.count('**Review Detail:** detailed'), 1)
        self.assertIn('Explain the reasons.', updated)
        self.assertEqual(profile_fields(profile_markdown({'name': 'Changed', 'review_detail': 'concise'}, updated))['review_detail'], 'concise')
        with self.assertRaises(ValueError):
            profile_markdown({'name': 'Changed', 'review_detail': 'unlimited'})
        self.assertEqual(diagnostics.request_failure('invalid', 'Incomplete workout instructions: private provider detail'), 'workout_details')
        self.assertEqual(diagnostics.failure_stage('workout_details'), 'coaching')
        self.assertNotIn('private provider detail', diagnostics.MESSAGES['workout_details'])
        self.assertIsNone(diagnostics.request_failure('failed', 'Incomplete workout instructions: example'))

    def test_upload_is_private_atomic_no_model_and_status_omits_full_points(self):
        before = self.app._state()
        with patch.object(self.app, '_race_call', side_effect=lambda action, _payload, **_kwargs: course() if action == 'race_parse' else None) as call:
            status = self.app.save_race_course({'gpx': '<gpx/>', 'filename': '../example.gpx'})
        self.assertEqual([c.args[0] for c in call.call_args_list], ['race_parse', 'race_read'])
        self.assertNotIn('points', status['course'])
        self.assertIn('preview', status['course'])
        self.assertEqual(self.app._state()['schedule'], before['schedule'])
        self.assertFalse((self.app.workspace / 'ai_coach_master.sqlite').exists())
        self.assertEqual(json.loads((self.app.data / 'race-course.json').read_bytes())['points'], course()['points'])
        with patch.object(self.app, '_race_call', side_effect=RaceFailure('invalid_input')):
            with self.assertRaises(RaceFailure):
                self.app.save_race_course({'gpx': '<invalid>', 'filename': 'bad.gpx'})
        self.assertEqual(json.loads((self.app.data / 'race-course.json').read_bytes())['route_hash'], 'a' * 64)
        with self.assertRaises(ValueError):
            self.app.save_race_course({'gpx': 'a' * (GPX_LIMIT + 1), 'filename': 'large.gpx'})

    def test_queued_race_success_preserves_training_schedule_and_published_data(self):
        before = self.daily_state()
        self.app.enqueue_race(self.payload)
        with self.assertRaises(RuntimeError):
            self.app.save_race_course({'gpx': '<gpx/>', 'filename': 'other.gpx'})
        with self.assertRaises(RuntimeError):
            self.app.enqueue('run_now')
        with patch.object(self.app, '_race_call', return_value={'route_hash': 'a' * 64, 'cached': False}) as review, patch.object(self.app, '_request_details', return_value=(None, 1)), patch.object(self.app, '_publish') as publication:
            self.assertTrue(self.app.tick())
        self.assertEqual(review.call_count, 1)
        self.assertEqual(review.call_args.args[0], 'race_review')
        publication.assert_not_called()
        after = self.app._state()
        for key, value in before.items(): self.assertEqual(after[key], value)
        self.assertIsNone(after['queued']); self.assertIsNone(after['running'])
        self.assertEqual(after['race_last_run']['status'], 'success')
        self.assertEqual(after['race_last_run']['request_attempts'], 1)
        self.assertEqual(self.app.dashboard(), b'last good private dashboard')
        self.assertEqual((self.app.workspace / 'training_plan.json').read_bytes(), b'{"sessions": []}')

    def test_failure_and_restart_do_not_overwrite_daily_status_or_retry_race(self):
        before = self.daily_state()
        self.app.enqueue_race(self.payload)
        with patch.object(self.app, '_race_call', side_effect=RaceFailure('daily_budget')), patch.object(self.app, '_request_details', return_value=(None, 0)):
            self.app.tick()
        self.assertEqual(self.app._state()['race_last_run']['code'], 'daily_budget')
        for key, value in before.items(): self.assertEqual(self.app._state()[key], value)
        self.app.enqueue_race(self.payload)
        self.app._claim(datetime(2026, 11, 17, 12, tzinfo=timezone.utc))
        with patch.object(self.app, '_race_call') as command:
            self.assertIsNone(self.app._claim(datetime(2026, 11, 17, 12, 1, tzinfo=timezone.utc)))
        command.assert_not_called()
        current = self.app._state()
        self.assertEqual(current['race_last_run']['status'], 'interrupted')
        self.assertIsNone(current['running']); self.assertIsNone(current['queued'])
        for key, value in before.items(): self.assertEqual(current[key], value)

    def test_changed_course_is_rejected_before_model_and_timezone_is_explicit(self):
        self.app.enqueue_race(self.payload)
        changed = course(); changed['route_hash'] = 'b' * 64
        atomic_json(self.app.data / 'race-course.json', changed)
        with patch.object(self.app, '_race_call') as call:
            self.app.tick()
        call.assert_not_called()
        self.assertEqual(self.app._state()['race_last_run']['code'], 'invalid_input')
        atomic_json(self.app.data / 'race-course.json', course())
        state = self.app._state(); state['schedule']['timezone'] = 'Pacific/Auckland'; self.app._write_state(state)
        self.app.enqueue_race(self.payload)
        with patch('ai_running_coach.app.runtime.utc_now', return_value=datetime(2026, 11, 17, 13, tzinfo=timezone.utc)), patch.object(self.app, '_race_call', return_value={'route_hash': 'a' * 64, 'cached': True}) as call:
            self.app.tick()
        self.assertEqual(call.call_args.args[1]['today'], '2026-11-18')
        self.assertEqual(call.call_args.kwargs['timezone_name'], 'Pacific/Auckland')
        self.assertTrue(self.app._state()['race_last_run']['cached'])

    def test_readonly_subprocess_does_not_load_keys_or_clobber_worker_error(self):
        process = MagicMock(); process.__enter__.return_value = process
        process.returncode = 0; process.communicate.return_value = ('{"ok":true,"value":null}', '')
        self.app.command_failure = 'provider_auth'
        with patch('ai_running_coach.app.runtime.subprocess.Popen', return_value=process) as launch:
            self.assertIsNone(self.app._race_call('race_read', {'route_hash': 'a' * 64}))
        self.assertNotIn('GEMINI_API_KEY', launch.call_args.kwargs['env'])
        self.assertEqual(self.app.command_failure, 'provider_auth')
        process.returncode = 1
        with patch('ai_running_coach.app.runtime.subprocess.Popen', return_value=process):
            with self.assertRaises(RaceFailure): self.app._race_call('race_read', {'route_hash': 'a' * 64})
        self.assertEqual(self.app.command_failure, 'provider_auth')

    def test_saved_strategy_export_is_route_bound_and_no_model_called(self):
        strategy = {'id': 'conservative', 'finish_seconds': {'low': 3000, 'high': 3600}, 'sections': []}
        def operation(name, payload, **_kwargs):
            if name == 'race_read': return {'route_hash': 'a' * 64, 'strategies': [strategy], 'request_id': 7}
            self.assertEqual(name, 'race_export'); self.assertEqual(payload['strategy'], strategy)
            return base64.b64encode(b'FIT bytes').decode()
        with patch.object(self.app, '_race_call', side_effect=operation) as call:
            self.assertEqual(self.app.race_export('fit', 'conservative', route_hash='a' * 64, request_id=7), b'FIT bytes')
        self.assertEqual([item.args[0] for item in call.call_args_list], ['race_read', 'race_export'])
        with patch.object(self.app, '_race_call', return_value={'route_hash': 'b' * 64}):
            with self.assertRaises(ValueError): self.app.race_export('fit', 'conservative', route_hash='a' * 64, request_id=7)
        with patch.object(self.app, '_race_call') as call:
            with self.assertRaisesRegex(ValueError, 'course changed'): self.app.race_export('fit', route_hash='b' * 64)
            call.assert_not_called()
        with patch.object(self.app, '_race_call', return_value={'route_hash': 'a' * 64, 'request_id': 8}) as call:
            with self.assertRaisesRegex(ValueError, 'strategies changed'): self.app.race_export('fit', 'conservative', route_hash='a' * 64, request_id=7)
            self.assertEqual([item.args[0] for item in call.call_args_list], ['race_read'])


class RaceRouteTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.app = Runtime(Path(directory.name))
        self.server = CoachServer(('127.0.0.1', 0), self.app, development=True)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)

    def request(self, method, path, value=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        try:
            connection.request(method, path, value, headers or {})
            response = connection.getresponse()
            return response.status, response.read(), dict(response.getheaders())
        finally: connection.close()

    def test_race_routes_keep_ingress_csrf_and_tight_nonupload_limits(self):
        _, body, headers = self.request('GET', '/api/status')
        session = {'Cookie': headers['Set-Cookie'].split(';')[0], 'X-Coach-CSRF': json.loads(body)['csrf'], 'Content-Type': 'application/json'}
        with patch.object(self.app, 'save_race_course', return_value={'course': {'name': 'Safe'}}) as save:
            self.assertEqual(self.request('POST', '/api/race/course', '{}', {'Content-Type': 'application/json'})[0], 403)
            save.assert_not_called()
            payload = json.dumps({'filename': 'large-track.gpx', 'gpx': 'x' * 18000})
            self.assertEqual(self.request('POST', '/api/race/course', payload, session)[0], 200)
            self.assertEqual(self.request('POST', '/api/race/action', payload, session)[0], 413)
        for path in ('/race-course.json', '/race_plans', '/workspace/race-course.json'):
            self.assertEqual(self.request('GET', path)[0], 404)
        self.server.development = False
        self.assertEqual(self.request('GET', '/api/race')[0], 403)


if __name__ == '__main__': unittest.main()
