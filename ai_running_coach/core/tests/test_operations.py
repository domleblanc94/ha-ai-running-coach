import importlib.util
import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import generate_dashboard as dashboard
import cron_daily_update as pipeline

PLAN = "| Day | Date | Workout Type | Distance / Duration | Target |\n| Monday | 2026-10-26 | Easy Run | 5 km | <script>alert(1)</script> |\n| Monday | 2026-10-26 | Strength | 30 min | Easy |"

class OperationsTests(unittest.TestCase):
    def test_render_all_same_day_activities_and_escape_without_external_scripts(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            db = base / "data.sqlite"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("CREATE TABLE activities(id INTEGER PRIMARY KEY,name TEXT,type TEXT,start_date TEXT,distance_m REAL,ai_feedback TEXT)")
                conn.executemany("INSERT INTO activities VALUES(?,?,?,?,?,?)", [
                    (1,"Run </script><img src=x onerror=alert(1)>","Run","2026-10-26T08:00:00",5000,None),
                    (2,"Evening swim","Swim","2026-10-26T18:00:00",1000,"Nice swim"),
                    (4,"Unscheduled jog","Run","2026-10-25T08:00:00",2000,None),
                    (3,"Old private activity","Ride","2017-01-01",1000,None)])
                conn.commit()
            plan = base / "plan.md"
            plan.write_text(PLAN,encoding="utf-8")
            before = db.read_bytes()
            out = dashboard.build_dashboard(db,plan,base / "page.html")
            html = out.read_text(encoding="utf-8")
            self.assertEqual(before,db.read_bytes())
            self.assertIn("Evening swim",html)
            self.assertIn("Unscheduled jog",html)
            self.assertIn("Other recent recorded activities",html)
            self.assertIn("Nice swim",html)
            self.assertNotIn("Old private activity",html)
            self.assertNotRegex(html,r'<script[^>]+src=')
            self.assertNotIn("<img src=x onerror=alert(1)>",html)
            activities=json.loads(re.search(r"const completedActivities = (.*);",html)[1])
            self.assertNotIn("suffer_score",activities["2026-10-26"][0])
            self.assertNotIn('<link rel="stylesheet" href="http',html)
            self.assertEqual(len(dashboard.parse_sessions(PLAN)),2)
            self.assertIn("const isCompleted = isManualChecked",html)
            self.assertNotIn("<script>alert(1)</script>",html)

    def test_no_new_data_still_processes_pending_work(self):
        with patch.dict("os.environ",{"COACH_ACTIVITY_SOURCE":"garmin"}), patch.object(pipeline,"run_script",side_effect=[2,0,0,0]) as run, patch.object(pipeline,"write_status"), patch.object(pipeline,"update_health"), patch.object(pipeline,"upload_health",return_value=True):
            self.assertEqual(pipeline.run_pipeline(),0)
            self.assertEqual([call.args[0] for call in run.call_args_list],
                ["garmin_sync.py","database_manager.py","ai_coach.py","generate_dashboard.py"])

    def test_review_failure_still_renders_and_fails_publish_gate(self):
        with patch.dict("os.environ",{"COACH_ACTIVITY_SOURCE":"garmin"}), patch.object(pipeline,"run_script",side_effect=[0,0,1,0]) as run, patch.object(pipeline,"write_status"), patch.object(pipeline,"update_health"), patch.object(pipeline,"upload_health",return_value=True):
            self.assertEqual(pipeline.run_pipeline(),1)
            self.assertEqual([call.args[0] for call in run.call_args_list].count("ai_coach.py"),1)
            self.assertEqual(run.call_args_list[-1].args[0],"generate_dashboard.py")

    def test_overlap_lock_rejects_second_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lock"
            with pipeline.pipeline_lock(path):
                with self.assertRaises(RuntimeError):
                    with pipeline.pipeline_lock(path):
                        pass

    def test_pending_plan_commit_preserves_last_published_dashboard(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            plan=base/"training_plan.md"
            plan.write_text(PLAN,encoding="utf-8")
            output=base/"dashboard.html"
            output.write_text("last good",encoding="utf-8")
            (base/".plan-commit.json").write_text("{}",encoding="utf-8")
            with self.assertRaises(RuntimeError):
                dashboard.build_dashboard(base/"missing.sqlite",plan,output)
            self.assertEqual(output.read_text(encoding="utf-8"),"last good")

    def test_applied_ai_changes_display_revision_and_reason(self):
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/"data.sqlite"
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("CREATE TABLE coach_review_jobs(status TEXT,updated_at TEXT,response_json TEXT,prompt_version TEXT)")
                payload={"trajectory":"on_track","reviews":[],"applied_revision":2,"plan_changes":[{"op":"update","session_id":"session-2026-10-26","reason":"Adapted after the recent run"}]}
                conn.execute("INSERT INTO coach_review_jobs VALUES(?,?,?,?)",("complete","2026-10-26",json.dumps(payload),"autonomous-coach-v2"))
                conn.commit()
            activities,review,reviewed_at=dashboard.read_database(db,"2026-10-01","2026-11-01")
            self.assertIn("Applied plan changes (revision 2)",review)
            self.assertIn("Adapted after the recent run",review)
            self.assertIn("on_track",review)
            self.assertNotIn("review only",review)

    def test_atomic_failure_keeps_last_good_html(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            plan=base/"plan.md"
            plan.write_text(PLAN,encoding="utf-8")
            output=base/"page.html"
            output.write_text("last good",encoding="utf-8")
            with patch.object(dashboard.os,"replace",side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    dashboard.build_dashboard(base/"missing.sqlite",plan,output)
            self.assertEqual(output.read_text(),"last good")
            self.assertFalse(list(base.glob("*.tmp")))

if __name__ == "__main__":
    unittest.main()
