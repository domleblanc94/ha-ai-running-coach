import copy
import unittest

from race_gpx import MAX_GPX_BYTES, parse_gpx, validate_route


def gpx(points, groups=None):
    groups = groups or [points]
    return '<gpx xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Fictional test route</name>' + ''.join('<trkseg>' + ''.join(
        '<trkpt lat="0" lon="' + str(longitude) + '">' + ('<ele>' + str(elevation) + '</ele>' if elevation is not None else '') + '</trkpt>'
        for longitude, elevation in group) + '</trkseg>' for group in groups) + '</trk></gpx>'


class RaceGpxTests(unittest.TestCase):
    def test_distance_elevation_roundtrip_and_unknown_are_not_flat(self):
        route = parse_gpx(gpx([(0, 0), (.01, 100), (.02, 0)]))
        self.assertAlmostEqual(route['summary']['distance_m'], 2223.9, delta=1)
        self.assertAlmostEqual(route['summary']['ascent_m'], 100, delta=8)
        self.assertAlmostEqual(route['summary']['descent_m'], 100, delta=8)
        self.assertEqual(validate_route(route)['route_hash'], route['route_hash'])
        unknown = parse_gpx(gpx([(0, None), (.01, None), (.02, 12)]))
        self.assertIsNone(unknown['summary']['ascent_m'])
        self.assertIsNone(unknown['summary']['descent_m'])
        self.assertTrue(any('missing' in warning for warning in unknown['summary']['warnings']))
        self.assertTrue(all(section['net_grade_pct'] is None for section in unknown['summary']['sections']))

    def test_dense_small_jitter_does_not_become_hundreds_of_metres_of_climb(self):
        noisy = parse_gpx(gpx([(index * .00001, (index % 3 - 1) * 2.5) for index in range(2001)]))
        self.assertLess(noisy['summary']['ascent_m'], 20)
        self.assertLess(noisy['summary']['descent_m'], 20)
        hill = parse_gpx(gpx([(index * .00001, index * .1) for index in range(2001)]))
        self.assertAlmostEqual(hill['summary']['ascent_m'], 200, delta=1)
        self.assertEqual(hill['summary']['descent_m'], 0)
        self.assertLessEqual(len(hill['preview']), 256)
        self.assertEqual(hill['preview'][0]['distance_m'], 0)
        self.assertEqual(hill['preview'][-1]['distance_m'], hill['points'][-1]['distance_m'])

    def test_segment_gaps_are_not_bridged(self):
        route = parse_gpx(gpx([], [[(0, 10), (.01, 20)], [(.02, 30), (.03, 40)]]))
        self.assertEqual(route['summary']['discontinuities'], 1)
        self.assertAlmostEqual(route['summary']['distance_m'], 2223.9, delta=1)
        self.assertEqual(route['points'][1]['distance_m'], route['points'][2]['distance_m'])
        self.assertEqual(len({point['segment'] for point in route['preview']}), 2)

    def test_rejects_entities_nonfinite_wrong_bounds_and_tampered_route(self):
        bad = ['<!DOCTYPE gpx [<!ENTITY x "test">]><gpx/>', '<not-gpx/>', '<gpx>',
               '<gpx><rte><rtept lat="nan" lon="0"/><rtept lat="0" lon=".1"/></rte></gpx>',
               '<gpx><rte><rtept lat="91" lon="0"/><rtept lat="0" lon=".1"/></rte></gpx>']
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_gpx(value)
        with self.assertRaises(ValueError):
            parse_gpx(b' ' * (MAX_GPX_BYTES + 1))
        route = parse_gpx(gpx([(0, 10), (.01, 20)]))
        for key, value in [('lat', True), ('lon', float('inf')), ('segment', -1), ('elevation_m', False)]:
            changed = copy.deepcopy(route)
            changed['points'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_route(changed)
        changed = copy.deepcopy(route)
        changed['summary']['distance_m'] = 1
        self.assertGreater(validate_route(changed)['summary']['distance_m'], 1000)

    def test_route_waypoints_are_supported_and_extensions_ignored(self):
        route = parse_gpx('<gpx><rte><name>Example</name><rtept lat="0" lon="0"><ele>10</ele><extensions><private>discard</private></extensions></rtept><rtept lat="0" lon=".01"><ele>20</ele></rtept></rte></gpx>')
        self.assertEqual(route['name'], 'Example')
        self.assertNotIn('discard', str(route))
        self.assertEqual(len(route['points']), 2)

    def test_realistic_large_race_track_and_point_limit(self):
        text = gpx([(index * .00002, 100) for index in range(21001)])
        self.assertGreater(len(text.encode()), 1024 * 1024)
        route = parse_gpx(text)
        self.assertEqual(route['summary']['point_count'], 21001)
        self.assertGreater(route['summary']['distance_m'], 40000)
        self.assertLessEqual(len(route['summary']['sections']), 12)
        self.assertLessEqual(len(route['preview']), 256)
        oversized = '<gpx><rte>' + '<rtept lat="0" lon="0"/>' * 50001 + '</rte></gpx>'
        with self.assertRaisesRegex(ValueError, '50,000'):
            parse_gpx(oversized)

    def test_preview_keeps_short_missing_height_gap_between_sampled_points(self):
        samples = [(index * .0001, 100 + index * .01) for index in range(600)]
        complete = parse_gpx(gpx(samples))
        shown = {point['lon'] for point in complete['preview']}
        missing = next(index for index in range(200, 300) if samples[index][0] not in shown)
        samples[missing] = (samples[missing][0], None)
        partial = parse_gpx(gpx(samples))
        gap = partial['points'][missing]['distance_m']
        after = next(point for point in partial['preview'] if point['distance_m'] > gap)
        original = next(point for point in partial['points'] if point['distance_m'] == after['distance_m'])
        self.assertIsNone(after['elevation_m'])
        self.assertIsNotNone(original['elevation_m'])
        self.assertIsNone(partial['points'][missing]['elevation_m'])
        self.assertIsNone(partial['summary']['ascent_m'])
        self.assertLessEqual(len(partial['preview']), 256)
        self.assertTrue(all(point['segment'] == 0 for point in partial['preview']))


if __name__ == '__main__':
    unittest.main()
