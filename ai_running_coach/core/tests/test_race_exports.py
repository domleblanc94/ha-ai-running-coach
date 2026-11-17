from datetime import datetime, timezone
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

from race_gpx import parse_gpx
from race_exports import course_fit, course_gpx
from tests.test_race_gpx import gpx
from tests.test_race_planner import response


class RaceExportTests(unittest.TestCase):
    def test_gpx_roundtrip_preserves_course_and_missing_heights(self):
        route = parse_gpx(gpx([(0, None), (.01, 20), (.02, 30)]))
        restored = parse_gpx(course_gpx(route))
        self.assertEqual(restored['route_hash'], route['route_hash'])
        self.assertIsNone(restored['points'][0]['elevation_m'])

    def test_fit_rejects_discontinuous_route_and_effort_only_timing(self):
        broken = parse_gpx(gpx([], [[(0, 10), (.01, 20)], [(.02, 30), (.03, 40)]]))
        with self.assertRaises(ValueError):
            course_fit(broken)
        route = parse_gpx(gpx([(0, 10), (.01, 20), (.02, 30), (.03, 40)]))
        with self.assertRaises(ValueError):
            course_fit(route, strategy=response(route)['strategies'][0])

    @unittest.skipUnless(shutil.which('node') and os.environ.get('COACH_FIT_SDK_MODULE'), 'Official Garmin SDK required for course decoding')
    def test_official_decoder_mechanics_and_strategy_identity(self):
        route = parse_gpx(gpx([(0, None), (.01, 20), (.02, 30), (.03, 10)]))
        authored = response(route, numeric=True)['strategies']
        stamp = datetime(2026, 11, 17, tzinfo=timezone.utc)
        files = [course_fit(route, created_at=stamp)] + [course_fit(route, name='Example ' + strategy['id'], strategy=strategy, created_at=stamp) for strategy in authored]
        script = '''const fs=require('fs'),{pathToFileURL}=require('url');
(async()=>{const sdk=await import(pathToFileURL(process.argv[1]).href);const input=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(JSON.stringify(input.map(s=>{const d=new sdk.Decoder(sdk.Stream.fromByteArray(Array.from(Buffer.from(s,'base64'))));return {valid:d.checkIntegrity(),...d.read()};})));})().catch(e=>{console.error(e);process.exitCode=1});'''
        result = subprocess.run([shutil.which('node'), '-e', script, os.environ['COACH_FIT_SDK_MODULE']], input=json.dumps([base64.b64encode(value).decode() for value in files]), text=True, capture_output=True, check=True)
        decoded = json.loads(result.stdout)
        serials = []
        for item in decoded:
            self.assertTrue(item['valid'])
            self.assertEqual(item['errors'], [])
            messages = item['messages']
            self.assertEqual(messages['fileIdMesgs'][0]['type'], 'course')
            self.assertEqual(messages['courseMesgs'][0]['sport'], 'running')
            self.assertEqual(len(messages['recordMesgs']), len(route['points']))
            self.assertAlmostEqual(messages['lapMesgs'][0]['totalDistance'], route['summary']['distance_m'], places=1)
            self.assertNotIn('altitude', messages['recordMesgs'][0])
            self.assertEqual(messages['recordMesgs'][1]['altitude'], 20)
            distances = [record['distance'] for record in messages['recordMesgs']]
            self.assertEqual(distances, sorted(distances))
            timestamps = [record['timestamp'] for record in messages['recordMesgs']]
            self.assertEqual(timestamps, sorted(timestamps))
            self.assertEqual(len(messages['eventMesgs']), 2)
            serials.append(messages['fileIdMesgs'][0]['serialNumber'])
        self.assertEqual(len(set(serials)), 3)
        self.assertNotIn('speed', decoded[0]['messages']['recordMesgs'][0])
        self.assertNotIn('totalTimerTime', decoded[0]['messages']['lapMesgs'][0])
        duration = sum((section['seconds_low'] + section['seconds_high']) / 2 for section in authored[0]['sections'])
        self.assertEqual(decoded[1]['messages']['lapMesgs'][0]['totalTimerTime'], duration)
        self.assertGreater(decoded[1]['messages']['recordMesgs'][1]['speed'], 0)


if __name__ == '__main__':
    unittest.main()
