import base64
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import Mock,patch
import contextlib,io

from garmin_personal_api import GarminPersonalClient,GarminPersonalError
import garmin_sync
from cron_daily_update import pipeline_lock


def token(expiry):
    encoded=base64.urlsafe_b64encode(json.dumps({'exp':expiry}).encode()).decode().rstrip('=')
    return 'synthetic.'+encoded+'.signature'


def response(code,text='',data=None,headers=None):
    return types.SimpleNamespace(status_code=code,text=text,headers=headers or {},json=lambda:data)


class GarminTransportTests(unittest.TestCase):
    def test_actual_429_handler_persists_retry_after_and_stops_new_client_bursts(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'token.json';now=[100000.0]
            session=Mock();session.request.side_effect=[response(200,token(now[0]+86400)),response(429,'Do not print a key here',headers={'Retry-After':'60'})]
            first=GarminPersonalClient('fake-personal-key',path,session=session,clock=lambda:now[0])
            with self.assertRaises(GarminPersonalError) as error:first.activities('2026-11-04','2026-11-04')
            self.assertEqual(error.exception.status_code,429);self.assertEqual(session.request.call_count,2)
            self.assertEqual(json.loads(path.read_text())['not_before'],100060)
            now[0]+=30;second_session=Mock();second=GarminPersonalClient('fake-personal-key',path,session=second_session,clock=lambda:now[0])
            with self.assertRaises(GarminPersonalError):second.activities('2026-11-04','2026-11-04')
            second_session.request.assert_not_called()
            now[0]+=31;second_session.request.return_value=response(200,data=[])
            self.assertEqual(second.activities('2026-11-04','2026-11-04'),[])
            self.assertEqual(second_session.request.call_count,1)
            self.assertEqual(second_session.request.call_args.args[0],'GET')
            self.assertFalse(second_session.request.call_args.kwargs['allow_redirects'])

    def test_401_invalidates_private_bearer_without_hidden_refresh_or_secret_logs(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'token.json';session=Mock()
            session.request.side_effect=[response(200,token(200000)),response(401,'SECRET_SERVER_BODY')]
            client=GarminPersonalClient('SECRET_PERSONAL_KEY',path,session=session,clock=lambda:100000)
            with self.assertRaises(GarminPersonalError) as error:client.activity(12)
            self.assertEqual(session.request.call_count,2)
            self.assertEqual(error.exception.status_code,401)
            self.assertNotIn('SECRET',str(error.exception))
            self.assertNotIn('access_token',json.loads(path.read_text()))

    def test_cli_lock_blocks_overlap_before_authentication(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with pipeline_lock(root/'.garmin_personal_sync.lock'),patch.object(garmin_sync,'SCRIPT_DIR',root),patch.object(garmin_sync,'get_garmin_client') as get_client,contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(garmin_sync.main(['--refresh-token']),1)
                get_client.assert_not_called()


if __name__=='__main__':unittest.main()
