from datetime import date
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest

from dashboard_panels import activity_measure, weekly_sport_data
from generate_dashboard import build_dashboard, parse_sessions
from planning_engine import parse_profile, render_plan


PROFILE = '''**Athlete Name:** Synthetic triathlete
**Training Mode:** triathlon
**Heart Rate Zones:** Not configured
**Weekly Availability:** Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday
**Pool Length:** 25 yd
**A-Race:** Synthetic triathlon
**Sport:** multisport
**Date:** 2026-12-01
**Target Time:** Just finish
'''


def fixture(root):
    profile = parse_profile(PROFILE)
    plan = {'schema_version': 1, 'revision': 2, 'profile_hash': profile['source_hash'], 'goals': profile['goals'], 'sessions': [
        {'id': 'z-bike', 'date': '2026-11-06', 'sport': 'cycling', 'workout_type': 'Easy', 'amount': '45 min',
         'target': 'Steady 150-180 W', 'brick': {'id': 'brick-one', 'order': 1},
         'workout': {'sport': 'cycling', 'name': 'Bike first', 'steps': [
             {'name': 'Steady', 'intensity': 'active', 'duration': {'type': 'time', 'seconds': 2700}, 'target': {'type': 'power', 'lowWatts': 150, 'highWatts': 180}}]}},
        {'id': 'a-run', 'date': '2026-11-06', 'sport': 'running', 'workout_type': 'Easy', 'amount': '20 min',
         'target': 'Comfortable running', 'brick': {'id': 'brick-one', 'order': 2}},
        {'id': 'pool', 'date': '2026-11-07', 'sport': 'swimming', 'sub_sport': 'lap_swimming',
         'workout_type': 'Technique', 'amount': '1000 m', 'target': 'Relaxed strokes'},
        {'id': 'water', 'date': '2026-11-08', 'sport': 'swimming', 'sub_sport': 'open_water',
         'workout_type': 'Open-water skills', 'amount': '1 km', 'target': 'Practised safe venue'},
    ]}
    (root / 'athlete.md').write_text(PROFILE, encoding='utf-8')
    (root / 'training_plan.json').write_text(json.dumps(plan), encoding='utf-8')
    md = root / 'training_plan.md'
    md.write_text(render_plan(plan), encoding='utf-8')
    db = root / 'data.sqlite'
    with closing(sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE activities(id INTEGER, name TEXT, type TEXT, start_date TEXT, distance_m REAL, moving_time_s REAL, timer_duration_s REAL, training_load REAL)')
        conn.executemany('INSERT INTO activities VALUES(?,?,?,?,?,?,?,?)', [
            (1, 'Run', 'running', '2026-11-05', 5000, 1800, 1900, 30),
            (2, 'Ride', 'cycling', '2026-11-05', 20000, 3600, 3700, 40),
            (3, 'Swim', 'lap_swimming', '2026-11-05', 1000, None, 1500, None),
            (4, 'Unknown swim distance', 'lap_swimming', '2026-11-06', None, None, None, None)])
        conn.commit()
    return plan, db, md


class MultisportDashboardTests(unittest.TestCase):
    def test_header_aware_markdown_preserves_units_and_same_title_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            plan, _, md = fixture(Path(directory))
            rows = parse_sessions(md.read_text(encoding='utf-8'))
            self.assertEqual([row['sport'] for row in rows], ['cycling', 'running', 'swimming', 'swimming'])
            self.assertEqual([row['amount'] for row in rows], ['45 min', '20 min', '1000 m', '1 km'])
            self.assertEqual(rows[2]['sub_sport'], 'lap_swimming')
            self.assertEqual(parse_sessions('| Monday | 2026-11-02 | Easy run | 4 km | Easy |')[0]['kind'], 'Easy run')

    def test_metrics_use_distinct_units_coverage_and_combined_duration_only(self):
        rows = [{'type': 'running', 'distance_m': 5000, 'moving_time_s': 1800, 'timer_duration_s': 1900},
                {'type': 'cycling', 'distance_m': 20000, 'moving_time_s': 3600, 'timer_duration_s': 3700},
                {'type': 'lap_swimming', 'distance_m': 1000, 'timer_duration_s': 1500},
                {'type': 'lap_swimming', 'distance_m': None}]
        stats = weekly_sport_data({'2026-11-06': rows}, [date(2026, 11, 2)], date(2026, 11, 6))
        self.assertEqual(stats['running'][0]['distance'], 5)
        self.assertEqual(stats['cycling'][0]['distance'], 20)
        self.assertEqual(stats['swimming'][0]['distance'], 1000)
        self.assertEqual(stats['swimming'][0]['distance_coverage'], 1)
        self.assertNotIn('distance', stats['all'][0])
        self.assertNotIn('distance', stats['other'][0])
        self.assertEqual(stats['all'][0]['duration_coverage'], 3)
        self.assertIn('6:00 /km', activity_measure(rows[0])[1])
        self.assertIn('20.0 km/h', activity_measure(rows[1])[1])
        self.assertIn('2:30 /100 m timer', activity_measure(rows[2])[1])
        self.assertEqual(activity_measure(rows[3]), ('Distance unavailable', ''))

    def test_render_preserves_canonical_and_correct_fit_mapping_and_pool_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, db, md = fixture(root)
            inputs = [root / 'training_plan.json', root / 'athlete.md', md, db]
            before = {path: path.read_bytes() for path in inputs}
            html = build_dashboard(db, md, root / 'dashboard.html', today=date(2026, 11, 6)).read_text(encoding='utf-8')
            ids = json.loads(re.search(r'const sessionIds = (.*);', html)[1])
            payload = json.loads(re.search(r'const workoutSessions = (.*);', html)[1])
            self.assertEqual(ids['2026-11-06|Easy'], ['z-bike', 'a-run'])
            self.assertEqual(payload['z-bike']['brick']['order'], 1)
            self.assertEqual(payload['a-run']['sport'], 'running')
            self.assertAlmostEqual(payload['pool']['pool_length_metres'], 22.86)
            self.assertEqual(payload['pool']['pool_length_unit'], 'yd')
            self.assertEqual(before, {path: path.read_bytes() for path in inputs})
            self.assertIn('No matching sessions this week.', html)
            self.assertIn('const trainingMode = "triathlon"', html)


if __name__ == '__main__':
    unittest.main()
