"""Stored detailed activity boundary: offline, bounded and coordinate-free."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from coach_chat_service import CoachChatService, ChatError
from database_manager import ensure_schema, ingest_payload
from garmin_sync import map_garmin_to_payload


class ActivityDetailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root/'ai_coach_master.sqlite'
        with closing(sqlite3.connect(self.db)) as conn:
            ensure_schema(conn)
            conn.execute("INSERT INTO activities(id,source,type,start_date,distance_m,avg_heartrate) VALUES (1,'local:fixture','Run','2020-01-01',5000,150)")
            conn.commit()
        self.service = CoachChatService(self.root)

    def update(self, **values):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('UPDATE activities SET '+','.join('"'+key+'"=?' for key in values)+' WHERE id=1', list(values.values()))
            conn.commit()

    def read(self, **values):
        return self.service.dispatch('activity_details', {'activity_id':1, **values})

    def assert_code(self, code, payload):
        with self.assertRaises(ChatError) as caught:
            self.service.dispatch('activity_details', payload)
        self.assertEqual(caught.exception.code, code)

    def test_summary_only_is_explicit_and_needs_no_plan_or_profile(self):
        result = self.read()
        self.assertEqual(result['activity']['avg_heartrate'],150)
        self.assertIsNone(result['activity']['avg_watts'])
        self.assertIn('avg_watts',result['unknown_fields'])
        self.assertEqual(result['splits']['status'],'not_stored')
        self.assertEqual(result['laps']['status'],'not_stored')
        self.assertEqual(result['streams']['status'],'not_requested')
        self.assertEqual(self.read(include_streams=True)['streams']['status'],'not_stored')
        self.assertEqual(result['provenance']['source'],'local:fixture')

    def test_details_page_numeric_measurements_without_gps_secrets_or_network(self):
        splits=[{'split':i+1,'distance':1000,'elapsed_time':300+i,'average_heartrate':150+i,
                 'averagePower':250,'averageRunCadence':174,'latitude':'GPS-SECRET',
                 'api_key':'CREDENTIAL-SECRET','nested':{'longitude':'GPS-SECRET'}} for i in range(5)]
        streams=[{'time':i*30,'heartrate':140+i,'velocity_smooth':3.2,'watts':230,
                  'cadence':86,'altitude':25,'latlng':['GPS-SECRET'], 'token':'CREDENTIAL-SECRET'} for i in range(7)]
        self.update(raw_splits_json=json.dumps(splits), downsampled_streams_json=json.dumps(streams),
                    hr_zone_seconds_json=json.dumps({'1':0,'2':120,'latitude':'GPS-SECRET'}),
                    provider_metrics_json=json.dumps({'perceivedWorkoutEffort':4,'token':'CREDENTIAL-SECRET'}),
                    stream_source='garmin_personal', metrics_source='local:fixture')
        (self.root/'.env').write_text('CREDENTIAL-SECRET',encoding='utf-8')
        with patch('socket.socket.connect',side_effect=AssertionError('No network')), patch('ai_coach.api_client',side_effect=AssertionError('No AI')):
            result=self.read(include_streams=True,offset=2,limit=2)
        self.assertEqual(result['splits']['total'],5)
        self.assertEqual(result['splits']['next_offset'],4)
        self.assertEqual(result['streams']['total'],7)
        self.assertEqual(result['streams']['items'][0]['time_s'],60)
        self.assertEqual(result['splits']['items'][0]['avg_heartrate'],152)
        self.assertEqual(result['splits']['items'][0]['avg_run_cadence'],174)
        self.assertEqual(result['hr_zone_seconds']['values']['1'],0)
        self.assertIsNone(result['hr_zone_seconds']['values']['3'])
        self.assertEqual(result['provenance']['stream_source'],'garmin_personal')
        text=json.dumps(result,allow_nan=False)
        self.assertNotIn('GPS-SECRET',text)
        self.assertNotIn('CREDENTIAL-SECRET',text)
        self.assertIsNone(self.read(offset=4,limit=2)['splits']['next_offset'])
        self.assertEqual(self.read(offset=100,limit=2)['splits']['items'],[])

    def test_ingestion_roundtrip_garmin_splits_and_offline_stream_zones(self):
        zones=[{'sport':'RUNNING','zone2Floor':158,'token':'CREDENTIAL-SECRET'}]
        payload=map_garmin_to_payload({'activityId':22,'activityType':{'typeKey':'running'},
            'startTimeLocal':'2026-11-25T08:00:00','distance':5000,'duration':1800},
            {'splitSummaries':[{'distance':1000,'duration':350,'splitType':'MARKER_KILOMETER'}]}, zones)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory=sqlite3.Row
            ingest_payload(conn,payload)
            ingest_payload(conn,{'source':'local:fixture','activity_metrics':{'id':23,'type':'Run','start_date':'2026-11-25'},
                'athlete_zones':{'heart_rate':{'zones':[{'min':0,'max':140},{'min':140,'max':-1}]}},
                'time_series_streams':{'time':[0,10,30],'heartrate':[130,140,150],'moving':[1,0,1], 'latlng':[[1,2],[3,4],[5,6]]}})
            conn.commit()
        garmin=self.service.dispatch('activity_details',{'activity_id':22})
        self.assertEqual(garmin['splits']['items'][0]['split_type'],'MARKER_KILOMETER')
        self.assertEqual(garmin['splits']['items'][0]['timer_duration_s'],350)
        self.assertEqual(garmin['hr_zone_configuration']['configurations'][0]['zone2Floor'],158)
        self.assertEqual(garmin['hr_zone_configuration']['source'],'garmin_personal')
        self.assertEqual(garmin['hr_zone_configuration']['configuration_role'],'garmin_profile_settings_at_sync')
        self.assertIn('not coach athlete.md zones',garmin['hr_zone_configuration']['scope'])
        self.assertEqual(self.service.dispatch('activity_details',{'activity_id':22,'include_streams':True})['streams']['status'],'not_stored')
        offline=self.service.dispatch('activity_details',{'activity_id':23,'include_streams':True})
        self.assertEqual(offline['streams']['items'][0]['heartrate_bpm'],135)
        self.assertEqual(offline['streams']['items'][0]['moving'],0.5)
        self.assertEqual(offline['hr_zone_configuration']['configurations'][0]['zones'][1]['max'],-1)
        self.assertEqual(offline['hr_zone_configuration']['source'],'local:fixture')
        self.assertEqual(offline['hr_zone_configuration']['configuration_role'],'imported_source_configuration')
        self.assertIn('historical/activity-specific origin unknown',offline['hr_zone_configuration']['scope'])
        self.assertFalse(offline['hr_zone_configuration']['coach_profile_substitution'])
        self.assertNotIn('CREDENTIAL-SECRET',json.dumps(garmin))

    def test_invalid_unknown_and_signed_internal_ids(self):
        for payload in ({}, {'activity_id':True}, {'activity_id':'1'}, {'activity_id':0}, {'activity_id':2**63},
                        {'activity_id':1,'include_streams':1}, {'activity_id':1,'offset':-1},
                        {'activity_id':1,'limit':201}, {'activity_id':1,'limit':True}, {'activity_id':1,'path':'.env'}):
            self.assert_code('invalid_input',payload)
        self.assert_code('not_found',{'activity_id':999})
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('UPDATE activities SET id=-9')
            conn.commit()
        self.assertEqual(self.service.dispatch('activity_details',{'activity_id':-9})['activity']['id'],-9)

    def test_policy_fails_closed_for_metrics_and_stream_provenance(self):
        for column in ('source','metrics_source','stream_source'):
            self.update(**{column:'legacy'})
            self.assert_code('source_policy',{'activity_id':1})
            self.update(**{column:'local:fixture'})

    def test_malformed_large_and_missing_numeric_data(self):
        self.update(raw_splits_json='[',downsampled_streams_json='{}')
        result=self.read(include_streams=True)
        self.assertEqual(result['splits']['status'],'invalid_stored_data')
        self.assertEqual(result['streams']['status'],'invalid_stored_data')
        self.update(raw_splits_json=' '* (4*1024*1024+1), downsampled_streams_json='[{"time":NaN,"heartrate":null,"watts":true}]', avg_watts=float('inf'))
        result=self.read(include_streams=True)
        self.assertEqual(result['splits']['status'],'too_large')
        self.assertIsNone(result['activity']['avg_watts'])
        self.assertIsNone(result['streams']['items'][0]['time_s'])
        self.assertIsNone(result['streams']['items'][0]['power_watts'])
        json.dumps(result,allow_nan=False)


if __name__=='__main__':
    unittest.main()
