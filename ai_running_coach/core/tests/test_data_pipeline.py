import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from database_manager import (ensure_schema, ingest_payload, calculate_stream_features,
                              get_existing_source_ids, build_master_database, normalize_sport)
from parse_coach_output import process_ai_coach_review, process_activity_reviews


class DataPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/'test.sqlite')
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        ensure_schema(self.conn)

    def payload(self, source='local:fixture', activity_id=10, **metrics):
        return {'source':source,'activity_metrics':dict(id=activity_id, type='Run',
            start_date='2026-10-01T12:00:00Z', distance_m=5000,moving_time_s=1800,
            description='Original',**metrics),'time_series_streams':{}}

    def test_refresh_preserves_feedback_and_unknown_measurements(self):
        ingest_payload(self.conn,self.payload())
        self.conn.execute("UPDATE activities SET ai_feedback='Keep',notes='Local'")
        refreshed = self.payload()
        refreshed['activity_metrics'].update(description='New feedback',distance_m=None)
        refreshed['metadata_only'] = True
        ingest_payload(self.conn,refreshed)
        row=self.conn.execute('SELECT * FROM activities').fetchone()
        self.assertEqual((row['description'],row['distance_m'],row['ai_feedback'],row['notes']),
                         ('New feedback',5000,'Keep','Local'))

    def test_metadata_without_splits_preserves_saved_splits(self):
        payload=self.payload(splits_metric=[{'distance_m':1000}])
        ingest_payload(self.conn,payload)
        refreshed=self.payload(splits_metric=[])
        refreshed['metadata_only']=True
        ingest_payload(self.conn,refreshed)
        splits=self.conn.execute('SELECT raw_splits_json FROM activities').fetchone()[0]
        self.assertEqual(json.loads(splits),[{'distance_m':1000}])

    def test_failed_alternate_stream_fetch_preserves_observations(self):
        payload=self.payload()
        payload['time_series_streams']={'time':[0,1],'heartrate':[140,142]}
        ingest_payload(self.conn,payload)
        before=self.conn.execute('SELECT downsampled_streams_json,stream_source FROM activities').fetchone()
        ingest_payload(self.conn,self.payload('garmin_personal',99))
        after=self.conn.execute('SELECT downsampled_streams_json,stream_source FROM activities').fetchone()
        self.assertEqual(tuple(before),tuple(after))

    def test_source_aliases_and_numeric_collision(self):
        original=ingest_payload(self.conn,self.payload())
        self.assertNotEqual(ingest_payload(self.conn,self.payload('garmin_personal',99)),original)
        collision=self.payload('garmin_personal',10)
        collision['activity_metrics'].update(start_date='2026-10-02T12:00:00Z')
        other=ingest_payload(self.conn,collision)
        self.assertNotEqual(other,original)
        self.assertEqual(ingest_payload(self.conn,collision),other)
        self.conn.commit()
        self.assertEqual(get_existing_source_ids(self.path,'garmin_personal'),{10,99})
        self.assertEqual(get_existing_source_ids(self.path,'local:fixture'),{10})

    def test_additive_migration_does_not_change_existing_rows(self):
        old=sqlite3.connect(':memory:')
        self.addCleanup(old.close)
        old.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,name TEXT,ai_feedback TEXT)')
        old.execute("INSERT INTO activities VALUES(42,'Existing','Keep')")
        ensure_schema(old)
        ensure_schema(old)
        self.assertEqual(old.execute('SELECT id,name,ai_feedback FROM activities').fetchone(),(42,'Existing','Keep'))

    def test_zone_unknown_open_upper_and_invalid_boundaries(self):
        streams={'heartrate':[100,150,200,None,0]}
        self.assertNotIn('hr_zone_2_pct',calculate_stream_features(streams,{}))
        zones={'heart_rate':{'zones':[{'min':0,'max':140},{'min':140,'max':-1}]}}
        result=calculate_stream_features(streams,zones)
        self.assertEqual(result['hr_zone_2_pct'],66.7)
        self.assertIn('not_time',result['hr_zone_basis'])
        zones['heart_rate']['zones'][1]['max']=None
        self.assertNotIn('hr_zone_2_pct',calculate_stream_features(streams,zones))
        zones['heart_rate']['zones'][1].update(min=130,max=-1)
        self.assertNotIn('hr_zone_2_pct',calculate_stream_features(streams,zones))

    def test_drift_requires_paired_timestamped_steady_running(self):
        streams={'time':list(range(2401)), 'heartrate':[140]*2401,
                 'velocity_smooth':[3]*2401,'grade_smooth':[0]*2401,'moving':[True]*2401}
        self.assertEqual(calculate_stream_features(streams,{},'Run')['cardiac_drift_pct'],0)
        self.assertIsNone(calculate_stream_features(streams,{},'Swim')['cardiac_drift_pct'])
        without_time=dict(streams);without_time.pop('time')
        self.assertIsNone(calculate_stream_features(without_time,{},'Run')['cardiac_drift_pct'])
        hills=dict(streams,grade_smooth=[4]*2401)
        self.assertIsNone(calculate_stream_features(hills,{},'Run')['cardiac_drift_pct'])
        missing=dict(streams,heartrate=[None]*500+[140]*1901)
        missing['heartrate'][1000:1500]=[None]*500
        self.assertIsNone(calculate_stream_features(missing,{},'Run')['cardiac_drift_pct'])

    def test_unaligned_streams_downsample_without_losing_activity(self):
        payload=self.payload()
        payload['time_series_streams']={'time':[0,1,2], 'heartrate':[140], 'velocity_smooth':[3,3]}
        ingest_payload(self.conn,payload)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM activities').fetchone()[0],1)

    def test_feedback_ids_are_atomic_and_do_not_bleed_across_day(self):
        ingest_payload(self.conn,self.payload())
        another=self.payload(activity_id=11)
        another['activity_metrics']['start_date']='2026-10-01T18:00:00Z'
        ingest_payload(self.conn,another)
        self.conn.commit()
        process_activity_reviews([{'activity_id':10,'feedback':'Only first'}],self.path)
        self.assertIsNone(self.conn.execute('SELECT ai_feedback FROM activities WHERE id=11').fetchone()[0])
        with self.assertRaises(ValueError):
            process_activity_reviews([{'activity_id':10,'feedback':'Overwrite'},
                                      {'activity_id':999,'feedback':'Bad'}],self.path)
        self.assertEqual(self.conn.execute('SELECT ai_feedback FROM activities WHERE id=10').fetchone()[0],'Only first')
        with self.assertRaises((ValueError,json.JSONDecodeError)):
            process_ai_coach_review('[REVIEW: 2026-10-01 | Unsafe]',self.path)

    def test_pending_payloads_survive_failed_transaction(self):
        data=Path(self.tmp.name)/'data';data.mkdir()
        good=data/'a_ai_payload.json';bad=data/'b_ai_payload.json'
        good.write_text(json.dumps(self.payload()),encoding='utf-8')
        bad.write_text('{broken',encoding='utf-8')
        self.conn.commit()
        with self.assertRaises(json.JSONDecodeError):
            build_master_database(self.path,str(data))
        self.assertTrue(good.exists())
        self.assertEqual(self.conn.execute('SELECT count(*) FROM activities').fetchone()[0],0)

    def test_sport_normalization(self):
        self.assertEqual([normalize_sport(x) for x in ['Run','running','trail_running','VirtualRun']],['run']*4)
        self.assertNotEqual(normalize_sport('Walk'),'run')


if __name__=='__main__':
    unittest.main()
