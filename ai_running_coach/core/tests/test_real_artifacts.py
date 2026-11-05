"""Opt-in end-to-end replay: set COACH_FIXTURE_DIR to private read-only input copies."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = os.getenv("COACH_FIXTURE_DIR")

@unittest.skipUnless(FIXTURES, "Set COACH_FIXTURE_DIR for private artifact replay")
class ArtifactReplay(unittest.TestCase):
    def test_offline_migration_dry_run_and_render(self):
        sources = [Path(FIXTURES) / name for name in ("ai_coach_master.sqlite", "athlete.md", "training_plan.md")]
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in sources}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for source in ROOT.glob("*.py"):
                shutil.copy2(source,target/source.name)
            shutil.copytree(ROOT/"dashboard_assets",target/"dashboard_assets")
            for source in sources:
                shutil.copy2(source,target/source.name)
            env = dict(os.environ, PYTHONIOENCODING="utf-8")
            env.pop("GEMINI_API_KEY",None)
            def command(*args):
                result = subprocess.run([sys.executable,*args],cwd=target,env=env,capture_output=True,text=True,encoding="utf-8",timeout=45)
                self.assertEqual(result.returncode,0,result.stderr)
                return result.stdout
            preview = json.loads(command("migrate_plan.py"))
            self.assertFalse(preview["applied"])
            self.assertFalse((target/"training_plan.json").exists())
            self.assertTrue(preview["conflicts"])
            applied = json.loads(command("migrate_plan.py","--apply"))
            self.assertTrue(applied["applied"])
            plan_before = (target/"training_plan.json").read_bytes()
            db_before = (target/"ai_coach_master.sqlite").read_bytes()
            dry = json.loads(command("ai_coach.py","--dry-run","--today","2026-10-31"))
            self.assertEqual(dry["status"],"dry_run")
            self.assertEqual(plan_before,(target/"training_plan.json").read_bytes())
            self.assertEqual(db_before,(target/"ai_coach_master.sqlite").read_bytes())
            self.assertNotIn("123-143",(target/"training_plan.md").read_text(encoding="utf-8"))
            (target/"training_plan.md").write_text("tampered manual Markdown",encoding="utf-8")
            command("generate_dashboard.py")
            output = (target/"dashboard.html").read_text(encoding="utf-8")
            self.assertNotIn('<script src=',output)
            self.assertIn("safeMarkdown",output)
            self.assertIn("Plan revision",output)
            self.assertIn('2026-10-26',output)
            self.assertNotIn("tampered manual Markdown",output)
            self.assertEqual(db_before,(target/"ai_coach_master.sqlite").read_bytes())
            # Exercise actual commit/storage/render integration with a synthetic model,
            # never a paid/network API. This schedule exists only inside the temp copy.
            stub = r'''import json,sys,types
from pathlib import Path
import ai_coach
plan=json.loads(Path("training_plan.json").read_text(encoding="utf-8"))
from datetime import datetime
profile=ai_coach.parse_profile(Path("athlete.md").read_text(encoding="utf-8"))
primary=next(goal for goal in profile["goals"] if goal["priority"]=="A-Race")
race_date=datetime.strptime(primary["date"],"%B %d, %Y").date().isoformat()
changes=[]
for session in plan["sessions"]:
    if session["date"] >= "2026-10-31":
        session=dict(session,target="Synthetic fixture target; not athlete guidance.")
        if not changes: session.update(workout_type="Fixture tempo",amount="6 km")
        if "race" in session["workout_type"].lower():session["date"]=race_date
        changes.append({"op":"update","session_id":session["id"],"session":session,"reason":"Synthetic coaching adaptation for integration test"})
payload={"summary":"Synthetic integration review with uncertainty, not live coaching.","trajectory":"insufficient_evidence","reviews":[],"plan_changes":changes}
types_module=types.ModuleType("google.genai.types")
types_module.GenerateContentConfig=lambda **kwargs:kwargs
types_module.ThinkingConfig=lambda **kwargs:kwargs
google=types.ModuleType("google");genai=types.ModuleType("google.genai");genai.types=types_module
sys.modules.update({"google":google,"google.genai":genai,"google.genai.types":types_module})
calls=[]
def generate(**kwargs):
    calls.append(kwargs)
    return types.SimpleNamespace(text=json.dumps(payload),usage_metadata=None)
ai_coach.api_client=lambda:types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
result=ai_coach.run_daily_coach_check(today="2026-10-31")
assert result["status"]=="complete"
assert result["applied_revision"]==2
assert not Path(".plan-commit.json").exists()
assert "Fixture tempo" in Path("training_plan.md").read_text(encoding="utf-8")
assert ai_coach.run_daily_coach_check(today="2026-10-31")["status"]=="unchanged"
assert len(calls)==1
'''
            (target/"synthetic_coach_test.py").write_text(stub,encoding="utf-8")
            command("synthetic_coach_test.py")
            command("generate_dashboard.py")
            revised=(target/"dashboard.html").read_text(encoding="utf-8")
            self.assertIn("Applied plan changes (revision 2)",revised)
            self.assertIn("Fixture tempo",revised)
            self.assertIn("Synthetic coaching adaptation",revised)
        for source in sources:
            self.assertEqual(before[source],hashlib.sha256(source.read_bytes()).hexdigest())

if __name__ == "__main__":
    unittest.main()
