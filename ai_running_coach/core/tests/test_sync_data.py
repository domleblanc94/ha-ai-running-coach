"""Official Garmin sync regression tests; all HTTP traffic is mocked."""
from contextlib import closing,redirect_stderr
from datetime import datetime,timezone
import io,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

from garmin_personal_api import GarminPersonalError
from garmin_sync import fetch_and_save_garmin_data,map_garmin_to_payload
from database_manager import ensure_schema,ingest_payload
from source_policy import assert_coaching_sources
import ai_coach
from database_manager import build_master_database


def activity(identifier=12,**extra):
    row={'activityId':identifier,'activityName':'Synthetic run','activityType':{'typeKey':'running'},
         'startTimeLocal':'2026-11-04 08:00:00','startTimeGMT':'2026-11-04 08:00:00',
         'distance':5000,'duration':1800,'movingDuration':1750,'elevationGain':60}
    row.update(extra);return row


def client():
    result=Mock();result.fingerprint='fixture-key-and-host';result.base_url='https://personal.connectapi.garmin.com'
    result.heart_rate_zones.return_value=[]
    return result


class OfficialSyncTests(unittest.TestCase):
    def test_fresh_official_sync_ingestion_then_coach_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'data.sqlite';queue=root/'queue';state=root/'sync.json'
            stub=client();stub.activities.return_value=[activity(12,activityTrainingLoad=42)]
            stub.activity.return_value={'activityId':12,'description':'[coach: rpe=4; pain=none]',
                                        'summaryDTO':{'perceivedWorkoutEffort':60,'directWorkoutFeel':3}}
            self.assertEqual(fetch_and_save_garmin_data(stub,db_path=db,data_dir=queue,state_file=state,today='2026-11-04'),0)
            build_master_database(db,queue)
            self.assertFalse(list(queue.glob('*_ai_payload.json')))
            profile=root/'athlete.md';profile.write_text('**Heart Rate Zones:** Unknown\n**Active Injuries:** None\n',encoding='utf-8')
            before=db.read_bytes()
            with patch.multiple(ai_coach,ATHLETE_FILE=profile,DB_PATH=db,PLAN_FILE=root/'training_plan.md',CANONICAL_FILE=root/'training_plan.json'),patch.object(ai_coach,'api_client',side_effect=AssertionError('Dry run must not call AI')),patch.dict('os.environ',{'COACH_ACTIVITY_SOURCE':'garmin'}),patch('sys.stdout',new=io.StringIO()):
                result=ai_coach.run_daily_coach_check(today='2026-11-04',dry_run=True,new_plan=True)
            row=result['evidence']['recent_7d'][0]
            self.assertEqual(row['source'],'garmin_personal')
            self.assertEqual(row['training_load'],42)
            self.assertEqual(row['provider_metrics']['perceivedWorkoutEffort'],60)
            self.assertEqual(row['reported_feedback'][0]['fields']['rpe'],4)
            self.assertIsNone(row['perceived_exertion']);self.assertIsNone(row['cardiac_drift_pct'])
            self.assertEqual(db.read_bytes(),before)
            self.assertFalse((root/'training_plan.json').exists())

    def test_unsafe_stream_history_cannot_bypass_source_readiness(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.row_factory=sqlite3.Row;ensure_schema(conn)
            ingest_payload(conn,{'source':'garmin_personal','metadata_only':True,'activity_metrics':{'id':1,'type':'running','start_date':'2026-11-04'}})
            conn.execute("UPDATE activities SET stream_source='strava'")
            with self.assertRaises(ValueError):assert_coaching_sources(conn)

    def test_legacy_rows_never_reach_the_ai_client_or_new_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'legacy.sqlite';profile=root/'athlete.md'
            profile.write_text('**Heart Rate Zones:** Unknown\n',encoding='utf-8')
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE activities(id INTEGER,source TEXT)');conn.execute("INSERT INTO activities VALUES(1,'strava')");conn.commit()
            before=db.read_bytes()
            with patch.multiple(ai_coach,ATHLETE_FILE=profile,DB_PATH=db,PLAN_FILE=root/'plan.md',CANONICAL_FILE=root/'plan.json'),patch.object(ai_coach,'api_client') as generate,patch.dict('os.environ',{'COACH_ACTIVITY_SOURCE':'garmin'}):
                for dry_run in (True,False):
                    with self.assertRaisesRegex(ValueError,'legacy or unidentified'):ai_coach.run_daily_coach_check(dry_run=dry_run,new_plan=True)
                generate.assert_not_called()
            self.assertEqual(db.read_bytes(),before)
            self.assertFalse((root/'plan.json').exists())

    def test_documented_summary_detail_units_unknowns_and_raw_effort(self):
        row=activity(12,movingDuration=None,averageHR=0,activityTrainingLoad=42,
                     hrTimeInZone_1=0,hrTimeInZone_2=0,hrTimeInZone_3=0,hrTimeInZone_4=0,hrTimeInZone_5=0)
        detail={'activityId':12,'activityTypeDTO':{'typeKey':'running'},'description':'User report [coach: rpe=4]',
                'summaryDTO':{'duration':1800,'perceivedWorkoutEffort':60,'directWorkoutFeel':3},
                'splitSummaries':[{'distance':1000,'duration':350,'splitType':'MARKER_KILOMETER'}]}
        payload=map_garmin_to_payload(row,detail,[]);metrics=payload['activity_metrics']
        self.assertEqual(payload['source'],'garmin_personal')
        self.assertIsNone(metrics['moving_time_s']);self.assertEqual(metrics['timer_duration_s'],1800)
        self.assertIsNone(metrics['avg_heartrate']);self.assertEqual(metrics['training_load'],42)
        self.assertIsNone(metrics['perceived_exertion']);self.assertNotIn('suffer_score',metrics)
        self.assertEqual(json.loads(metrics['provider_metrics_json'])['perceivedWorkoutEffort'],60)
        self.assertIsNone(metrics['hr_zone_2_pct']);self.assertEqual(metrics['hr_zone_basis'],'no_reported_zone_duration')
        self.assertEqual(metrics['splits_metric'],detail['splitSummaries'])
        self.assertEqual(metrics['description'],detail['description'])
        self.assertTrue(metrics['source_start_date_utc'].endswith('+00:00'))
        self.assertIsNone(map_garmin_to_payload(activity(13,duration=None,movingDuration=None))['activity_metrics']['timer_duration_s'])

    def test_reported_zone_seconds_use_known_time_without_default_zone_generation(self):
        payload=map_garmin_to_payload(activity(**{'hrTimeInZone_'+str(i):i*10 for i in range(1,6)}),{},[{'sport':'RUNNING','zone2Floor':158}])
        metrics=payload['activity_metrics']
        self.assertEqual(metrics['hr_zone_2_pct'],13.3)
        self.assertEqual(json.loads(metrics['hr_zones_json'])[0]['zone2Floor'],158)
        self.assertEqual(metrics['hr_zone_basis'],'reported_zone_seconds_fraction')

    def test_partial_rate_limit_preserves_queue_and_resumes_exact_offset(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d);first=client();first.activities.return_value=[activity(12),activity(13)]
            first.activity.side_effect=[{},GarminPersonalError('rate limited',429,999999)]
            args={'db_path':root/'data.sqlite','data_dir':root/'queue','state_file':root/'sync.json','today':'2026-11-04'}
            self.assertEqual(fetch_and_save_garmin_data(first,**args),1)
            state=json.loads((root/'sync.json').read_text())
            self.assertEqual(state['active_window']['next_offset'],1);self.assertNotIn('last_completed_date',state)
            self.assertTrue((root/'queue'/'garmin_personal_12_ai_payload.json').exists())
            second=client();second.activities.return_value=[activity(13)];second.activity.return_value={}
            self.assertEqual(fetch_and_save_garmin_data(second,**args),0)
            self.assertEqual(second.activities.call_args.kwargs['start'],1)
            self.assertTrue((root/'queue'/'garmin_personal_13_ai_payload.json').exists())
            state=json.loads((root/'sync.json').read_text());self.assertNotIn('active_window',state)
            self.assertEqual(state['last_completed_date'],'2026-11-04')

    def test_resumed_historical_window_does_not_claim_current_day_freshness(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d);first=client();first.activities.return_value=[activity(12),activity(13)]
            first.activity.side_effect=[{},GarminPersonalError('rate limited',429)]
            args={'db_path':root/'db.sqlite','data_dir':root/'queue','state_file':root/'state.json'}
            self.assertEqual(fetch_and_save_garmin_data(first,today='2026-11-04',**args),1)
            resumed=client();resumed.activities.return_value=[activity(13)];resumed.activity.return_value={}
            self.assertEqual(fetch_and_save_garmin_data(resumed,today='2026-11-07',**args),1)
            state=json.loads((root/'state.json').read_text())
            self.assertEqual(state['last_completed_date'],'2026-11-04')
            self.assertNotIn('last_success_at',state)
            catchup=client();catchup.activities.return_value=[]
            self.assertEqual(fetch_and_save_garmin_data(catchup,today='2026-11-07',**args),2)
            self.assertEqual(catchup.activities.call_args.args[1],'2026-11-07')
            self.assertEqual(json.loads((root/'state.json').read_text())['last_completed_date'],'2026-11-07')

    def test_empty_sync_returns_no_new_data_and_explicit_backfill_is_honored(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);stub=client();stub.activities.return_value=[]
            args={'db_path':root/'data.sqlite','data_dir':root/'queue','state_file':root/'sync.json','today':'2026-11-04'}
            self.assertEqual(fetch_and_save_garmin_data(stub,**args),2)
            self.assertEqual(fetch_and_save_garmin_data(stub,backfill_days=365,**args),2)
            self.assertEqual(stub.activities.call_args.args[0],'2025-11-05')

    def test_legacy_database_blocks_network_and_remains_byte_identical(self):
        for source in ('strava','garmin',None):
            with self.subTest(source=source),tempfile.TemporaryDirectory() as d:
                root=Path(d);db=root/'legacy.sqlite'
                with closing(sqlite3.connect(db)) as conn:
                    conn.execute('CREATE TABLE activities(id INTEGER,source TEXT)');conn.execute('INSERT INTO activities VALUES(1,?)',(source,));conn.commit()
                before=db.read_bytes();stub=client()
                with self.assertRaises(ValueError):fetch_and_save_garmin_data(stub,db_path=db,data_dir=root/'queue',state_file=root/'state',today='2026-11-04')
                self.assertEqual(db.read_bytes(),before);stub.heart_rate_zones.assert_not_called()
                self.assertFalse((root/'queue').exists())

    def test_official_source_is_never_aliased_to_existing_legacy_measurements(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.row_factory=sqlite3.Row;ensure_schema(conn)
            legacy={'source':'strava','metadata_only':True,'activity_metrics':{'id':12,'type':'running','start_date':'2026-11-04T08:00:00','distance_m':5000,'moving_time_s':1750}}
            old=ingest_payload(conn,legacy)
            official=ingest_payload(conn,map_garmin_to_payload(activity(12)))
            self.assertNotEqual(old,official)
            self.assertEqual(conn.execute('SELECT source FROM activities WHERE id=?',(old,)).fetchone()[0],'strava')
            with self.assertRaises(ValueError):assert_coaching_sources(conn)

    def test_key_change_requires_same_account_confirmation_and_region_never_adopts(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);state=root/'state.json';state.write_text(json.dumps({'source_identity':'old-key','base_url':'https://personal.connectapi.garmin.com','last_completed_date':'2026-11-03'}))
            stub=client();stub.activities.return_value=[]
            args={'db_path':root/'data.sqlite','data_dir':root/'queue','state_file':state,'today':'2026-11-04'}
            before=state.read_bytes()
            with self.assertRaises(ValueError):fetch_and_save_garmin_data(stub,**args)
            self.assertEqual(state.read_bytes(),before);stub.heart_rate_zones.assert_not_called()
            self.assertEqual(fetch_and_save_garmin_data(stub,accept_key_rotation=True,**args),2)
            stub.base_url='https://personal.connectapi.garmin.cn'
            with self.assertRaises(ValueError):fetch_and_save_garmin_data(stub,accept_key_rotation=True,**args)

    def test_missing_binding_cannot_adopt_an_existing_official_dataset(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db=root/'data.sqlite'
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE activities(id INTEGER,source TEXT)');conn.execute("INSERT INTO activities VALUES(1,'garmin_personal')");conn.commit()
            stub=client()
            with self.assertRaises(ValueError):fetch_and_save_garmin_data(stub,db_path=db,data_dir=root/'queue',state_file=root/'missing-state.json',today='2026-11-04')
            stub.heart_rate_zones.assert_not_called()

if __name__=='__main__':unittest.main()
