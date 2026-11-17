"""Full wrapper/core race flow against a loopback-only fake provider."""
import copy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest

from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app.storage import atomic_write


GPX = '<gpx version="1.1"><trk><name>Fictional &amp; private</name><trkseg>' + ''.join(
    f'<trkpt lat="51" lon="{-1 + index / 100}"><ele>{100 + (index % 3) * 15}</ele></trkpt>' for index in range(9)
) + '</trkseg></trk></gpx>'


def response(course):
    sections = [{'section_id': row['id'], 'seconds_low': None, 'seconds_high': None,
                 'effort': 'Comfortable conversational effort', 'guidance': 'Control the climb and adjust to conditions.'}
                for row in course['summary']['sections']]
    checkpoint = next(row['start_m'] for row in course['summary']['sections'] if row['start_m'] >= course['summary']['distance_m'] / 2)
    return {'summary': 'Illustrative coaching: the supplied profile has no recorded running evidence, so use effort and reassess at halfway.',
            'fitness': {'confidence': 'low', 'evidence': ['No imported running activities are available.'], 'limitations': ['Current race fitness and surface conditions are unknown.']},
            'strategies': [
                {'id': 'conservative', 'title': 'Controlled finish', 'rationale': 'Preserve capacity for the final sections.',
                 'conditions': ['Reduce effort if it rises unexpectedly.'], 'pickup_after_m': None, 'sections': sections},
                {'id': 'ambitious', 'title': 'Conditional later progression', 'rationale': 'Only increase effort when the first half feels sustainable.',
                 'conditions': ['At the checkpoint progress only if comfortable; otherwise retain the conservative approach.'],
                 'pickup_after_m': checkpoint, 'sections': copy.deepcopy(sections)},
            ]}


class ProviderHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass

    def do_POST(self):
        self.server.calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        value = json.dumps({'choices': [{'message': {'content': json.dumps(self.server.response)}, 'finish_reason': 'stop'}],
                            'usage': {'prompt_tokens': 100, 'completion_tokens': 200}}).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(value)))
        self.end_headers(); self.wfile.write(value)


class RaceIntegrationTests(unittest.TestCase):
    def test_profile_only_upload_review_cache_invalid_retry_and_shared_budget(self):
        with tempfile.TemporaryDirectory() as temporary:
            app = Runtime(Path(temporary), core_dir=os.environ.get('COACH_TEST_CORE_DIR'))
            fake = ThreadingHTTPServer(('127.0.0.1', 0), ProviderHandler)
            fake.calls = []; fake.response = {}
            threading.Thread(target=fake.serve_forever, daemon=True).start()
            self.addCleanup(fake.server_close); self.addCleanup(fake.shutdown)
            app.save_config({'provider': 'openai', 'model': 'synthetic-test-model', 'openai_api_key': 'synthetic-local-only-key', 'daily_request_limit': '2'})
            env = app._env(); env['OPENAI_BASE_URL'] = f'http://127.0.0.1:{fake.server_port}/v1'
            atomic_write(app.workspace / '.env', ''.join(f'{key}={json.dumps(value)}\n' for key, value in sorted(env.items())).encode())
            app.save_profile('**Athlete Name:** Synthetic runner\n**Heart Rate Zones:** Not configured\n**Review Detail:** detailed\n')
            self.assertFalse((app.workspace / 'ai_coach_master.sqlite').exists())
            uploaded = app.save_race_course({'gpx': GPX, 'filename': 'synthetic.gpx'})
            self.assertIsNone(uploaded['plan'])
            self.assertFalse((app.workspace / 'ai_coach_master.sqlite').exists())
            self.assertEqual(len(fake.calls), 0)
            self.assertNotIn('points', uploaded['course'])
            course = app._race_course(); fake.response = response(course)
            request = {'route_hash': course['route_hash'], 'race_name': 'Fictional race', 'race_date': '', 'question': 'Please explain a careful start.'}
            before = {name: (app.workspace / name).read_bytes() for name in ('.env', 'athlete.md')}
            schedule = app._state()['schedule'].copy()
            app.enqueue_race(request); self.assertTrue(app.tick())
            self.assertEqual(app._state()['race_last_run']['status'], 'success')
            self.assertEqual(len(fake.calls), 1)
            self.assertEqual(app.status()['request_budget']['used'], 1)
            plan = app.race_status()['plan']
            self.assertEqual(plan['fitness']['confidence'], 'low')
            self.assertIsNone(plan['strategies'][0]['finish_seconds'])
            self.assertEqual(plan['route_hash'], course['route_hash'])
            self.assertNotIn('"lat":', json.dumps(fake.calls[0]))
            self.assertNotIn('"lon":', json.dumps(fake.calls[0]))
            self.assertFalse((app.workspace / 'training_plan.json').exists()); self.assertIsNone(app.dashboard())
            self.assertIsNone(app._state()['last_run']); self.assertIsNone(app._state()['last_success_at'])
            self.assertEqual(app._state()['schedule'], schedule)
            self.assertEqual(before, {name: (app.workspace / name).read_bytes() for name in before})
            fit = app.race_export('fit', route_hash=course['route_hash'])
            self.assertEqual(fit[8:12], b'.FIT')
            self.assertIn(b'<gpx', app.race_export('gpx', route_hash=course['route_hash']))
            self.assertEqual(len(fake.calls), 1)
            app.enqueue_race(request); app.tick()
            self.assertEqual(len(fake.calls), 1)
            self.assertTrue(app._state()['race_last_run']['cached'])
            self.assertEqual(app._state()['race_last_run']['request_attempts'], 0)
            fake.response = {'bad': 'Not a race plan'}
            app.enqueue_race({**request, 'question': 'Another question'}); app.tick()
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(app._state()['race_last_run']['code'], 'response_invalid')
            self.assertEqual(app.race_status()['plan']['request_id'], plan['request_id'])
            app.enqueue_race({**request, 'question': 'A third new question'}); app.tick()
            self.assertEqual(len(fake.calls), 2)
            self.assertEqual(app._state()['race_last_run']['code'], 'daily_budget')
            self.assertEqual(app.status()['request_budget']['used'], 2)
            self.assertIsNone(app._state()['last_run'])
            self.assertEqual(list(app.workspace.rglob('*.pyc')), [])


if __name__ == '__main__': unittest.main()
