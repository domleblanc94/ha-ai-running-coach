import contextlib
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import shlex
import sqlite3
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import health_status as health
import cron_daily_update as pipeline

class HealthTests(unittest.TestCase):
    def project(self,root):
        p=root/'project';p.mkdir()
        script=p/'upload.sh';script.write_text('scp -i ~/.ssh/test_key '+(p/'dashboard.html').as_posix()+' root@ha:/config/www/dashboard.html',encoding='utf-8')
        return p,script

    def test_running_or_unfinished_attempt_cannot_inherit_green(self):
        now=datetime.now(timezone.utc)
        state={'updated_at':now.isoformat(),'phase':'coaching','last_pipeline_attempt_at':now.isoformat(),'last_pipeline_success_at':(now-timedelta(hours=1)).isoformat(),'last_publish_success_at':(now-timedelta(hours=1)).isoformat()}
        self.assertEqual(health.public_state(state)['status'],'running')
        state['phase']='ready'
        self.assertEqual(health.public_state(state)['status'],'warning')
        state['phase']='awaiting_publish'
        self.assertEqual(health.public_state(state)['status'],'warning')

    def test_failed_pipeline_delivers_status_without_replacing_ha_dashboard(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p,script=self.project(root);ha=root/'ha';ha.mkdir();(ha/'dashboard.html').write_text('last-good')
            def transport(args,**kwargs):
                if args[0]=='scp':shutil.copy2(args[-2],ha/Path(args[-1].split(':',1)[1]).name)
                else:
                    command=shlex.split(args[-1]);(ha/Path(command[-2]).name).replace(ha/Path(command[-1]).name)
                return subprocess.CompletedProcess(args,0)
            def publish_status(project):return health.upload_health(project,script,runner=transport)
            with patch.object(pipeline,'SCRIPT_DIR',p),patch.object(pipeline,'run_script',side_effect=[1,1,0,0]),patch.object(pipeline,'upload_health',side_effect=publish_status):
                self.assertEqual(pipeline.run_pipeline(),1)
            status=json.loads((ha/'coach-status.json').read_text())
            self.assertEqual(status['status'],'error')
            self.assertTrue(status['issues'])
            self.assertEqual((ha/'dashboard.html').read_text(),'last-good')
            self.assertFalse(status['last_pipeline_success_at'])

    def test_truncation_reports_safe_detail_without_changing_retry_state(self):
        with tempfile.TemporaryDirectory() as d:
            project, _ = self.project(Path(d))
            started = datetime.now(timezone.utc) + timedelta(seconds=2)
            with contextlib.closing(sqlite3.connect(project / "ai_coach_master.sqlite")) as db:
                db.execute("CREATE TABLE coach_api_requests(id INTEGER PRIMARY KEY,status,error,started_at,request_kind)")
                db.execute(
                    "INSERT INTO coach_api_requests VALUES(1,'invalid',?,?,'daily')",
                    ("Model output was truncated by its token budget; last-good plan retained.", started.isoformat()),
                )
                db.commit()
            with (
                patch.object(pipeline, "SCRIPT_DIR", project),
                patch.object(pipeline, "activity_source_mode", return_value="garmin"),
                patch.object(pipeline, "run_script", side_effect=[2, 0, 1, 0]),
                patch.object(pipeline, "upload_health", return_value=True),
            ):
                self.assertEqual(pipeline.run_pipeline(), 1)
            sidecar = json.loads((project / "coach-status.json").read_text())
            self.assertEqual(sidecar["status"], "error")
            self.assertEqual(sidecar["issues"], ["AI response exceeded its output limit; last published dashboard retained"])
            self.assertEqual(
                json.loads((project / "pipeline_status.json").read_text())["errors"],
                ["Coach review pending; previous review retained"],
            )
            self.assertIsNone(pipeline.coach_failure_message(project, (started + timedelta(seconds=1)).isoformat()))

    def test_status_transport_failures_persist_and_never_expose_commands(self):
        with tempfile.TemporaryDirectory() as d:
            p,script=self.project(Path(d))
            def failing(*args,**kwargs):raise subprocess.CalledProcessError(1,['scp'],stderr='private transport detail')
            self.assertFalse(health.upload_health(p,script,runner=failing))
            saved=(p/'coach-status.json').read_text()
            self.assertIn('Live status could not be delivered',saved)
            self.assertNotIn('private transport detail',saved)

    def test_transport_rejects_shell_construction_and_other_sources(self):
        with tempfile.TemporaryDirectory() as d:
            p,script=self.project(Path(d))
            for text in ['scp other.html root@ha:/config/www/dashboard.html','scp '+(p/'dashboard.html').as_posix()+' root@ha:/config/www/dashboard.html; evil','echo hi\nscp '+(p/'dashboard.html').as_posix()+' root@ha:/config/www/dashboard.html']:
                script.write_text(text)
                with self.assertRaises(ValueError):health.transport_from_script(script,p)

    def test_unexpected_runtime_failure_after_lock_reports_health(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)
            with patch.object(pipeline,'SCRIPT_DIR',p),patch.object(pipeline,'run_pipeline',side_effect=RuntimeError('failure inside work')),patch.object(pipeline,'upload_health',return_value=True) as delivered, patch.object(pipeline,'RotatingFileHandler',return_value=__import__('logging').NullHandler()):
                with self.assertLogs(level='ERROR'):
                    self.assertEqual(pipeline.main(),1)
            self.assertTrue(delivered.called)
            self.assertEqual(json.loads((p/'coach-status.json').read_text())['phase'],'failed')
