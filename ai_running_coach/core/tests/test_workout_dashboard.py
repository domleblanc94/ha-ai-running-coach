from contextlib import closing
from datetime import date
import json
from pathlib import Path
import re
import shutil
import subprocess
import sqlite3
import tempfile
import unittest
from generate_dashboard import build_dashboard
from planning_engine import parse_profile,render_plan

class WorkoutDashboardTests(unittest.TestCase):
    def test_canonical_steps_embed_safely_and_readonly_with_official_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);profile="**Heart Rate Zones:** Not configured\n"
            (root/"athlete.md").write_text(profile)
            attack="</script><img src=x onerror=alert(1)>"
            session={"id":"run-1","date":"2026-11-04","workout_type":"Easy Run + Strides","amount":"4 km including 4 x 30s strides","target":"Easy effort; stride pace unspecified.","workout":{"name":attack,"sport":"running","steps":[{"name":"Stride","notes":"Fast but comfortable","intensity":"active","duration":{"type":"time","seconds":30},"target":{"type":"open"}}]}}
            plan={"schema_version":1,"revision":1,"profile_hash":parse_profile(profile)["source_hash"],"goals":[],"sessions":[session]}
            (root/"training_plan.json").write_text(json.dumps(plan));(root/"training_plan.md").write_text(render_plan(plan))
            db=root/"db.sqlite"
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute("CREATE TABLE activities(id INTEGER PRIMARY KEY,name,type,start_date,distance_m,training_load,suffer_score)")
                conn.execute("INSERT INTO activities VALUES(1,'Example','Run','2026-11-04',4000,12,999)")
            before=db.read_bytes();html=build_dashboard(db,root/"training_plan.md",root/"dashboard.html",today=date(2026,11,4)).read_text(encoding="utf-8")
            self.assertEqual(before,db.read_bytes())
            payload=json.loads(re.search(r"const workoutSessions = (.*);",html)[1]);self.assertEqual(payload['run-1']['workout'],session['workout'])
            self.assertNotIn(attack,html);self.assertNotRegex(html,r'<script[^>]+src=')
            self.assertIn('window.WorkoutFit',html);self.assertIn('window.WorkoutDownload',html)
            self.assertIn('data-workout-session="run-1"',html)
            loads=json.loads(re.search(r"const loadData = (.*);",html)[1]);self.assertEqual(loads[-1],12);self.assertTrue(all(x is None for x in loads[:-1]))
            self.assertIn('Garmin training load',html)
    def test_editor_is_local_human_readable_and_does_not_execute_prescriptions(self):
        source=(Path(__file__).resolve().parents[1]/'dashboard_assets/workout-ui.js').read_text(encoding='utf-8')
        self.assertNotIn('innerHTML',source);self.assertNotIn('fetch(',source);self.assertNotIn('eval(',source)
        for text in ['Lap button / open','Duration value','Watch target','I reviewed','50 steps','finish manually at the stated overall distance']:
            self.assertIn(text,source)

    @unittest.skipUnless(shutil.which('node'),'Node is optional for browser-library tests')
    def test_running_type_classification_handles_strides_and_run_walk(self):
        source=Path(__file__).resolve().parents[1]/'dashboard_assets/workout-ui.js'
        script="const fs=require('fs'),vm=require('vm');const scope={window:{}};vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);console.log(JSON.stringify(['Easy Run + Strides','Run/Walk','Forest Run','Rest','Walk','Strength','Ride','Trail Hike','Hill Walk'].map(workout_type=>scope.window.WorkoutDownload.eligible({workout_type}))));"
        result=subprocess.run([shutil.which('node'),'-e',script,str(source)],capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout),[True,True,True,False,False,False,True,False,False])
