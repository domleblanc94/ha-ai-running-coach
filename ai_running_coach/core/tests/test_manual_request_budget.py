"""Budget tests use synthetic providers only, including separate-process crashes."""
from contextlib import closing, redirect_stdout
from datetime import date, datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch

import ai_coach
from planning_engine import parse_plan, parse_profile, render_plan


class Unavailable(Exception):
    code = 503


def fixture(root, normal_requests=2, extra_kind=None):
    profile_text = '**Zone 2 (Endurance):** 130 - 150 bpm\n**Active Injuries:** None\n'
    profile = parse_profile(profile_text)
    plan = parse_plan('| Day | '+(date.today()+timedelta(days=1)).isoformat()+' | Run | 5 km | Coach target |', profile)
    paths = {'ATHLETE_FILE': root/'athlete.md', 'PLAN_FILE': root/'training_plan.md',
             'CANONICAL_FILE': root/'training_plan.json', 'DB_PATH': root/'data.sqlite', 'SCRIPT_DIR': root}
    paths['ATHLETE_FILE'].write_text(profile_text, encoding='utf-8')
    paths['PLAN_FILE'].write_text(render_plan(plan), encoding='utf-8')
    paths['CANONICAL_FILE'].write_text(json.dumps(plan), encoding='utf-8')
    with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
        ai_coach.ensure_review_schema(conn)
        conn.execute("ALTER TABLE activities ADD COLUMN source TEXT DEFAULT 'local:fixture'")
        for kind in ['daily']*normal_requests + ([extra_kind] if extra_kind else []):
            conn.execute('INSERT INTO coach_api_requests(job_fingerprint,model,request_kind,started_at,status,error) VALUES(?,?,?,?,?,?)',
                         ('earlier-evidence', 'primary', kind, datetime.now(timezone.utc).isoformat(), 'invalid', 'Earlier invalid response'))
        conn.commit()
    return paths


ENV = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'primary,fallback',
       'COACH_MAX_DAILY_REQUESTS': '2', 'COACH_MAX_ATTEMPTS': '2', 'COACH_ACTIVITY_SOURCE': 'offline'}
REASON = 'User confirmed one additional request in the app.'


def approval():
    return {'manual_extra_attempt': datetime.now(timezone.utc).date().isoformat(), 'manual_reason': REASON}


def response(new_session=False):
    session = {'id': 'manual-first-session', 'date': (date.today()+timedelta(days=1)).isoformat(),
               'workout_type': 'Run', 'amount': '30 min', 'target': 'Conversational effort'}
    payload = {'summary': 'A fresh reviewed block.', 'trajectory': 'insufficient_evidence', 'reviews': [],
               'plan_changes': [{'op': 'add', 'session_id': session['id'], 'session': session, 'reason': 'Current evidence.'}] if new_session else []}
    return types.SimpleNamespace(text=json.dumps(payload), usage_metadata=None)


