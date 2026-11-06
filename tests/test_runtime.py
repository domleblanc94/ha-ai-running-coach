from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

from ai_running_coach.app.runtime import Runtime, load_calendar_rules
from ai_running_coach.app.scheduler import due_date
from ai_running_coach.app.storage import atomic_json, file_lock


PROFILE = '# Athlete\n**Heart Rate Zones:** Not configured\n**Weekly Availability:** 3 days\n'


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'app-data'
        self.app = Runtime(self.data)

    def setup_profile(self):
        self.app.save_config({'gemini_api_key': 'synthetic-test-key'})
        self.app.save_profile(PROFILE)

    def test_bundled_calendar_load_preserves_unrelated_module_and_import_path(self):
        other = ModuleType('sports')
        original_path = list(sys.path)
        with patch.dict(sys.modules, {'sports': other}):
            runtime = Runtime(self.data.parent / 'second-data')
            conflicts = runtime.calendar_rules.running_session_conflicts({'sessions': [
                {'id': 'bike', 'date': '2026-11-06', 'sport': 'cycling', 'workout_type': 'Intervals'},
                {'id': 'run', 'date': '2026-11-06', 'sport': 'running', 'workout_type': 'Intervals'},
            ]}, '2026-11-06')
            self.assertEqual(conflicts, [])
            self.assertIs(sys.modules['sports'], other)
            self.assertEqual(sys.path, original_path)

    def test_bundled_calendar_import_failure_restores_prior_module(self):
        core = self.data.parent / 'broken-bundle'
        core.mkdir()
        (core / 'sports.py').write_text('VALUE=1\n', encoding='utf-8')
        (core / 'training_calendar.py').write_text('from sports import VALUE\nraise RuntimeError("fixture")\n', encoding='utf-8')
        other = ModuleType('sports')
        with patch.dict(sys.modules, {'sports': other}):
            with self.assertRaisesRegex(RuntimeError, 'fixture'):
                load_calendar_rules(core)
            self.assertIs(sys.modules['sports'], other)

    def fixture_publication(self, html=b'<html>saved plan</html>'):
        now = datetime.now(timezone.utc)
        workspace = self.app.workspace
        plan = workspace / 'training_plan.json'
        db = workspace / 'ai_coach_master.sqlite'
        output = workspace / 'dashboard.html'
        plan.write_text('{}')
        db.touch()
        output.write_bytes(html)
        # A synthetic instant render can share a coarse filesystem clock tick.
        rendered = max(now.timestamp(), plan.stat().st_mtime, db.stat().st_mtime) + 1
        os.utime(output, (rendered, rendered))
        stamp = now.isoformat()
        atomic_json(workspace / 'pipeline_status.json', {'attempted_at': stamp, 'sync_success_at': stamp, 'errors': []})
        return 0

    def test_fresh_setup_config_and_profile_validation_keep_keys_private(self):
        self.assertFalse(self.app.status()['setup_ready'])
        self.assertFalse(self.app.status()['schedule']['enabled'])
        self.assertIsNone(self.app.dashboard())
        with self.assertRaises(ValueError):
            self.app.enqueue('new_plan')
        self.setup_profile()
        original = (self.app.workspace / 'athlete.md').read_bytes()
        status = self.app.save_config({'gemini_api_key': '', 'model': 'gemini-3.5-flash-lite'})
        self.assertTrue(status['setup_ready'])
        self.assertTrue(status['config']['has_gemini_api_key'])
        self.assertNotIn('synthetic-test-key', json.dumps(status))
        self.assertEqual((self.app.workspace / 'athlete.md').read_bytes(), original)
        with self.assertRaises(ValueError):
            self.app.save_config({'PYTHONPATH': 'unsafe'})
        with self.assertRaises(ValueError):
            self.app.save_profile('No zone metadata')
        self.assertEqual((self.app.workspace / 'athlete.md').read_bytes(), original)
        with self.assertRaises(ValueError):
            self.app.set_schedule(True)

    def test_durable_action_and_only_success_replaces_last_good_dashboard(self):
        self.setup_profile()
        self.app.enqueue('new_plan')
        restarted = Runtime(self.data)
        with patch.object(restarted, '_command', side_effect=lambda *_args, **_kwargs: self.fixture_publication()) as command:
            self.assertTrue(restarted.tick())
            command.assert_called_once_with('new_plan', timezone_name='UTC')
        self.assertEqual(restarted.dashboard(), b'<html>saved plan</html>')
        healthy = restarted.health()
        self.assertEqual(healthy['status'], 'ok')
        self.assertEqual(healthy['last_pipeline_success_at'], healthy['last_publish_success_at'])
        restarted.enqueue('run_now')

        def failed(*_args, **_kwargs):
            (self.app.workspace / 'dashboard.html').write_bytes(b'<html>failed render</html>')
            return 1

        with patch.object(restarted, '_command', side_effect=failed):
            self.assertTrue(restarted.tick())
        self.assertEqual(restarted.dashboard(), b'<html>saved plan</html>')
        self.assertEqual(restarted.health()['status'], 'error')
        self.assertEqual(restarted.health()['last_publish_success_at'], healthy['last_publish_success_at'])
        self.assertEqual(restarted.enqueue('new_plan')['queued']['action'], 'new_plan')

    def test_worker_failure_is_visible_without_exposing_exception(self):
        with patch.object(self.app, 'tick', side_effect=ValueError('private detail')):
            self.app.run_worker(Event())
        self.assertFalse(self.app.status()['worker_ok'])
        self.assertEqual(self.app.health()['status'], 'error')
        self.assertNotIn('private detail', json.dumps(self.app.health()))

    def test_manual_plan_passes_saved_timezone_to_core_with_schedule_off(self):
        self.setup_profile()
        self.app.set_schedule(False, '03:00', 'Pacific/Auckland')
        self.app.enqueue('new_plan')
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 1
        process.communicate.return_value = (None, '')
        with patch('ai_running_coach.app.runtime.subprocess.Popen', return_value=process) as launch:
            self.assertTrue(Runtime(self.data).tick())
        environment = launch.call_args.kwargs['env']
        self.assertEqual(environment['TZ'], 'Pacific/Auckland')
        self.assertEqual(launch.call_args.args[0][-1], 'new_plan')
        if sys.platform.startswith('linux'):
            epoch = datetime(2026, 11, 5, 12, tzinfo=timezone.utc).timestamp()
            probe = '''
from datetime import datetime, timezone
import sys
epoch = float(sys.argv[1])
print(datetime.fromtimestamp(epoch).date(), datetime.fromtimestamp(epoch, timezone.utc).date())
'''
            child = subprocess.check_output([sys.executable, '-I', '-c', probe, str(epoch)], env=environment, text=True)
            self.assertEqual(child.strip(), '2026-11-06 2026-11-05')

    def test_interrupted_claim_is_not_retried_and_schedule_pauses(self):
        self.setup_profile()
        self.app.enqueue('new_plan')
        now = datetime.now(timezone.utc)
        self.assertIsNotNone(self.app._claim(now))
        restarted = Runtime(self.data)
        with patch.object(restarted, '_command') as command:
            self.assertFalse(restarted.tick())
            self.assertFalse(restarted.tick())
            command.assert_not_called()
        self.assertEqual(restarted.status()['last_run']['status'], 'interrupted')
        self.assertFalse(restarted.status()['schedule']['enabled'])
        restarted.enqueue('new_plan')

    def test_locked_worker_cannot_claim_queued_action(self):
        self.setup_profile()
        self.app.enqueue('new_plan')
        with file_lock(self.app.run_lock):
            with patch.object(self.app, '_command') as command:
                self.assertFalse(self.app.tick())
                command.assert_not_called()
        self.assertEqual(self.app.status()['queued']['action'], 'new_plan')

    def test_schedule_catches_up_once_only_and_does_not_backfill_missed_days(self):
        self.setup_profile()
        self.app.enqueue('new_plan')
        with patch.object(self.app, '_command', side_effect=lambda *_args, **_kwargs: self.fixture_publication()):
            self.app.tick()
        before = datetime(2026, 11, 5, 1, tzinfo=timezone.utc)
        after = before + timedelta(days=3, hours=3)
        with patch('ai_running_coach.app.runtime.utc_now', return_value=before):
            self.app.set_schedule(True, '03:00', 'UTC')
        restarted = Runtime(self.data)
        with patch('ai_running_coach.app.runtime.utc_now', return_value=after):
            with patch.object(restarted, '_command', return_value=1) as command:
                self.assertTrue(restarted.tick())
                self.assertFalse(restarted.tick())
                command.assert_called_once_with('run_now', timezone_name='UTC')
        self.assertEqual(restarted.status()['schedule']['last_date'], '2026-11-08')

    def test_workspace_install_does_not_touch_private_runtime_files(self):
        self.setup_profile()
        workspace = self.app.workspace
        runtime_files = {'.env': (workspace / '.env').read_bytes(),
                         'athlete.md': PROFILE.encode(), 'ai_coach_master.sqlite': b'preserved',
                         '.garmin_personal_sync.json': b'{"binding":"preserved"}'}
        for name, data in runtime_files.items():
            (workspace / name).write_bytes(data)
        Runtime(self.data)
        for name, data in runtime_files.items():
            self.assertEqual((workspace / name).read_bytes(), data)
        self.assertFalse((workspace / 'tests').exists())

    def test_symlinked_core_runtime_paths_rejected(self):
        path = self.app.workspace / 'training_plan.json'
        target = self.data / 'external.json'
        target.write_text('untouched')
        try:
            path.symlink_to(target)
        except OSError:
            self.skipTest('Symlink creation unavailable on this system')
        with self.assertRaisesRegex(RuntimeError, 'Links are not allowed'):
            Runtime(self.data)
        self.assertEqual(target.read_text(), 'untouched')

    def test_actual_pinned_workspace_dry_run_and_render_without_provider(self):
        self.setup_profile()
        workspace = self.app.workspace
        env = {key: value for key, value in os.environ.items() if key.upper() in {'PATH', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}}
        env['COACH_PUBLISH_ENABLED'] = 'false'
        for arguments in (['database_manager.py'], ['ai_coach.py', '--dry-run', '--new-plan']):
            subprocess.run([sys.executable, *arguments], cwd=workspace, env=env,
                           capture_output=True, text=True, check=True, timeout=30)
        self.assertFalse((workspace / 'training_plan.json').exists())
        create_plan = '''
import json
from datetime import date
from planning_engine import parse_profile, render_plan
from pathlib import Path
profile = parse_profile(Path('athlete.md').read_text())
plan = {'schema_version': 1, 'revision': 1, 'profile_hash': profile['source_hash'],
        'goals': profile['goals'], 'sessions': [{'id': 'synthetic-run', 'date': date.today().isoformat(),
        'workout_type': 'Run', 'amount': '30 minutes', 'target': 'Conversational effort'}]}
Path('training_plan.json').write_text(json.dumps(plan))
Path('training_plan.md').write_text(render_plan(plan))
'''
        subprocess.run([sys.executable, '-c', create_plan], cwd=workspace, env=env,
                       capture_output=True, text=True, check=True, timeout=30)
        render = subprocess.run([sys.executable, 'generate_dashboard.py'], cwd=workspace, env=env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(render.returncode, 0, render.stderr)
        self.assertGreater((workspace / 'dashboard.html').stat().st_size, 1000)


class SchedulerTests(unittest.TestCase):
    def test_dst_gap_and_repeat_are_daily_slots(self):
        schedule = {'enabled': True, 'time': '01:30', 'timezone': 'Europe/London', 'last_date': None}
        self.assertIsNone(due_date(schedule, datetime(2026, 3, 29, 0, 59, tzinfo=timezone.utc)))
        self.assertEqual(due_date(schedule, datetime(2026, 3, 29, 1, tzinfo=timezone.utc)), '2026-03-29')
        self.assertEqual(due_date(schedule, datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)), '2026-10-25')
        schedule['last_date'] = '2026-10-25'
        self.assertIsNone(due_date(schedule, datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc)))


if __name__ == '__main__':
    unittest.main()
