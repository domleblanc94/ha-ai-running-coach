from contextlib import closing
import io
from contextlib import redirect_stdout
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from setup_coach import initialize,demo,doctor,main
from planning_engine import parse_profile
from health_status import upload_health
from unittest.mock import Mock,patch

class SetupTests(unittest.TestCase):
    def test_empty_setup_preserves_files_and_does_not_install_fictional_zones(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=initialize(tmp)
            profile=parse_profile((root/"athlete.md").read_text())
            self.assertEqual(profile["zones"],{})
            self.assertEqual(profile["goals"],[])
            (root/".env").write_text("PRIVATE=preserve\n")
            (root/"athlete.md").write_text("Existing private profile")
            initialize(root)
            self.assertEqual((root/".env").read_text(),"PRIVATE=preserve\n")
            self.assertEqual((root/"athlete.md").read_text(),"Existing private profile")
            with closing(sqlite3.connect(root/"ai_coach_master.sqlite")) as conn:self.assertEqual(conn.execute("select count(*) from activities").fetchone()[0],0)
    def test_other_provider_requires_explicit_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):initialize(tmp,provider="openai")

    def test_fresh_gemini_default_and_explicit_existing_model_are_distinct(self):
        from model_provider import DEFAULT_MODELS
        with tempfile.TemporaryDirectory() as tmp:
            root=initialize(tmp)
            self.assertIn('COACH_MODELS='+DEFAULT_MODELS['gemini']+'\n',(root/'.env').read_text())
            (root/'.env').write_text('COACH_PROVIDER=gemini\nCOACH_MODELS=gemini-2.5-flash\nGEMINI_API_KEY=private-placeholder\n',encoding='utf-8')
            before=(root/'.env').read_bytes()
            initialize(root,model=DEFAULT_MODELS['gemini'])
            self.assertEqual((root/'.env').read_bytes(),before)
            output=io.StringIO()
            with patch.dict('os.environ',{},clear=True),redirect_stdout(output):
                self.assertEqual(doctor(root,offline=True),0)
            self.assertIn('deprecated',output.getvalue());self.assertIn('December 1, 2026',output.getvalue())
            self.assertNotIn('private-placeholder',output.getvalue())
            self.assertEqual((root/'.env').read_bytes(),before)

    def test_explicit_model_for_new_setup_is_not_replaced_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=initialize(tmp,model='my-explicit-model')
            self.assertIn('COACH_MODELS=my-explicit-model\n',(root/'.env').read_text())
    def test_synthetic_demo_is_separate_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            target=Path(tmp)/"demo";page=demo(target)
            self.assertIn("SYNTHETIC DEMO",page.read_text(encoding="utf-8"))
            with self.assertRaises(ValueError):demo(target)
    def test_no_publisher_is_valid_local_operation(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict("os.environ",{"COACH_PUBLISH_ENABLED":"false"}):
            runner=Mock();self.assertTrue(upload_health(tmp,runner=runner));runner.assert_not_called()

    def test_doctor_distinguishes_offline_and_missing_live_key_without_secrets(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict("os.environ",{},clear=True):
            root=initialize(tmp)
            text=io.StringIO()
            with redirect_stdout(text):
                self.assertEqual(doctor(root,offline=True),0)
                self.assertEqual(doctor(root),1)
            self.assertIn("API key",text.getvalue())
            with patch.dict("os.environ",{"GEMINI_API_KEY":"private-test-secret"}),redirect_stdout(text):self.assertEqual(doctor(root),0)
            self.assertNotIn("private-test-secret",text.getvalue())

    def test_doctor_rejects_invalid_endpoint_and_official_keyless(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict("os.environ",{},clear=True),redirect_stdout(io.StringIO()):
            root=initialize(tmp,provider="openai",model="chosen-model")
            with patch.dict("os.environ",{"OPENAI_BASE_URL":"not-a-url","OPENAI_API_KEY":"test"}):self.assertEqual(doctor(root),1)
            with patch.dict("os.environ",{"OPENAI_BASE_URL":"https://api.openai.com/v1","COACH_ALLOW_KEYLESS":"true"}):self.assertEqual(doctor(root),1)
            with patch.dict("os.environ",{"OPENAI_BASE_URL":"http://127.0.0.1:11434/v1","COACH_ALLOW_KEYLESS":"true"}):self.assertEqual(doctor(root),0)

    def test_fresh_official_workspace_has_no_old_history_or_credentials(self):
        with tempfile.TemporaryDirectory() as tmp,redirect_stdout(io.StringIO()):
            target=Path(tmp)/'official-coach'
            self.assertEqual(main(['--fresh-garmin-workspace',str(target),'--non-interactive']),0)
            self.assertIn('COACH_ACTIVITY_SOURCE=garmin',(target/'.env').read_text())
            self.assertIn('GARMIN_PERSONAL_API_KEY=\n',(target/'.env').read_text())
            self.assertFalse((target/'training_plan.json').exists())
            self.assertFalse((target/'.garmin_personal_token.json').exists())
            self.assertFalse((target.parent/'official-coach.zip').exists())
            with closing(sqlite3.connect(target/'ai_coach_master.sqlite')) as conn:self.assertEqual(conn.execute('select count(*) from activities').fetchone()[0],0)