class ManualBudgetTests(unittest.TestCase):
    def test_extra_supports_review_first_plan_and_new_plan_with_audit(self):
        for mode in ('review', 'first', 'new'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                paths = fixture(Path(directory))
                if mode == 'first':
                    paths['PLAN_FILE'].unlink()
                    paths['CANONICAL_FILE'].unlink()
                generate = Mock(return_value=response(mode != 'review'))
                client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
                with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV, clear=True), patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                    self.assertEqual(ai_coach.run_daily_coach_check(new_plan=mode == 'new', **approval())['status'], 'complete')
                    self.assertEqual(os.environ['COACH_MAX_DAILY_REQUESTS'], '2')
                    with self.assertRaisesRegex(RuntimeError, 'Daily coaching'):
                        ai_coach.run_daily_coach_check('Another distinct question')
                    # A repeated approved action may reuse a completed review, but cannot request again.
                    try:
                        ai_coach.run_daily_coach_check(new_plan=mode == 'new', **approval())
                    except ValueError as exc:
                        self.assertIn('already been used', str(exc))
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(generate.call_args.kwargs['model'], 'primary')
                with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                    rows = ai_coach.daily_coaching_requests(conn)
                    self.assertEqual(len(rows), 3)
                    self.assertEqual(rows[-1][3:], ('complete', None, 'manual_override'))
                    snapshot = json.loads(conn.execute('SELECT input_snapshot_json FROM coach_api_requests ORDER BY id DESC LIMIT 1').fetchone()[0])
                    self.assertEqual(snapshot['manual_approval'], {'date': approval()['manual_extra_attempt'], 'reason': REASON, 'normal_daily_limit': 2})

    def test_failed_extra_is_consumed_without_fallback_or_retry(self):
        for failed_response in (Unavailable('503 UNAVAILABLE'), response()):
            if not isinstance(failed_response, Exception):
                failed_response.text = '{invalid JSON'
            with self.subTest(outcome=type(failed_response).__name__), tempfile.TemporaryDirectory() as directory:
                paths = fixture(Path(directory))
                generate = Mock(side_effect=failed_response) if isinstance(failed_response, Exception) else Mock(return_value=failed_response)
                client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
                originals = {name: paths[name].read_bytes() for name in ('PLAN_FILE', 'CANONICAL_FILE')}
                with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV, clear=True), patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                    with self.assertRaisesRegex(RuntimeError, 'Review remains pending'):
                        ai_coach.run_daily_coach_check(**approval())
                    with self.assertRaisesRegex(ValueError, 'already been used'):
                        ai_coach.run_daily_coach_check(**approval())
                    # Changing the configured cap cannot clear the consumed extra marker.
                    with patch.dict(os.environ, {'COACH_MAX_DAILY_REQUESTS': '5'}):
                        with self.assertRaisesRegex(ValueError, 'already been used'):
                            ai_coach.run_daily_coach_check('New evidence', **approval())
                self.assertEqual(generate.call_count, 1)
                for name, content in originals.items():
                    self.assertEqual(paths[name].read_bytes(), content)

    def test_extra_bypasses_exhausted_pending_job_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = fixture(Path(directory), normal_requests=0)
            generate = Mock(side_effect=[Unavailable('503 UNAVAILABLE'), Unavailable('503 UNAVAILABLE'), response()])
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV, clear=True), patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                self.assertEqual(ai_coach.run_daily_coach_check(**approval())['status'], 'complete')
            self.assertEqual(generate.call_count, 3)

    def test_approval_guards_make_no_provider_call(self):
        cases = [({}, 1, None, 'requires the normal'),
                 ({'manual_extra_attempt': '2000-01-01'}, 2, None, 'current UTC date'),
                 ({'manual_reason': 'short'}, 2, None, 'approval reason'),
                 ({'max_attempts_this_run': 2}, 2, None, 'exactly one'),
                 ({'operator_extra_attempt': approval()['manual_extra_attempt'], 'operator_reason': REASON}, 2, None, 'only one'),
                 ({}, 2, 'operator_recovery', 'already been used'),
                 ({}, 2, 'manual_override', 'already been used')]
        for changes, count, kind, message in cases:
            with self.subTest(message=message, kind=kind), tempfile.TemporaryDirectory() as directory:
                paths = fixture(Path(directory), count, kind)
                with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV, clear=True), patch.object(ai_coach, 'api_client') as client:
                    with self.assertRaisesRegex(ValueError, message):
                        ai_coach.run_daily_coach_check(**(approval() | changes))
                    client.assert_not_called()

    def test_expiry_is_rechecked_when_reserving_after_slow_preparation(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = fixture(Path(directory))
            actual = datetime.now(timezone.utc)
            tomorrow = actual + timedelta(days=1)
            class Clock(datetime):
                current = actual
                @classmethod
                def now(cls, tz=None):
                    return cls.current
            def client_after_midnight():
                Clock.current = tomorrow
                return types.SimpleNamespace(models=types.SimpleNamespace(generate_content=Mock()))
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV, clear=True), patch.object(ai_coach, 'datetime', Clock), patch.object(ai_coach, 'api_client', side_effect=client_after_midnight):
                with self.assertRaisesRegex(ValueError, 'current UTC date'):
                    ai_coach.run_daily_coach_check(**approval())
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM coach_api_requests').fetchone()[0], 2)

    def test_configured_limit_changes_normal_requests_without_rewriting_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = fixture(Path(directory))
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=Mock(return_value=response())))
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV | {'COACH_MAX_DAILY_REQUESTS': '3'}, clear=True), patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                self.assertEqual(ai_coach.run_daily_coach_check()['status'], 'complete')
                with self.assertRaisesRegex(RuntimeError, 'Daily coaching'):
                    ai_coach.run_daily_coach_check('Different evidence')
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual([row[5] for row in ai_coach.daily_coaching_requests(conn)], ['daily']*3)

    def test_raising_normal_cap_allows_explicit_same_evidence_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = fixture(Path(directory), normal_requests=0)
            generate = Mock(side_effect=[Unavailable('503 UNAVAILABLE'), Unavailable('503 UNAVAILABLE'), response()])
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, ENV, clear=True), patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                self.assertEqual(generate.call_count, 2)
                with self.assertRaisesRegex(RuntimeError, 'Daily coaching'):
                    ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                with patch.dict(os.environ, {'COACH_MAX_DAILY_REQUESTS': '3'}):
                    self.assertEqual(ai_coach.run_daily_coach_check(max_attempts_this_run=1)['status'], 'complete')
                self.assertEqual(generate.call_count, 3)
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                rows = ai_coach.daily_coaching_requests(conn)
                self.assertEqual([row[5] for row in rows], ['daily']*3)
                self.assertEqual(len({row[1] for row in rows}), 1)

    def test_large_daily_cap_does_not_increase_per_invocation_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = fixture(Path(directory), normal_requests=0)
            generate = Mock(side_effect=Unavailable('503 UNAVAILABLE'))
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env = ENV | {'COACH_MAX_DAILY_REQUESTS': '20', 'COACH_MODELS': 'primary,fallback,last'}
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, env, clear=True), patch.object(ai_coach, 'api_client', return_value=client), patch.object(ai_coach.time, 'sleep'), redirect_stdout(io.StringIO()):
                with self.assertRaises(RuntimeError):
                    ai_coach.run_daily_coach_check()
                self.assertEqual(generate.call_count, 2)


