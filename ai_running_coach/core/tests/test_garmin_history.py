"""Isolated historical migration, using mocked Garmin responses only."""
from contextlib import closing,redirect_stdout,redirect_stderr
import io,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch

import garmin_sync
from garmin_personal_api import GarminPersonalError
from database_manager import build_master_database


def row(identifier):
    return {'activityId':identifier,'activityName':'Historical run','activityType':{'typeKey':'running'},
            'startTimeLocal':'2010-01-01 08:00:00','distance':5000,'duration':1800}


def fake_client():
    client=Mock();client.fingerprint='same-account-key';client.base_url='https://personal.connectapi.garmin.com'
    client.heart_rate_zones.return_value=[];client.activity.return_value={}
    return client


class HistoryTests(unittest.TestCase):
    def test_paginated_history_upserts_duplicates_and_preserves_daily_state_and_auth(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()):
            root=Path(d);daily=root/'daily.json';daily.write_text('{"last_success_at":"unchanged"}')
            auth=root/'garmin_auth_status.json';auth.write_text('{"state":"action_required"}')
            client=fake_client();client.activities.side_effect=[[row(i) for i in range(1,51)],[row(50),row(51)]]
            args=dict(db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'history.json',today='2026-11-04',
                      start_date='2010-01-01',end_date='2026-11-03',historical=True)
            self.assertEqual(garmin_sync.fetch_and_save_garmin_data(client,**args),0)
            self.assertEqual([call.kwargs['start'] for call in client.activities.call_args_list],[0,50])
            self.assertEqual(client.activities.call_args.args,('2010-01-01','2026-11-03'))
            build_master_database(root/'db.sqlite',root/'queue')
            with closing(sqlite3.connect(root/'db.sqlite')) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM activities').fetchone()[0],51)
            self.assertEqual(daily.read_text(),'{"last_success_at":"unchanged"}')
            self.assertEqual(auth.read_text(),'{"state":"action_required"}')
            state=json.loads((root/'history.json').read_text());self.assertNotIn('last_success_at',state)
            self.assertEqual(state['completed_window'],{'start_date':'2010-01-01','end_date':'2026-11-03'})
            again=fake_client();self.assertEqual(garmin_sync.fetch_and_save_garmin_data(again,**args),2)
            again.activities.assert_not_called();again.heart_rate_zones.assert_not_called()

    def test_partial_history_resumes_offset_without_losing_completed_payloads(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            root=Path(d);client=fake_client();client.activities.return_value=[row(1),row(2)]
            client.activity.side_effect=[{},GarminPersonalError('rate limited',429,999999)]
            args=dict(db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'history.json',today='2026-11-04',start_date='2010-01-01',end_date='2026-11-03',historical=True)
            self.assertEqual(garmin_sync.fetch_and_save_garmin_data(client,**args),1)
            build_master_database(root/'db.sqlite',root/'queue')
            self.assertEqual(json.loads((root/'history.json').read_text())['active_window']['next_offset'],1)
            second=fake_client();second.activities.return_value=[row(2)]
            self.assertEqual(garmin_sync.fetch_and_save_garmin_data(second,**args),0)
            self.assertEqual(second.activities.call_args.kwargs['start'],1)
            build_master_database(root/'db.sqlite',root/'queue')
            with closing(sqlite3.connect(root/'db.sqlite')) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM activities').fetchone()[0],2)

    def test_cli_discovers_earliest_then_resumes_and_ingests_without_touching_daily_binding(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()):
            root=Path(d);client=fake_client();daily=root/'daily.json'
            binding={'source_identity':client.fingerprint,'base_url':client.base_url,'last_completed_date':'2026-11-04'}
            daily.write_text(json.dumps(binding));before=daily.read_bytes()
            client.activities.side_effect=[[row(1)],[row(1)]]
            with patch.multiple(garmin_sync,SCRIPT_DIR=root,STATE_FILE=daily,DB_PATH=root/'db.sqlite',DATA_DIR=root/'queue'),patch.object(garmin_sync,'get_garmin_client',return_value=client),patch.object(garmin_sync,'publish_direct_auth_status'):
                self.assertEqual(garmin_sync.main(['--historical-backfill','--start-date','earliest','--end-date','2026-11-03']),0)
                self.assertEqual(client.activities.call_args_list[0].args,(None,'2026-11-03'))
                self.assertEqual(client.activities.call_args_list[0].kwargs,{'start':0,'limit':1})
                self.assertEqual(garmin_sync.main(['--historical-backfill','--start-date','earliest','--end-date','2026-11-03']),2)
                client.activities.side_effect=[[row(1)]]
                self.assertEqual(garmin_sync.main(['--historical-backfill','--start-date','earliest','--end-date','2026-11-03','--verify-history']),0)
                self.assertEqual(client.activities.call_args.args,(None,'2026-11-03'))
            self.assertEqual(daily.read_bytes(),before)
            self.assertEqual(client.activities.call_count,3)
            with closing(sqlite3.connect(root/'db.sqlite')) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM activities').fetchone()[0],1)

    def test_historical_cli_cannot_overlap_daily_pipeline(self):
        from cron_daily_update import pipeline_lock
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d)
            with pipeline_lock(root/'.pipeline.lock'),patch.object(garmin_sync,'SCRIPT_DIR',root),patch.object(garmin_sync,'get_garmin_client') as client:
                self.assertEqual(garmin_sync.main(['--historical-backfill','--start-date','earliest','--end-date','2026-11-03']),1)
                client.assert_not_called()

    def test_expected_batch_ceiling_preserves_auth_and_daily_freshness(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d);auth=root/'garmin_auth_status.json';auth.write_text('{"state":"ok"}')
            client=fake_client();client.activities.return_value=[row(1),row(2)]
            client.activity.side_effect=[{},GarminPersonalError('batch limit',code='batch_limit')]
            self.assertEqual(garmin_sync.fetch_and_save_garmin_data(client,db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'history.json',historical=True,start_date='2010-01-01',end_date='2026-11-03',today='2026-11-04'),1)
            self.assertEqual(auth.read_text(),'{"state":"ok"}')
            state=json.loads((root/'history.json').read_text())
            self.assertEqual(state['active_window']['next_offset'],1)
            self.assertNotIn('last_success_at',state)

    def test_fresh_summary_verification_repairs_gap_without_known_detail_requests(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()):
            root=Path(d);queue=root/'queue';queue.mkdir();state=root/'history.json';state.write_text('{}')
            from database_manager import ensure_schema,ingest_payload
            with closing(sqlite3.connect(root/'db.sqlite')) as conn,conn:
                ensure_schema(conn);ingest_payload(conn,garmin_sync.map_garmin_to_payload(row(1),{},[]))
            client=fake_client();client.activities.return_value=[row(1),row(2),row(2)]
            with patch.multiple(garmin_sync,DB_PATH=root/'db.sqlite',DATA_DIR=queue):
                self.assertEqual(garmin_sync.verify_history(client,'2010-01-01','2026-11-03',state),0)
            client.activity.assert_called_once_with(2)
            build_master_database(root/'db.sqlite',queue)
            with patch.object(garmin_sync,'DB_PATH',root/'db.sqlite'):
                self.assertEqual(garmin_sync.finish_history_verification(state),0)
            progress=json.loads(state.read_text())['verification']
            self.assertTrue(progress['complete']);self.assertEqual(progress['seen_ids'],[1,2]);self.assertEqual(progress['repaired_ids'],[2])
            with closing(sqlite3.connect(root/'db.sqlite')) as conn:self.assertEqual(conn.execute('SELECT count(*) FROM activities').fetchone()[0],2)

    def test_legacy_database_blocks_discovery_and_verification_before_network(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d);db=root/'db.sqlite'
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute('CREATE TABLE activities(id INTEGER,source TEXT)')
                conn.execute("INSERT INTO activities VALUES(1,'strava')")
            with patch.multiple(garmin_sync,SCRIPT_DIR=root,DB_PATH=db),patch.object(garmin_sync,'get_garmin_client') as client:
                self.assertEqual(garmin_sync.main(['--historical-backfill','--start-date','earliest','--end-date','2026-11-03']),1)
                client.assert_not_called()
                stub=fake_client()
                with self.assertRaises(ValueError):garmin_sync.verify_history(stub,'2010-01-01','2026-11-03',root/'history.json')
                stub.activities.assert_not_called()

    def test_completion_requires_ids_present_after_ingestion(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()):
            root=Path(d);state=root/'history.json'
            state.write_text(json.dumps({'verification':{'seen_ids':[123],'scan_complete':True,'complete':False,'next_offset':1}}))
            with patch.object(garmin_sync,'DB_PATH',root/'empty.sqlite'):
                self.assertEqual(garmin_sync.finish_history_verification(state),1)
            progress=json.loads(state.read_text())['verification']
            self.assertFalse(progress['complete']);self.assertEqual(progress['missing_ids'],[123]);self.assertEqual(progress['next_offset'],0)


if __name__=='__main__':unittest.main()
