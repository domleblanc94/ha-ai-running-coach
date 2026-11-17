"""Synthetic elevation responses only: no external service or athlete data."""
import copy
import base64
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET

import race_elevation as elevation
from race_exports import course_fit, course_gpx
from race_gpx import parse_gpx, validate_route
from race_planner import make_race_prompt


NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def route(count=30, *, heights=None, increment=.001, groups=None):
    groups = groups or [[(index * increment, heights(index) if heights else None) for index in range(count)]]
    body = ''.join('<trkseg>' + ''.join('<trkpt lat="0" lon="' + str(lon) + '">' +
                    ('<ele>' + str(height) + '</ele>' if height is not None else '') + '</trkpt>'
                    for lon, height in group) + '</trkseg>' for group in groups)
    return parse_gpx('<gpx><trk><name>Fictional course</name>' + body + '</trk></gpx>')


def constant(locations, timeout):
    return {'elevation': [100.0] * len(locations)}


class RaceElevationTests(unittest.TestCase):
    def test_complete_gpx_is_offline_unchanged_and_does_not_create_cache(self):
        original = route(heights=lambda index: index * 2)
        before = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory)/'cache'
            transport = Mock(side_effect=AssertionError('No request for supplied heights'))
            self.assertEqual(elevation.enrich_missing_elevations(original, cache, transport=transport), original)
            self.assertFalse(cache.exists())
            transport.assert_not_called()
        self.assertEqual(original, before)

    def test_missing_only_fill_validates_provenance_and_complete_cache_is_offline(self):
        original = route(1001, increment=.0001, heights=lambda index: 123 if index % 40 == 0 else None)
        before = copy.deepcopy(original)
        with tempfile.TemporaryDirectory() as directory:
            transport = Mock(side_effect=constant)
            result = elevation.enrich_missing_elevations(original, directory, transport=transport, now=NOW)
            self.assertEqual(original, before)
            self.assertNotEqual(result['route_hash'], original['route_hash'])
            self.assertEqual(result['summary']['missing_elevation_points'], 0)
            self.assertEqual(result['summary']['derived_elevation_points'], original['summary']['missing_elevation_points'])
            self.assertEqual(result['elevation_enrichment']['source_route_hash'], original['route_hash'])
            for old, new in zip(original['points'], result['points']):
                self.assertEqual((old['lat'], old['lon'], old['segment']), (new['lat'], new['lon'], new['segment']))
                if old['elevation_m'] is not None:
                    self.assertEqual(old['elevation_m'], new['elevation_m'])
            self.assertEqual(validate_route(result), result)
            transport.reset_mock(side_effect=True)
            transport.side_effect = AssertionError('Complete cache must avoid HTTP')
            cached = elevation.enrich_missing_elevations(original, directory, transport=transport)
            self.assertEqual(cached, result)
            transport.assert_not_called()
            tampered = copy.deepcopy(result)
            tampered['summary']['distance_m'] = 0
            self.assertEqual(validate_route(tampered), result)

    def test_segment_boundaries_and_null_dem_samples_are_not_interpolated_across(self):
        original = route(groups=[[(i * .0001, None) for i in range(30)],
                                 [(.1 + i * .0001, None) for i in range(30)]])
        def values(locations, timeout):
            return {'elevation': [None if .0008 < point['lon'] < .002 else (100 if point['lon'] < .1 else 800)
                                  for point in locations]}
        with tempfile.TemporaryDirectory() as directory:
            result = elevation.enrich_missing_elevations(original, directory, transport=values, now=NOW)
            self.assertGreater(result['summary']['missing_elevation_points'], 0)
            self.assertEqual(result['summary']['discontinuities'], 1)
            self.assertEqual(result['summary']['distance_m'], original['summary']['distance_m'])
            self.assertTrue(all(point['elevation_m'] == 800 for point in result['points'][30:]))
            self.assertIsNone(result['points'][14]['elevation_m'])
            self.assertIsNone(result['summary']['ascent_m'])
            self.assertEqual(list(Path(directory).iterdir()), [])
            with self.assertRaisesRegex(ValueError, 'continuous'):
                course_fit(result)

    def test_tiny_unknown_spans_do_not_publish_complete_climb_or_section_grade(self):
        large = route(4001, increment=.00001, heights=lambda index: None if index == 2000 else 100 + index * .01)
        self.assertGreaterEqual(large['summary']['elevation_coverage'], .999)
        self.assertEqual(large['summary']['missing_elevation_points'], 1)
        self.assertIsNone(large['summary']['ascent_m'])
        self.assertIsNone(large['summary']['descent_m'])
        self.assertTrue(any('missing' in warning.lower() for warning in large['summary']['warnings']))
        tiny = route(groups=[[(0, 10), (.0049999, 20), (.005, None), (.0050001, 20), (.01, 30)]])
        self.assertEqual(tiny['summary']['elevation_coverage'], 1.0)
        self.assertTrue(all(section['ascent_m'] is None and section['descent_m'] is None
                            and section['net_grade_pct'] is None for section in tiny['summary']['sections']))

    def test_partial_retry_cannot_lose_known_heights_then_fuller_result_succeeds(self):
        original = route(20)
        first = lambda locations, timeout: {'elevation': [100 if i < 8 else None for i in range(len(locations))]}
        losing = lambda locations, timeout: {'elevation': [None if i < 8 else 100 for i in range(len(locations))]}
        with tempfile.TemporaryDirectory() as directory:
            partial = elevation.enrich_missing_elevations(original, directory, transport=first, now=NOW)
            before = copy.deepcopy(partial)
            for transport, code in [(losing, 'elevation_no_improvement'),
                                    (lambda locations, timeout: {'elevation': [None] * len(locations)}, 'elevation_no_data'),
                                    (Mock(side_effect=OSError('private URL must not escape')), 'elevation_unavailable')]:
                with self.subTest(code=code), self.assertRaises(elevation.ElevationError) as failure:
                    elevation.enrich_missing_elevations(original, directory, current_route=partial, transport=transport)
                self.assertEqual(failure.exception.code, code)
                self.assertEqual(partial, before)
                self.assertEqual(list(Path(directory).iterdir()), [])
            full = elevation.enrich_missing_elevations(original, directory, current_route=partial, transport=constant)
            self.assertEqual(full['summary']['missing_elevation_points'], 0)
            self.assertNotEqual(full['route_hash'], partial['route_hash'])
            self.assertEqual(full['elevation_enrichment']['source_route_hash'], original['route_hash'])
            with self.assertRaisesRegex(ValueError, 'same original'):
                elevation.enrich_missing_elevations(route(21), directory, current_route=partial, transport=constant)

    def test_large_route_is_bounded_to_five_batches_and_reports_coarser_sampling(self):
        original = route(12001, increment=.0001)
        transport = Mock(side_effect=constant)
        with tempfile.TemporaryDirectory() as directory:
            result = elevation.enrich_missing_elevations(original, directory, transport=transport, now=NOW)
        self.assertLessEqual(transport.call_count, 5)
        self.assertLessEqual(sum(len(call.args[0]) for call in transport.call_args_list), 500)
        self.assertTrue(all(len(call.args[0]) <= 100 and 0 < call.args[1] <= 10 for call in transport.call_args_list))
        self.assertGreater(result['summary']['elevation_source']['sample_spacing_m'], 90)
        self.assertEqual(result['summary']['missing_elevation_points'], 0)

    def test_fragmentation_and_overall_budget_fail_without_cache_or_retries(self):
        fragmented = route(groups=[[(index * .01, None), (index * .01 + .001, None)] for index in range(251)])
        with tempfile.TemporaryDirectory() as directory:
            transport = Mock(side_effect=constant)
            with self.assertRaises(elevation.ElevationError) as failure:
                elevation.enrich_missing_elevations(fragmented, directory, transport=transport)
            self.assertEqual(failure.exception.code, 'elevation_too_fragmented')
            transport.assert_not_called()
            original = route(20)
            with patch.object(elevation.time, 'monotonic', side_effect=[0, 0, 56]), self.assertRaises(elevation.ElevationError) as failure:
                elevation.enrich_missing_elevations(original, directory, transport=transport)
            self.assertEqual(failure.exception.code, 'elevation_unavailable')
            self.assertEqual(transport.call_count, 1)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_invalid_response_and_late_batch_failure_never_mutate_input_or_cache(self):
        original = route(140)
        before = copy.deepcopy(original)
        bad = [None, {}, {'elevation': []}, {'error': True, 'elevation': [100] * 100},
               {'elevation': [True] * 100}, {'elevation': [float('nan')] * 100},
               {'elevation': ['100'] * 100}, {'elevation': [9001] * 100}]
        with tempfile.TemporaryDirectory() as directory:
            keep = Path(directory)/'unrelated.txt'; keep.write_text('unchanged')
            for payload in bad:
                transport = Mock(return_value=payload)
                with self.subTest(payload=str(payload)[:70]), self.assertRaises(elevation.ElevationError) as failure:
                    elevation.enrich_missing_elevations(original, directory, transport=transport)
                self.assertEqual(failure.exception.code, 'elevation_invalid_response')
                self.assertEqual(transport.call_count, 1)
            late = Mock(side_effect=[{'elevation': [100] * 100}, elevation.ElevationError('elevation_rate_limit')])
            with self.assertRaises(elevation.ElevationError) as failure:
                elevation.enrich_missing_elevations(original, directory, transport=late)
            self.assertEqual(failure.exception.code, 'elevation_rate_limit')
            self.assertEqual(late.call_count, 2)
            self.assertEqual(list(Path(directory).iterdir()), [keep])
            self.assertEqual(keep.read_text(), 'unchanged')
        self.assertEqual(original, before)

    def test_provenance_tampering_rejected_but_fetch_time_does_not_change_identity(self):
        original = route(20)
        with tempfile.TemporaryDirectory() as directory:
            result = elevation.enrich_missing_elevations(original, directory, transport=constant, now=NOW)
        for change in ('range_overlap', 'range_bounds', 'sample_order', 'provider', 'source', 'height', 'bool_version'):
            broken = copy.deepcopy(result)
            meta = broken['elevation_enrichment']
            if change == 'range_overlap': meta['filled_ranges'].append([1, 2])
            if change == 'range_bounds': meta['filled_ranges'][0][1] = 1000
            if change == 'sample_order': meta['samples'][0][0] = 1
            if change == 'provider': meta['provider'] = 'https://private.example.invalid/'
            if change == 'source': meta['source_route_hash'] = '0' * 64
            if change == 'height': broken['points'][1]['elevation_m'] = 101
            if change == 'bool_version': meta['version'] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_route(broken)
        stamp = copy.deepcopy(result)
        stamp['elevation_enrichment']['retrieved_at'] = '2026-11-18T00:00:00+00:00'
        self.assertEqual(validate_route(stamp)['route_hash'], result['route_hash'])

    def test_network_transport_is_fixed_host_no_redirect_and_bounded_body(self):
        def reply(body):
            item = Mock(status=200, headers={})
            item.read1.side_effect = io.BytesIO(body).read1
            item.__enter__ = Mock(return_value=item); item.__exit__ = Mock(return_value=False)
            return item
        valid = reply(b'{"elevation":[100]}')
        opener = Mock(); opener.open.return_value = valid
        with patch.object(elevation.urllib.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(elevation._request([{'lat': 50.1, 'lon': -.2}], 8), {'elevation': [100]})
            request = opener.open.call_args.args[0]
            url = urllib.parse.urlsplit(request.full_url)
            self.assertEqual((url.scheme, url.netloc, url.path), ('https', 'api.open-meteo.com', '/v1/elevation'))
            self.assertEqual(set(urllib.parse.parse_qs(url.query)), {'latitude', 'longitude'})
            self.assertIsInstance(build.call_args.args[0], elevation._NoRedirect)
            self.assertTrue(all(0 < call.args[0] <= 4096 for call in valid.read1.call_args_list))
            for body in (b'x' * (elevation.MAX_RESPONSE_BYTES + 1), b'{"elevation":[NaN]}', b'<html>bad</html>'):
                opener.open.return_value = reply(body)
                with self.assertRaises(elevation.ElevationError) as failure:
                    elevation._request([{'lat': 0, 'lon': 0}], 8)
                self.assertEqual(failure.exception.code, 'elevation_invalid_response')
            opener.open.side_effect = urllib.error.HTTPError('https://private.invalid/?secret=1', 302, 'redirect', {}, io.BytesIO())
            with self.assertRaises(elevation.ElevationError) as failure:
                elevation._request([{'lat': 0, 'lon': 0}], 8)
            self.assertEqual(str(failure.exception), 'elevation_unavailable')
            self.assertIsNone(elevation._NoRedirect().redirect_request(None, None, 302, '', {}, 'http://127.0.0.1'))

            opener.open.side_effect = None
            truncated = reply(b'{"elevation":[100]}')
            truncated.headers = {'Content-Length': '50'}
            opener.open.return_value = truncated
            with self.assertRaises(elevation.ElevationError) as failure:
                elevation._request([{'lat': 0, 'lon': 0}], 8)
            self.assertEqual(failure.exception.code, 'elevation_invalid_response')
            opener.open.return_value = reply(b'{"elevation":[100]}')
            with patch.object(elevation.time, 'monotonic', side_effect=[0, 0, 9]), self.assertRaises(elevation.ElevationError) as failure:
                elevation._request([{'lat': 0, 'lon': 0}], 8)
            self.assertEqual(failure.exception.code, 'elevation_unavailable')

    def test_cache_eviction_and_corruption_do_not_touch_unrelated_files(self):
        original = route(20)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            keep = root/'keep.json'; keep.write_text('private unrelated file')
            for index in range(70):
                (root/(format(index, '064x') + '.json')).write_text('{}')
            result = elevation.enrich_missing_elevations(original, root, transport=constant, now=NOW)
            self.assertEqual(len(list(root.glob('?' * 64 + '.json'))), 64)
            self.assertEqual(keep.read_text(), 'private unrelated file')
            path = root/(elevation._cache_key(original) + '.json')
            path.write_text('{"elevation":"invalid cache"}')
            transport = Mock(side_effect=constant)
            recovered = elevation.enrich_missing_elevations(original, root, transport=transport, now=NOW)
            self.assertGreater(transport.call_count, 0)
            self.assertEqual(recovered['route_hash'], result['route_hash'])

    def test_exports_have_map_source_notes_and_no_fake_navigation_waypoint(self):
        with tempfile.TemporaryDirectory() as directory:
            result = elevation.enrich_missing_elevations(route(20), directory, transport=constant, now=NOW)
        text = course_gpx(result).decode()
        xml = ET.fromstring(text)
        ns = {'g': 'http://www.topografix.com/GPX/1/1'}
        notes = xml.find('g:metadata/g:desc', ns).text
        for phrase in ('Open-Meteo', 'Copernicus', 'CC BY 4.0', 'Changes:', 'surface', '90 m'):
            self.assertIn(phrase, notes)
        self.assertEqual(len(xml.findall('.//g:trkpt', ns)), 20)
        self.assertEqual(len(xml.findall('.//g:wpt', ns)), 0)
        self.assertEqual(len(xml.findall('.//g:ele', ns)), 20)
        self.assertEqual(course_fit(result, created_at=NOW)[8:12], b'.FIT')
        prompt = make_race_prompt({}, result, {}, 'Plan this synthetic course.', 'Synthetic race', None)
        data = json.loads(prompt.split('\nDATA:\n')[1])
        self.assertEqual(data['course']['elevation_source']['provider'], 'Open-Meteo')
        self.assertIn('approximate map data', prompt)
        self.assertNotIn('elevation_enrichment', data['course'])
        self.assertNotIn('samples', data['course']['elevation_source'])
        for private in ('"lat":', '"lon":', '"points":', '"filled_ranges":'):
            self.assertNotIn(private, prompt)

    @unittest.skipUnless(shutil.which('node') and os.environ.get('COACH_FIT_SDK_MODULE'), 'Official Garmin FIT SDK required')
    def test_official_fit_decoder_retains_gpx_and_derived_heights(self):
        original = route(20, heights=lambda index: 123 if index == 0 else None)
        with tempfile.TemporaryDirectory() as directory:
            enriched = elevation.enrich_missing_elevations(original, directory, transport=constant, now=NOW)
        script = '''const fs=require('fs'),{pathToFileURL}=require('url');
(async()=>{const sdk=await import(pathToFileURL(process.argv[1]).href);
const d=new sdk.Decoder(sdk.Stream.fromByteArray(Array.from(Buffer.from(fs.readFileSync(0,'utf8'),'base64'))));
console.log(JSON.stringify({valid:d.checkIntegrity(),...d.read()}));})().catch(e=>{console.error(e);process.exitCode=1});'''
        result = subprocess.run([shutil.which('node'), '-e', script, os.environ['COACH_FIT_SDK_MODULE']],
                                input=base64.b64encode(course_fit(enriched, created_at=NOW)).decode(),
                                text=True, capture_output=True, check=True)
        decoded = json.loads(result.stdout)
        self.assertTrue(decoded['valid'])
        self.assertEqual(decoded['errors'], [])
        self.assertEqual(decoded['messages']['fileIdMesgs'][0]['type'], 'course')
        records = decoded['messages']['recordMesgs']
        self.assertEqual([item['altitude'] for item in records], [123] + [100] * 19)
        self.assertEqual(len(decoded['messages'].get('coursePointMesgs', [])), 0)


if __name__ == '__main__':
    unittest.main()
