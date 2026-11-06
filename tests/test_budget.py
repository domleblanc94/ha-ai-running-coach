"""Manual budget controls, using synthetic ledgers and no provider requests."""

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_running_coach.app.runtime import MANUAL_EXTRA_REASON, Runtime
from ai_running_coach.app import diagnostics
from ai_running_coach.app.server import CoachHandler


PROFILE = '# Athlete\n**Heart Rate Zones:** Not configured\n**Weekly Availability:** 3 days\n'


class BudgetTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.data = Path(temporary.name) / 'data'
        self.app = Runtime(self.data)
        self.app.save_config({'gemini_api_key': 'synthetic-key'})
        self.app.save_profile(PROFILE)
        self.now = datetime.now(timezone.utc)

    def ledger(self, kinds, now=None):
        path = self.app.workspace / 'ai_coach_master.sqlite'
        with closing(sqlite3.connect(path)) as conn, conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS coach_api_requests (
                id INTEGER PRIMARY KEY, started_at TEXT, status TEXT, error TEXT, request_kind TEXT)''')
            conn.executemany('INSERT INTO coach_api_requests (started_at,status,error,request_kind) VALUES (?,?,?,?)',
                             [((now or self.now).isoformat(), 'started', None, kind) for kind in kinds])
        return path

    def approval(self, now=None):
        return {'date': (now or self.now).date().isoformat(), 'confirmed': True}

    def test_limit_default_validation_existing_setting_and_blank_keys(self):
        self.assertEqual(self.app.status()['config']['daily_request_limit'], 2)
        self.app.save_config({'daily_request_limit': '7'})
        restarted = Runtime(self.data)
        status = restarted.save_config({'model': 'gemini-test-model', 'gemini_api_key': ''})
        self.assertEqual(status['config']['daily_request_limit'], 7)
        self.assertTrue(status['config']['has_gemini_api_key'])
        self.assertNotIn('synthetic-key', json.dumps(status))
        self.assertEqual(status['request_budget']['limit'], 7)
        for value in ('0', '21', '-1', '1.5', '2e0', '', ' 2', 2, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                restarted.save_config({'daily_request_limit': value})
        self.assertEqual(restarted.status()['config']['daily_request_limit'], 7)

    def test_existing_duplicate_plan_warning_does_not_change_plan_or_budget(self):
        self.app.save_config({'daily_request_limit': '5'})
        today = (self.now + timedelta(days=1)).date().isoformat()
        tomorrow = (self.now + timedelta(days=4)).date().isoformat()
        another_day = (self.now + timedelta(days=5)).date().isoformat()
        plan = {'schema_version': 1, 'sessions': [
            {'id': 'easy', 'date': today, 'workout_type': 'easy_run', 'amount': '7 km', 'target': 'Easy'},
            {'id': 'long', 'date': today, 'workout_type': 'long_run', 'amount': '16 km', 'target': 'Easy'},
            {'id': 'other-run', 'date': tomorrow, 'workout_type': 'easy_run', 'amount': '5 km', 'target': 'Easy'},
            {'id': 'intervals', 'date': tomorrow, 'workout_type': 'interval_run', 'amount': '9 km', 'target': 'Controlled'},
            {'id': 'another-run', 'date': another_day, 'workout_type': 'easy_run', 'amount': '5 km', 'target': 'Easy'},
            {'id': 'strength', 'date': another_day, 'workout_type': 'Strength', 'amount': '20 minutes', 'target': 'Easy'},
        ]}
        path = self.app.workspace / 'training_plan.json'
        path.write_text(json.dumps(plan))
        before = {file: file.read_bytes() for file in (path, self.app.state_file, self.app.workspace / '.env')}
        with patch.object(self.app, '_command', side_effect=AssertionError('Status must be read only')):
            status = self.app.status()
        self.assertEqual(status['running_session_conflicts'], [
            {'date': today, 'session_ids': ['easy', 'long']},
            {'date': tomorrow, 'session_ids': ['other-run', 'intervals']},
        ])
        self.assertEqual(status['request_budget']['limit'], 5)
        self.assertEqual(status['request_budget']['used'], 0)
        self.assertEqual(before, {file: file.read_bytes() for file in before})

    def test_malformed_private_limit_is_repairable_without_silently_changing_it(self):
        path = self.app.workspace / '.env'
        path.write_text(path.read_text() + 'COACH_MAX_DAILY_REQUESTS="invalid"\n')
        status = self.app.status()
        self.assertFalse(status['setup_ready'])
        self.assertIsNone(status['config']['daily_request_limit'])
        self.assertEqual(status['request_budget']['extra_status'], 'invalid_limit')
        self.assertIn('invalid', path.read_text())
        repaired = self.app.save_config({'daily_request_limit': '4'})
        self.assertTrue(repaired['setup_ready'])
        self.assertEqual(repaired['request_budget']['limit'], 4)

    def test_exact_manual_diagnostics_are_safe_and_provider_quota_remains_distinct(self):
        messages = {
            'Manual extra request approval must name the current UTC date.': 'manual_extra_expired',
            'The manual extra request allowance has already been used for this UTC day.': 'manual_extra_unavailable',
            'Manual extra request requires the normal daily coaching allowance to be exhausted.': 'manual_extra_unavailable',
        }
        for message, expected in messages.items():
            self.assertEqual(diagnostics.command_failure('ERROR:root:ai_coach.py failed: ' + message), expected)
            self.assertEqual(diagnostics.failure_stage(expected), 'coaching')
        self.assertEqual(diagnostics.request_failure('failed', 'gemini API HTTP 429: quota'), 'provider_quota')

    def test_usage_is_readonly_counts_failed_started_and_all_shared_extra_kinds(self):
        path = self.ledger(['daily', 'daily', 'manual_override', 'unrelated'])
        self.ledger(['daily'], self.now - timedelta(days=1))
        before = path.read_bytes()
        with patch.object(self.app, '_command', side_effect=AssertionError('No subprocess')):
            budget = self.app.status()['request_budget']
        self.assertEqual(budget['used'], 3)
        self.assertTrue(budget['extra_used'])
        self.assertFalse(budget['extra_available'])
        self.assertEqual(budget['resets_at'], (self.now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat())
        self.assertEqual(before, path.read_bytes())
        self.app.save_config({'daily_request_limit': '3'})
        self.assertEqual(self.app.status()['request_budget']['extra_status'], 'used')
        with patch('ai_running_coach.app.runtime.utc_now', return_value=self.now + timedelta(days=1)):
            tomorrow = self.app.status()['request_budget']
        self.assertEqual(tomorrow['used'], 0)
        self.assertFalse(tomorrow['extra_used'])

    def test_legacy_recovery_spends_same_extra_and_corrupt_ledger_fails_closed(self):
        path = self.ledger(['daily', 'daily', 'operator_recovery'])
        self.assertFalse(self.app.status()['request_budget']['extra_available'])
        path.write_bytes(b'not a database')
        budget = self.app.status()['request_budget']
        self.assertIsNone(budget['used'])
        self.assertEqual(budget['extra_status'], 'ledger_unavailable')
        with self.assertRaises(ValueError):
            self.app.enqueue('new_plan', self.approval())

    def test_confirmation_required_after_normal_allowance_and_action_is_durable(self):
        with self.assertRaises(ValueError):
            self.app.enqueue('new_plan', self.approval())
        self.ledger(['daily', 'daily'])
        for value in (None, {}, {'date': self.now.date().isoformat()}, {'date': self.now.date().isoformat(), 'confirmed': 1},
                      {'date': self.now.date().isoformat(), 'confirmed': False}, self.approval(self.now - timedelta(days=1))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                CoachHandler.run_action(self.app, {'action': 'new_plan', 'manual_extra': value})
        status = CoachHandler.run_action(self.app, {'action': 'new_plan', 'manual_extra': self.approval()})
        self.assertEqual(status['queued']['manual_extra'], {'approved_day': self.now.date().isoformat(), 'reason': MANUAL_EXTRA_REASON})
        self.assertEqual(Runtime(self.data).status()['queued'], status['queued'])
        with self.assertRaises(RuntimeError):
            self.app.enqueue('new_plan', self.approval())
        self.assertEqual(status['config']['daily_request_limit'], 2)

    def test_expired_queue_does_not_call_core_or_roll_approval_forward(self):
        self.ledger(['daily', 'daily'])
        self.app.enqueue('new_plan', self.approval())
        with patch('ai_running_coach.app.runtime.utc_now', return_value=self.now + timedelta(days=1)), patch.object(self.app, '_command') as command:
            self.assertTrue(self.app.tick())
            self.assertFalse(self.app.tick())
            last = self.app.status()['last_run']
            command.assert_not_called()
        self.assertEqual(last['code'], 'manual_extra_expired')
        self.assertEqual(last['request_attempts'], 0)

    def test_interrupted_reserved_extra_is_not_replayed(self):
        self.ledger(['daily', 'daily'])
        self.app.enqueue('new_plan', self.approval())
        self.app._claim(self.now)
        self.ledger(['manual_override'])
        restarted = Runtime(self.data)
        with patch.object(restarted, '_command') as command:
            self.assertFalse(restarted.tick())
            self.assertFalse(restarted.tick())
            command.assert_not_called()
        self.assertEqual(restarted.status()['last_run']['status'], 'interrupted')
        self.assertTrue(restarted.status()['request_budget']['extra_used'])
        with self.assertRaises(ValueError):
            restarted.enqueue('new_plan', self.approval())

    def test_preflight_failure_leaves_extra_for_new_confirmation_and_learning_is_preserved(self):
        self.ledger(['daily', 'daily'])
        self.app.save_profile(PROFILE + '**HR Zone Learning:** Enabled\n')
        self.app.enqueue('new_plan', self.approval())
        with patch.object(self.app, '_command', return_value=1) as command:
            self.assertTrue(self.app.tick())
            self.assertFalse(self.app.tick())
            command.assert_called_once()
        self.assertEqual(command.call_args.args, ('learn_new_plan',))
        self.assertIn('input_text', command.call_args.kwargs)
        self.assertEqual(command.call_args.kwargs['manual_extra']['approved_day'], self.now.date().isoformat())
        status = self.app.status()
        self.assertEqual(status['last_run']['request_attempts'], 0)
        self.assertTrue(status['request_budget']['extra_available'])
        self.assertIsNone(status['queued'])
        self.assertEqual(status['config']['daily_request_limit'], 2)


if __name__ == '__main__':
    unittest.main()