PROCESS_RUNNER = r'''
import os, socket, sys, time, types
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import ai_coach
root = Path(sys.argv[2]); mode = sys.argv[3]; label = sys.argv[4]
ai_coach.SCRIPT_DIR = root
ai_coach.ATHLETE_FILE = root/'athlete.md'; ai_coach.PLAN_FILE = root/'training_plan.md'
ai_coach.CANONICAL_FILE = root/'training_plan.json'; ai_coach.DB_PATH = root/'data.sqlite'
def forbidden(*args, **kwargs): raise AssertionError('Network forbidden in budget test')
socket.socket = forbidden
class Unavailable(Exception): code = 503
def generate(**kwargs):
    with (root/'calls').open('a') as stream: stream.write('call\n')
    (root/('called-'+label)).touch()
    if mode == 'hold': time.sleep(30)
    raise Unavailable('503 synthetic unavailable')
def client():
    if mode == 'race':
        (root/('ready-'+label)).touch()
        deadline = time.monotonic()+10
        while not (root/'release').exists():
            if time.monotonic()>deadline: raise RuntimeError('Test barrier timeout')
            time.sleep(.02)
    return types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
ai_coach.api_client = client
action = ai_coach._run_daily_coach_check if mode == 'race' else ai_coach.run_daily_coach_check
try:
    action(manual_extra_attempt=sys.argv[5], manual_reason='User explicitly confirmed the extra request.')
except Exception as exc:
    print(type(exc).__name__+': '+str(exc), flush=True)
'''


class ManualProcessBudgetTests(unittest.TestCase):
    def child(self, root, mode, label):
        env = dict(os.environ, **ENV, PYTHONDONTWRITEBYTECODE='1')
        return subprocess.Popen([sys.executable, '-B', '-c', PROCESS_RUNNER, str(Path(ai_coach.__file__).parent),
                                 str(root), mode, label, approval()['manual_extra_attempt']],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)

    def await_file(self, path):
        deadline = time.monotonic()+10
        while not path.exists() and time.monotonic()<deadline:
            time.sleep(.02)
        self.assertTrue(path.exists(), f'Synthetic provider did not reach {path.name}')

    def test_concurrent_action_and_process_restart_cannot_reuse_reserved_extra(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = fixture(root)
            first = self.child(root, 'hold', 'first')
            try:
                self.await_file(root/'called-first')
                duplicate = self.child(root, 'finish', 'duplicate')
                output, error = duplicate.communicate(timeout=10)
                self.assertIn('Another daily pipeline is running', output, error)
            finally:
                first.kill()
                first.communicate(timeout=10)
            restarted = self.child(root, 'finish', 'restarted')
            output, error = restarted.communicate(timeout=10)
            self.assertIn('already been used', output, error)
            self.assertEqual((root/'calls').read_text().splitlines(), ['call'])
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute("SELECT status FROM coach_api_requests WHERE request_kind='manual_override'").fetchall(), [('started',)])

    def test_transaction_rechecks_admission_even_without_outer_process_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = fixture(root)
            children = [self.child(root, 'race', label) for label in ('a', 'b')]
            try:
                self.await_file(root/'ready-a')
                self.await_file(root/'ready-b')
                (root/'release').touch()
                results = [child.communicate(timeout=10) for child in children]
                self.assertEqual(sum('already been used' in output for output, _ in results), 1, results)
                self.assertEqual((root/'calls').read_text().splitlines(), ['call'])
                with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                    self.assertEqual(conn.execute("SELECT count(*) FROM coach_api_requests WHERE request_kind='manual_override'").fetchone()[0], 1)
            finally:
                for child in children:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=10)


if __name__ == '__main__':
    unittest.main()
