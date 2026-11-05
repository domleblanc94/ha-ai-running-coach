"""Credential failures, bounded renewal and recovery; no real HTTP or SSH."""
import base64
from contextlib import closing,redirect_stderr,redirect_stdout
import io,json,os
from pathlib import Path
import tempfile,types,unittest
from unittest.mock import Mock,patch

import cron_daily_update as pipeline
import garmin_sync
from garmin_personal_api import GarminPersonalClient,GarminPersonalError,GarminConfigurationError


def jwt(expiry):
    part=base64.urlsafe_b64encode(json.dumps({'exp':expiry}).encode()).decode().rstrip('=')
    return 'test.'+part+'.signature'


def response(code,text='',data=None):
    return types.SimpleNamespace(status_code=code,text=text,headers={},json=lambda:data)


class GarminAuthActionsTests(unittest.TestCase):
    def cached_client(self,root,session):
        seed=Mock();seed.request.return_value=response(200,jwt(186400))
        original=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=seed,clock=lambda:100000)
        original.access_token()
        return GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=session,clock=lambda:100000)

    def test_expired_access_token_is_renewed_normally_without_action_required(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);session=Mock();session.request.side_effect=[response(200,jwt(186400)),response(200,data=[])]
            client=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=session,clock=lambda:100000)
            client.cache.update(access_token=jwt(99999),expires_at=99999)
            self.assertEqual(client.activities('2026-11-04','2026-11-04'),[])
            self.assertEqual([call.args[0] for call in session.request.call_args_list],['POST','GET'])
            self.assertTrue(client.auth_verified)
            self.assertNotIn('credential_rejection',client.cache)

    def test_rejected_cached_token_renews_exactly_once_and_recovers(self):
        with tempfile.TemporaryDirectory() as d:
            session=Mock();session.request.side_effect=[response(401,'SECRET_BODY'),response(200,jwt(186500)),response(200,data=[])]
            client=self.cached_client(Path(d),session)
            self.assertEqual(client.activities('2026-11-04','2026-11-04'),[])
            self.assertEqual([call.args[0] for call in session.request.call_args_list],['GET','POST','GET'])
            self.assertTrue(client.auth_verified)

    def test_personal_key_rejection_is_actionable_and_pauses_automatic_exchanges(self):
        for code in (400,401,403):
            with self.subTest(code=code),tempfile.TemporaryDirectory() as d:
                root=Path(d);session=Mock();session.request.return_value=response(code,'SECRET_BODY')
                client=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=session,clock=lambda:100000)
                with self.assertRaises(GarminPersonalError) as result:client.activities('2026-11-04','2026-11-04')
                self.assertEqual(result.exception.code,'key_rejected');self.assertEqual(result.exception.stage,'token')
                self.assertTrue(result.exception.action_required);self.assertNotIn('SECRET',str(result.exception))
                second_session=Mock();second=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=second_session,clock=lambda:100001)
                with self.assertRaises(GarminPersonalError):second.access_token()
                second_session.request.assert_not_called()
                second_session.request.return_value=response(200,jwt(186500))
                second.access_token(force_refresh=True)
                self.assertEqual(second_session.request.call_count,1)
                self.assertNotIn('credential_rejection',second.cache)

    def test_failed_renewal_during_zones_is_not_swallowed_as_optional_403(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()) as terminal:
            root=Path(d);session=Mock();session.request.return_value=response(403,'SECRET_BODY')
            client=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=session,clock=lambda:100000)
            result=garmin_sync.fetch_and_save_garmin_data(client,db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'state.json',today='2026-11-04')
            self.assertEqual(result,1);self.assertEqual(session.request.call_count,1)
            status=json.loads((root/'garmin_auth_status.json').read_text())
            self.assertEqual((status['state'],status['code']),('action_required','key_rejected'))
            self.assertNotIn('SECRET',terminal.getvalue());self.assertNotIn('fingerprint',json.dumps(status))
            self.assertNotIn('last_completed_date',json.loads((root/'state.json').read_text()))

    def test_optional_zone_permission_failure_does_not_imply_bad_key(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()):
            root=Path(d);session=Mock();session.request.side_effect=[response(200,jwt(186400)),response(403,'SECRET_BODY'),response(200,data=[])]
            client=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=session,clock=lambda:100000)
            result=garmin_sync.fetch_and_save_garmin_data(client,db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'state.json',today='2026-11-04')
            self.assertEqual(result,2)
            self.assertEqual(json.loads((root/'garmin_auth_status.json').read_text())['state'],'ok')

    def test_activity_permission_failure_is_actionable_without_expiry_claim(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d);session=Mock();session.request.side_effect=[response(200,jwt(186400)),response(200,data=[]),response(403,'SECRET_BODY')]
            client=GarminPersonalClient('SECRET_TEST_KEY',root/'token.json',session=session,clock=lambda:100000)
            self.assertEqual(garmin_sync.fetch_and_save_garmin_data(client,db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'state.json',today='2026-11-04'),1)
            status=json.loads((root/'garmin_auth_status.json').read_text())
            self.assertEqual(status['code'],'permission_denied');self.assertEqual(status['state'],'action_required')
            self.assertIn('does not establish',status['action'])

    def test_missing_key_has_persistent_action_without_http(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'GARMIN_PERSONAL_API_KEY':''}),patch.object(garmin_sync,'load_environment'),redirect_stderr(io.StringIO()):
            root=Path(d)
            with patch('garmin_personal_api.requests.Session') as session:
                with self.assertRaises(GarminConfigurationError):garmin_sync.fetch_and_save_garmin_data(db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'state.json',today='2026-11-04')
                session.assert_not_called()
            status=json.loads((root/'garmin_auth_status.json').read_text())
            self.assertEqual((status['state'],status['code']),('action_required','missing_key'))

    def test_same_key_unverified_failure_preserves_action_but_new_key_not_mislabelled(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            path=Path(d)/'garmin_auth_status.json'
            garmin_sync.write_auth_status(path,'key_rejected',context='private-key-A')
            garmin_sync.record_auth_error(path,GarminPersonalError('network failed',code='temporary_error'),context='private-key-A')
            self.assertEqual(json.loads(path.read_text())['state'],'action_required')
            garmin_sync.record_auth_error(path,GarminPersonalError('network failed',code='temporary_error'),context='private-key-B')
            status=json.loads(path.read_text())
            self.assertEqual(status['state'],'temporary_error');self.assertEqual(status['code'],'temporary_error')
            self.assertNotIn('private-key',json.dumps(status))

    def test_only_successful_full_sync_clears_action_and_token_only_probe_publishes_pending(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()):
            root=Path(d);path=root/'garmin_auth_status.json';stub=Mock()
            stub.fingerprint='private-key-A';stub.base_url='https://personal.connectapi.garmin.com'
            stub.heart_rate_zones.return_value=[];stub.activities.return_value=[]
            garmin_sync.write_auth_status(path,'key_rejected',context=stub.fingerprint)
            with patch.multiple(garmin_sync,SCRIPT_DIR=root,AUTH_STATUS_FILE=path),patch.object(garmin_sync,'get_garmin_client',return_value=stub),patch.object(garmin_sync,'publish_direct_auth_status') as publish:
                self.assertEqual(garmin_sync.main(['--refresh-token']),0)
                publish.assert_called_once()
            self.assertEqual(json.loads(path.read_text())['code'],'verification_pending')
            self.assertEqual(json.loads(path.read_text())['state'],'action_required')
            self.assertEqual(garmin_sync.fetch_and_save_garmin_data(stub,db_path=root/'db.sqlite',data_dir=root/'queue',state_file=root/'state.json',today='2026-11-04'),2)
            self.assertEqual(json.loads(path.read_text())['state'],'ok')

    def test_pipeline_delivers_structured_auth_failure_without_coaching_or_fresh_success(self):
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'COACH_ACTIVITY_SOURCE':'garmin'}):
            root=Path(d);garmin_sync.write_auth_status(root/'garmin_auth_status.json','key_rejected',context='private-key-A')
            with patch.object(pipeline,'SCRIPT_DIR',root),patch.object(pipeline,'run_script',side_effect=[1,0,0]) as run,patch.object(pipeline,'update_health') as health,patch.object(pipeline,'upload_health',return_value=True) as upload:
                self.assertEqual(pipeline.run_pipeline(),1)
            self.assertEqual([call.args[0] for call in run.call_args_list],['garmin_sync.py','database_manager.py','generate_dashboard.py'])
            auth_calls=[call.kwargs['garmin_auth'] for call in health.call_args_list if 'garmin_auth' in call.kwargs]
            self.assertEqual(auth_calls[0]['state'],'action_required')
            self.assertNotIn('fingerprint',json.dumps(auth_calls))
            self.assertFalse(any('last_sync_success_at' in call.kwargs for call in health.call_args_list))
            upload.assert_called_once()

    def test_malformed_private_context_does_not_block_new_failure_status(self):
        with tempfile.TemporaryDirectory() as d,redirect_stderr(io.StringIO()):
            root=Path(d);path=root/'garmin_auth_status.json'
            for malformed in ([], {'fingerprint':'same-key','status':'invalid'}):
                (root/'.garmin_auth_context.json').write_text(json.dumps(malformed),encoding='utf-8')
                status=garmin_sync.record_auth_error(path,GarminPersonalError('network failed',code='temporary_error'),context='same-key')
                self.assertEqual(status['code'],'temporary_error')

    def test_offline_pipeline_clears_irrelevant_garmin_action(self):
        from health_status import update_health,read_state,public_state
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'COACH_ACTIVITY_SOURCE':'offline'}):
            root=Path(d)
            auth=garmin_sync.write_auth_status(root/'garmin_auth_status.json','key_rejected',context='private-key-A')
            update_health(root,garmin_auth=auth)
            with patch.object(pipeline,'SCRIPT_DIR',root),patch.object(pipeline,'run_script',return_value=0) as run,patch.object(pipeline,'upload_health',return_value=True):
                self.assertEqual(pipeline.run_pipeline(),0)
            self.assertNotIn('garmin_sync.py',[call.args[0] for call in run.call_args_list])
            self.assertIsNone(read_state(root).get('garmin_auth'))
            self.assertNotEqual(public_state(read_state(root))['status'],'input_required')

    def test_direct_sync_status_delivery_respects_active_source_without_model_calls(self):
        from health_status import read_state
        with tempfile.TemporaryDirectory() as d,patch.dict(os.environ,{'COACH_PIPELINE_CHILD':''}):
            root=Path(d)
            garmin_sync.write_auth_status(root/'garmin_auth_status.json','key_rejected',context='private-key-A')
            with patch.object(garmin_sync,'SCRIPT_DIR',root),patch('health_status.upload_health',return_value=True) as upload:
                with patch.dict(os.environ,{'COACH_ACTIVITY_SOURCE':'garmin'}):
                    garmin_sync.publish_direct_auth_status()
                    self.assertEqual(read_state(root)['garmin_auth']['state'],'action_required')
                with patch.dict(os.environ,{'COACH_ACTIVITY_SOURCE':'offline'}):
                    garmin_sync.publish_direct_auth_status()
                    self.assertIsNone(read_state(root)['garmin_auth'])
                self.assertEqual(upload.call_count,2)
            self.assertNotIn('last_coach_success_at',read_state(root))
            with patch.object(garmin_sync,'SCRIPT_DIR',root),patch('health_status.upload_health',return_value=False),patch.dict(os.environ,{'COACH_ACTIVITY_SOURCE':'garmin'}),redirect_stderr(io.StringIO()) as error:
                garmin_sync.publish_direct_auth_status()
                self.assertIn('delivery to Home Assistant failed',error.getvalue())


if __name__=='__main__':unittest.main()
