from datetime import datetime, timezone
import copy
import base64
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app.race_runtime import RaceFailure
from ai_running_coach.app.server import CoachServer
from ai_running_coach.app.storage import atomic_json, file_lock
from tests.test_race_app import course


def route(missing=True, route_hash='a' * 64):
    value = course()
    value['route_hash'] = route_hash
    value['points'] = [{'lat': 51, 'lon': -1, 'elevation_m': None if missing else 100, 'distance_m': 0, 'segment': 0},
                       {'lat': 51.01, 'lon': -1.01, 'elevation_m': 120, 'distance_m': 1300, 'segment': 0}]
    return value


class ElevationRuntimeTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.app = Runtime(Path(directory.name), core_dir=os.environ.get('COACH_TEST_CORE_DIR'))
        state = self.app._state()
        state['last_run'] = {'status': 'success', 'started_at': '2026-11-17T03:00:00+00:00', 'finished_at': '2026-11-17T03:01:00+00:00', 'code': 'complete'}
        state['last_success_at'] = state['last_run']['finished_at']
        state['schedule'] = {'enabled': True, 'time': '03:00', 'timezone': 'UTC', 'last_date': '2026-11-17'}
        self.app._write_state(state)
        self.daily = {key: copy.deepcopy(state[key]) for key in ('last_run', 'last_success_at', 'schedule')}
        self.health = self.app.health()

    def upload(self, value):
        def operation(name, payload, **options):
            if name == 'race_parse':
                with file_lock(self.app.state_lock, blocking=False): pass
                return copy.deepcopy(value)
            self.assertEqual(name, 'race_read')
            return None
        with patch.object(self.app, '_race_call', side_effect=operation):
            return self.app.save_race_course({'gpx': '<gpx><!-- Literal original --> </gpx>', 'filename': 'synthetic.gpx'})

    def assert_daily_unchanged(self):
        state = self.app._state()
        for key, value in self.daily.items(): self.assertEqual(state[key], value)
        self.assertEqual(self.app.health(), self.health)
        self.assertFalse((self.app.workspace / 'ai_coach_master.sqlite').exists())

    def test_import_queues_only_missing_heights_and_retains_original_upload(self):
        status = self.upload(route())
        self.assertTrue(status['course']['has_missing_elevation'])
        self.assertTrue(status['elevation_pending'])
        self.assertEqual(self.app._state()['queued']['action'], 'race_elevation')
        self.assertNotIn('original_gpx', status)
        self.assertEqual(self.app.race_export('original', route_hash='a' * 64), b'<gpx><!-- Literal original --> </gpx>')
        self.assert_daily_unchanged()
        state = self.app._state(); state['queued'] = None; self.app._write_state(state)
        self.upload(route(False, 'b' * 64))
        self.assertIsNone(self.app._state()['queued'])
        with patch.object(self.app, '_race_call', side_effect=RaceFailure('invalid_input')):
            with self.assertRaises(RaceFailure): self.app.save_race_course({'gpx': '<invalid>', 'filename': 'bad.gpx'})
        self.assertEqual(self.app._race_course()['route_hash'], 'b' * 64)

    def test_success_no_state_lock_during_lookup_no_ai_and_no_training_publication(self):
        self.upload(route())
        def lookup(name, payload, **options):
            self.assertEqual(name, 'race_elevation')
            self.assertEqual(payload['route'], route())
            self.assertEqual(payload['current_route'], route())
            with file_lock(self.app.state_lock, blocking=False): pass
            self.assertEqual(self.app.health(), self.health)
            with self.assertRaises(RuntimeError): self.app.enqueue_elevation({'route_hash': 'a' * 64})
            return route(False, 'b' * 64)
        with patch.object(self.app, '_race_call', side_effect=lookup), patch.object(self.app, '_publish') as publish, patch.object(self.app, '_request_details') as requests:
            self.assertTrue(self.app.tick())
        publish.assert_not_called(); requests.assert_not_called()
        stored = self.app._race_envelope()
        self.assertEqual(stored['original'], route())
        self.assertEqual(stored['active']['route_hash'], 'b' * 64)
        self.assertEqual(self.app._state()['race_elevation_last_run']['status'], 'success')
        self.assert_daily_unchanged()

    def test_outage_partial_retry_no_improvement_and_restart_retain_current_course(self):
        self.upload(route())
        before = (self.app.data / 'race-course.json').read_bytes()
        with patch.object(self.app, '_race_call', side_effect=RaceFailure('elevation_unavailable')): self.app.tick()
        self.assertEqual((self.app.data / 'race-course.json').read_bytes(), before)
        self.app.enqueue_elevation({'route_hash': 'a' * 64})
        partial = route(True, 'b' * 64)
        with patch.object(self.app, '_race_call', return_value=partial): self.app.tick()
        self.assertEqual(self.app._state()['race_elevation_last_run']['code'], 'elevation_partial')
        before = (self.app.data / 'race-course.json').read_bytes()
        self.app.enqueue_elevation({'route_hash': 'b' * 64})
        with patch.object(self.app, '_race_call', side_effect=RaceFailure('elevation_no_improvement')): self.app.tick()
        self.assertEqual(self.app._state()['race_elevation_last_run']['status'], 'failed')
        self.assertEqual((self.app.data / 'race-course.json').read_bytes(), before)
        self.app.enqueue_elevation({'route_hash': 'b' * 64})
        self.app._claim(datetime.now(timezone.utc))
        self.assertIsNone(self.app._claim(datetime.now(timezone.utc)))
        self.assertEqual(self.app._state()['race_elevation_last_run']['code'], 'elevation_interrupted')
        self.assert_daily_unchanged()

    def test_legacy_retry_survives_restart_and_stale_result_cannot_replace_course(self):
        atomic_json(self.app.data / 'race-course.json', route())
        self.app.enqueue_elevation({'route_hash': 'a' * 64})
        with patch.object(self.app, '_race_call', return_value=route(False, 'b' * 64)): self.app.tick()
        restarted = Runtime(self.app.data, core_dir=os.environ.get('COACH_TEST_CORE_DIR'))
        self.assertIsNone(restarted._race_envelope()['original_gpx'])
        self.assertEqual(restarted._race_course()['route_hash'], 'b' * 64)
        with self.assertRaises(ValueError): restarted.race_export('original', route_hash='b' * 64)
        atomic_json(self.app.data / 'race-course.json', route())
        self.app.enqueue_elevation({'route_hash': 'a' * 64})
        def changed(*args, **kwargs):
            atomic_json(self.app.data / 'race-course.json', route(False, 'c' * 64))
            return route(False, 'b' * 64)
        with patch.object(self.app, '_race_call', side_effect=changed): self.app.tick()
        self.assertEqual(self.app._race_course()['route_hash'], 'c' * 64)
        self.assertEqual(self.app._state()['race_elevation_last_run']['code'], 'route_changed')
        self.assert_daily_unchanged()

    def test_elevation_subprocess_has_no_provider_keys_or_daily_failure_side_effect(self):
        self.app.save_config({'gemini_api_key': 'private-synthetic-key'})
        self.app.command_failure = 'provider_auth'
        process = MagicMock(); process.__enter__.return_value = process
        process.returncode = 0; process.communicate.return_value = ('{"ok":true,"value":null}', '')
        with patch('ai_running_coach.app.runtime.subprocess.Popen', return_value=process) as launch:
            self.app._race_call('race_elevation', {'route': route()})
        self.assertNotIn('GEMINI_API_KEY', launch.call_args.kwargs['env'])
        self.assertEqual(self.app.command_failure, 'provider_auth')

    def test_actual_core_runner_reuses_seeded_cache_preserves_original_bytes_and_state(self):
        raw = b'\xef\xbb\xbf<gpx><metadata><desc>Original &amp; private</desc></metadata><trk><trkseg><trkpt lat="51" lon="-1"/><trkpt lat="51.01" lon="-1.01"><ele>120</ele></trkpt></trkseg></trk></gpx>'
        self.app.save_race_course({'gpx_base64': base64.b64encode(raw).decode(), 'filename': 'synthetic.gpx'})
        original = self.app._race_course()
        script = '''
import json,socket,sys
from pathlib import Path
workspace=Path(sys.argv[1]);sys.path.insert(0,str(workspace))
def blocked(*args,**kwargs):raise AssertionError('No external requests allowed')
socket.socket.connect=blocked;socket.create_connection=blocked
from race_elevation import enrich_missing_elevations
route=json.load(sys.stdin)
enrich_missing_elevations(route,workspace/'.race-elevation-cache',transport=lambda locations,timeout:{'elevation':[100+i for i in range(len(locations))]})
'''
        subprocess.run([sys.executable, '-I', '-B', '-c', script, str(self.app.workspace)],
                       input=json.dumps(original), check=True, text=True, capture_output=True)
        # If the real isolated runner misses the seeded cache, its transport
        # fails closed rather than contacting Open-Meteo during this test.
        module = self.app.workspace / 'race_elevation.py'
        module.write_text(module.read_text(encoding='utf-8') + '\ndef _request(*args, **kwargs):\n    raise AssertionError("Unexpected external elevation request")\n', encoding='utf-8')
        files = {name: (self.app.workspace / name).read_bytes() for name in ('.env',) if (self.app.workspace / name).exists()}
        self.assertTrue(self.app.tick())
        self.assertEqual(self.app._state()['race_elevation_last_run']['status'], 'success')
        active = self.app._race_course()
        self.assertNotEqual(active['route_hash'], original['route_hash'])
        self.assertEqual(active['summary']['missing_elevation_points'], 0)
        self.assertEqual(active['points'][1]['elevation_m'], 120)
        self.assertEqual(self.app.race_export('original', route_hash=active['route_hash']), raw)
        self.assertIn(b'Copernicus', self.app.race_export('source', route_hash=active['route_hash']))
        self.assertIn(b'Open-Meteo', self.app.race_export('gpx', route_hash=active['route_hash']))
        self.assertEqual(self.app.race_export('fit', route_hash=active['route_hash'])[8:12], b'.FIT')
        self.assertFalse((self.app.workspace / 'training_plan.json').exists())
        for name, value in files.items(): self.assertEqual((self.app.workspace / name).read_bytes(), value)
        self.assert_daily_unchanged()

    def test_geometry_and_downloads_are_hash_bound_and_http_keeps_ingress_csrf(self):
        self.upload(route(False))
        geometry = self.app.race_geometry('a' * 64)
        self.assertEqual(geometry['points'], [[51, -1, 0], [51.01, -1.01, 0]])
        with self.assertRaises(RaceFailure): self.app.race_geometry('b' * 64)
        server = CoachServer(('127.0.0.1', 0), self.app, development=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            try:
                connection.request(method, path, body, headers or {}); response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally: connection.close()
        _, _, headers = request('GET', '/')
        self.assertIn("img-src 'self' data: https://tile.openstreetmap.org", headers['Content-Security-Policy'])
        self.assertEqual(headers['Referrer-Policy'], 'no-referrer')
        self.assertEqual(request('GET', '/api/race/geometry?route_hash=' + 'a' * 64)[0], 200)
        self.assertEqual(request('GET', '/api/race/geometry?route_hash=' + 'a' * 64 + '&url=https://example.com')[0], 409)
        self.assertEqual(request('POST', '/api/race/elevation', '{}', {'Content-Type': 'application/json'})[0], 403)
        self.assertEqual(request('GET', '/exports/race-original.gpx?route_hash=' + 'a' * 64)[1], b'<gpx><!-- Literal original --> </gpx>')
        server.development = False
        self.assertEqual(request('GET', '/api/race/geometry?route_hash=' + 'a' * 64)[0], 403)


@unittest.skipUnless(shutil.which('node'), 'Node is needed for offline map geometry checks')
class MapGeometryTests(unittest.TestCase):
    def test_segments_antimeridian_polar_and_wide_route_handling(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const scope={window:{},localStorage:{getItem:()=>null},byId:()=>({addEventListener(){}})};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);const segments=scope.window.CoachRouteMap.segments;
const input=[[10,179,0],[10,-179,0],[10,-178,1],[10,-177,1]],before=JSON.stringify(input),result=segments(input);
assert.equal(result.lines.length,2);assert.equal(result.lines[0][1][1],181);assert.equal(JSON.stringify(input),before);
assert.throws(()=>segments([[89,0,0],[89,1,0]]),/polar/);
assert.throws(()=>segments([[0,0,0],[0,100,0],[0,-160,0]]),/wide/);
assert.throws(()=>segments([[0,0,0],[0,Infinity,0]]),/invalid/);
console.log('Map geometry passed');
'''
        path = Path(__file__).resolve().parents[1] / 'ai_running_coach/app/web/race-map.js'
        subprocess.run([shutil.which('node'), '-e', script, str(path)], check=True, capture_output=True, text=True)


if __name__ == '__main__': unittest.main()
