"""Local dashboard rebuilds never consume AI allowance or change saved evidence."""
import http.client
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from ai_running_coach.app.runtime import Runtime
from ai_running_coach.app.server import CoachServer
from ai_running_coach.app.storage import atomic_json


class DashboardRefreshTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.app = Runtime(Path(directory.name))
        self.app.save_profile('**Athlete Name:** Synthetic athlete\n**Heart Rate Zones:** Not configured\n')
        self.workspace = self.app.workspace
        self.app.public.joinpath('dashboard.html').write_bytes(b'last-good')

    def fixture(self):
        script = '''
import json, sqlite3
from datetime import date
from pathlib import Path
from planning_engine import parse_profile, render_plan
profile=parse_profile(Path('athlete.md').read_text())
plan={'schema_version':1,'revision':3,'profile_hash':profile['source_hash'],'goals':profile['goals'],
      'sessions':[{'id':'synthetic-run','date':date.today().isoformat(),
                   'workout_type':'Easy run','amount':'5 km','target':'Comfortable effort'}]}
Path('training_plan.json').write_text(json.dumps(plan))
Path('training_plan.md').write_text(render_plan(plan))
with sqlite3.connect('ai_coach_master.sqlite') as conn:
    conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,name TEXT,type TEXT,start_date TEXT,distance_m REAL,timer_duration_s REAL,training_load REAL)')
    conn.execute('INSERT INTO activities VALUES(1,?,?,?,?,?,?)',('Synthetic run','running',date.today().isoformat(),4000,1500,30))
    conn.execute('CREATE TABLE coach_api_requests(id INTEGER PRIMARY KEY,started_at TEXT,status TEXT,error TEXT,request_kind TEXT)')
    conn.execute("INSERT INTO coach_api_requests VALUES(1,'2026-11-27T00:00:00Z','complete',NULL,'daily')")
'''
        env={key:value for key,value in os.environ.items() if key.upper() in {'PATH','SYSTEMROOT','WINDIR','TEMP','TMP'}}
        subprocess.run([sys.executable,'-c',script], cwd=self.workspace, env=env,
                       capture_output=True, text=True, check=True, timeout=30)

    def test_actual_offline_refresh_preserves_inputs_and_coach_state(self):
        self.fixture()
        inputs=['athlete.md','training_plan.json','training_plan.md','ai_coach_master.sqlite']
        before={name:(self.workspace/name).read_bytes() for name in inputs}
        state_before=json.loads(self.app.state_file.read_bytes())
        result=self.app.refresh_dashboard({})
        page=self.app.dashboard()
        self.assertTrue(page.startswith(b'<!DOCTYPE html>'))
        self.assertTrue(b'Synthetic run' in page, 'Stored synthetic activity appears in dashboard')
        self.assertTrue(result['dashboard_revision'])
        for name,content in before.items(): self.assertEqual((self.workspace/name).read_bytes(),content,name)
        state_after=json.loads(self.app.state_file.read_bytes())
        state_after.pop('dashboard_revision')
        self.assertEqual(state_after,state_before)
        self.assertFalse((self.workspace/'dashboard-refresh.html').exists())

    def test_failures_busy_recovery_and_invalid_inputs_keep_last_good(self):
        with self.assertRaises(ValueError): self.app.refresh_dashboard({'action':'run_now'})
        with self.assertRaises(ValueError): self.app.refresh_dashboard({})
        self.fixture()
        state=json.loads(self.app.state_file.read_bytes())
        state['queued']={'action':'run_now'}
        atomic_json(self.app.state_file,state)
        with self.assertRaises(RuntimeError): self.app.refresh_dashboard({})
        state['queued']=None
        atomic_json(self.app.state_file,state)
        (self.workspace/'.plan-commit.json').write_text('{}')
        with self.assertRaises(RuntimeError): self.app.refresh_dashboard({})
        (self.workspace/'.plan-commit.json').unlink()
        for code in (1,0):
            with patch.object(self.app,'_command',return_value=code) as command:
                with self.assertRaises(RuntimeError): self.app.refresh_dashboard({})
                command.assert_called_once_with('render_dashboard',timeout=45,timezone_name='UTC')
            self.assertEqual(self.app.dashboard(),b'last-good')

    def test_render_command_receives_no_provider_credentials(self):
        self.app.save_config({'gemini_api_key':'synthetic-secret'})
        with patch('ai_running_coach.app.runtime.subprocess.Popen') as popen:
            process=popen.return_value.__enter__.return_value
            process.communicate.return_value=(None,None)
            process.returncode=0
            self.app._command('render_dashboard',timeout=45)
            env=popen.call_args.kwargs['env']
            self.assertNotIn('GEMINI_API_KEY',env)
            self.assertNotIn('COACH_PROVIDER',env)

    def test_http_refresh_requires_csrf_and_exact_empty_payload(self):
        server=CoachServer(('127.0.0.1',0),self.app,development=True)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def request(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=20)
            try:
                connection.request(method,path,body,headers or {})
                response=connection.getresponse()
                return response.status,response.read(),dict(response.getheaders())
            finally: connection.close()
        _,body,headers=request('GET','/api/status')
        session={'Cookie':headers['Set-Cookie'].split(';')[0],'X-Coach-CSRF':json.loads(body)['csrf'],
                 'Content-Type':'application/json'}
        self.assertEqual(request('POST','/api/dashboard/refresh','{}',{'Content-Type':'application/json'})[0],403)
        self.assertEqual(request('POST','/api/dashboard/refresh','{"unexpected":true}',session)[0],400)
        with patch.object(self.app,'refresh_dashboard',return_value={'dashboard_revision':'synthetic'}) as refresh:
            self.assertEqual(request('POST','/api/dashboard/refresh','{}',session)[0],200)
            refresh.assert_called_once_with({})
        self.assertEqual(request('GET','/assets/graph-preferences.js')[0],200)


if __name__ == '__main__': unittest.main()
