"""Observed Garmin running kinds share one ingestion, coaching and UI classifier."""
from contextlib import closing
import sqlite3,unittest

from database_manager import ensure_schema,ingest_payload,normalize_sport
from planning_engine import is_run,build_evidence
from generate_dashboard import build_overview
from source_policy import normalize_sport as shared_normalize_sport


class RunningTypeTests(unittest.TestCase):
    def test_running_aliases_and_other_sports_use_shared_classification(self):
        self.assertIs(normalize_sport,shared_normalize_sport)
        for kind in ('Run','running','trail_running','treadmill_running','track_running','ultra_run','VirtualRun'):
            with self.subTest(kind=kind):
                self.assertEqual(normalize_sport(kind),'run');self.assertTrue(is_run(kind))
        for kind in ('walking','hiking','strength_training','cycling','unknown'):
            with self.subTest(kind=kind):self.assertFalse(is_run(kind))

    def test_track_and_ultra_reach_coach_and_dashboard_without_changing_raw_kind(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.row_factory=sqlite3.Row;ensure_schema(conn)
            kinds=('track_running','ultra_run','walking','strength_training')
            for identifier,kind in enumerate(kinds,1):
                ingest_payload(conn,{'source':'garmin_personal','metadata_only':True,'activity_metrics':{
                    'id':identifier,'type':kind,'start_date':'2026-11-04T08:00:00','distance_m':5000,'moving_time_s':1800,'elevation_gain_m':50}})
            self.assertEqual([tuple(row) for row in conn.execute('SELECT type,normalized_type FROM activities ORDER BY id')],
                             [('track_running','run'),('ultra_run','run'),('walking','walk'),('strength_training','strength')])
            evidence=build_evidence(conn,'2026-11-04')
            self.assertEqual(evidence['running_28d']['count'],2)
            self.assertEqual(evidence['running_28d']['total_km'],10)
            overview=build_overview(conn,{'sessions':[]},'2026-11-04')
            self.assertEqual(overview['windows']['28']['runs'],2)
            self.assertEqual(overview['windows']['28']['distance_km'],10)


if __name__=='__main__':unittest.main()
