from contextlib import closing, redirect_stdout
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import ai_coach
from planning_engine import (apply_coach_changes, apply_plan_changes, fingerprint,
                             parse_profile, render_plan)
from training_calendar import training_days, training_day_conflicts


TODAY = '2026-11-06'
BASE_PROFILE = '**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n'
SELECTED = '**Weekly Availability:** Monday, Wednesday, Friday\n'


def session(identifier, day, kind='Easy run', **extra):
    return {'id': identifier, 'date': day, 'workout_type': kind,
            'amount': '30 minutes', 'target': 'Comfortable effort', **extra}


def plan_for(profile, sessions):
    return {'schema_version': 1, 'revision': 1, 'profile_hash': profile['source_hash'],
            'goals': profile['goals'], 'sessions': sessions}


def addition(value):
    return {'op': 'add', 'session_id': value['id'], 'session': value,
            'reason': 'Use the selected training days.'}


class TrainingAvailabilityTests(unittest.TestCase):
    def test_missing_and_legacy_free_text_are_not_zero_availability(self):
        for raw in ('', '3 days', 'Monday or Tuesday', 'Not set; coach may choose days'):
            with self.subTest(raw=raw):
                profile = parse_profile(BASE_PROFILE + '**Weekly Availability:** ' + raw + '\n')
                self.assertIsNone(training_days(profile))
                self.assertFalse(training_day_conflicts(
                    plan_for(profile, [session('sunday', '2026-11-08')]), profile, TODAY))
        profile = parse_profile(BASE_PROFILE + '**Weekly Availability:** friday, Monday and Wednesday\n')
        self.assertEqual(training_days(profile), ['Monday', 'Wednesday', 'Friday'])

    def test_future_training_is_rejected_but_rest_and_history_are_preserved(self):
        profile = parse_profile(BASE_PROFILE + SELECTED)
        old = session('past-sunday', '2026-11-01')
        plan = plan_for(profile, [old, session('future-sunday', '2026-11-08'),
                                  session('monday', '2026-11-09')])
        original = copy.deepcopy(plan)
        with self.assertRaisesRegex(ValueError, 'Training-day conflict:.*Sunday'):
            apply_plan_changes(plan, [], profile, TODAY)
        self.assertEqual(plan, original)
        for kind in ('Rest', 'Rest day', 'Off', 'Full rest'):
            with self.subTest(kind=kind):
                resting = copy.deepcopy(plan)
                resting['sessions'][1]['workout_type'] = kind
                self.assertFalse(training_day_conflicts(resting, profile, TODAY))
                result = apply_plan_changes(resting, [], profile, TODAY)
                self.assertEqual(result['sessions'][0], old)
        for kind in ('Recovery run', 'Active recovery', 'Mobility', 'Rest / Easy run'):
            with self.subTest(kind=kind):
                active = plan_for(profile, [session('active', '2026-11-08', kind)])
                self.assertEqual(len(training_day_conflicts(active, profile, TODAY)), 1)
        disguised = plan_for(profile, [session('active', '2026-11-08', 'Rest', workout={})])
        self.assertEqual(len(training_day_conflicts(disguised, profile, TODAY)), 1)

    def test_only_actual_configured_race_is_an_exception(self):
        profile = parse_profile(BASE_PROFILE + SELECTED +
                                '**A-Race:** Main event\n**Date:** 2026-11-08\n')
        race = session('race', '2026-11-08', 'Main event race', goal_priority='A-Race')
        self.assertFalse(training_day_conflicts(plan_for(profile, [race]), profile, TODAY))
        for kind in ('Race-pace run', 'Race effort', 'Race simulation', 'Race prep', 'Easy run'):
            with self.subTest(kind=kind):
                training = dict(race, workout_type=kind)
                self.assertTrue(training_day_conflicts(plan_for(profile, [training]), profile, TODAY))
        milestone = copy.deepcopy(profile)
        milestone['goals'][0]['event'] = 'Training milestone: Run consistently'
        self.assertTrue(training_day_conflicts(plan_for(milestone, [race]), milestone, TODAY))
        unknown = copy.deepcopy(profile)
        unknown['goals'] = []
        self.assertTrue(training_day_conflicts(plan_for(unknown, [race]), unknown, TODAY))

    def test_prompt_lists_constraints_and_conflicts_and_changes_cache_identity(self):
        profile = parse_profile(BASE_PROFILE + SELECTED)
        plan = plan_for(profile, [session('sunday', '2026-11-08')])
        evidence = {'today': TODAY, 'window_start': '2026-10-10', 'recent_7d': []}
        prompt = ai_coach.make_prompt(profile, plan, evidence, 'Review my plan')
        data = json.loads(prompt.split('\nDATA:\n', 1)[1])
        self.assertEqual(data['allowed_training_days'], ['Monday', 'Wednesday', 'Friday'])
        self.assertEqual(data['training_day_conflicts'], [
            {'session_id': 'sunday', 'date': '2026-11-08', 'weekday': 'Sunday'}])
        self.assertIn('Correct every supplied training_day_conflicts', prompt)
        old = {'provider': 'gemini', 'prompt_version': 'autonomous-coach-v3'}
        current = dict(old, prompt_version=ai_coach.PROMPT_VERSION)
        self.assertNotEqual(fingerprint(profile, plan, evidence, 'Review', old),
                            fingerprint(profile, plan, evidence, 'Review', current))

    def test_changed_profile_replacement_is_atomic_and_retires_old_future_days(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_profile = parse_profile(BASE_PROFILE)
            historic = session('past-sunday', '2026-11-01')
            old_plan = plan_for(old_profile, [historic, session('old-sunday', '2026-11-08')])
            paths = {'ATHLETE_FILE': root/'athlete.md', 'PLAN_FILE': root/'training_plan.md',
                     'CANONICAL_FILE': root/'training_plan.json', 'DB_PATH': root/'data.sqlite'}
            paths['ATHLETE_FILE'].write_text(BASE_PROFILE, encoding='utf-8')
            paths['PLAN_FILE'].write_text(render_plan(old_plan), encoding='utf-8')
            paths['CANONICAL_FILE'].write_text(json.dumps(old_plan), encoding='utf-8')
            original = {path: path.read_bytes() for path in (paths['PLAN_FILE'], paths['CANONICAL_FILE'])}
            invalid = {'summary': 'Synthetic off-day response.', 'trajectory': 'insufficient_evidence',
                       'reviews': [], 'plan_changes': [addition(session('new-sunday', '2026-11-08'))]}
            valid = {**invalid, 'summary': 'Synthetic response honoring selected days.',
                     'plan_changes': [addition(session('new-monday', '2026-11-09'))]}
            generate = Mock(side_effect=[types.SimpleNamespace(text=json.dumps(value), usage_metadata=None)
                                         for value in (invalid, valid)])
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            environment = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'synthetic-model',
                           'COACH_ACTIVITY_SOURCE': 'offline', 'COACH_MAX_DAILY_REQUESTS': '2'}
            with patch.multiple(ai_coach, **paths), patch.object(ai_coach, 'SCRIPT_DIR', root), \
                    patch.dict(os.environ, environment, clear=True), \
                    patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                before = ai_coach.run_daily_coach_check(dry_run=True, today=TODAY, new_plan=True)
                paths['ATHLETE_FILE'].write_text(BASE_PROFILE + SELECTED, encoding='utf-8')
                after = ai_coach.run_daily_coach_check(dry_run=True, today=TODAY, new_plan=True)
                self.assertNotEqual(before['fingerprint'], after['fingerprint'])
                self.assertIn('"allowed_training_days": ["Monday", "Wednesday", "Friday"]', after['prompt'])
                with self.assertRaisesRegex(RuntimeError, 'Training-day conflict:'):
                    ai_coach.run_daily_coach_check(today=TODAY, new_plan=True, max_attempts_this_run=1)
                self.assertEqual(original, {path: path.read_bytes() for path in original})
                self.assertFalse((root/'.plan-commit.json').exists())
                result = ai_coach.run_daily_coach_check(today=TODAY, new_plan=True, max_attempts_this_run=1)
                self.assertEqual(result['status'], 'complete')
            self.assertEqual(generate.call_count, 2)
            saved = json.loads(paths['CANONICAL_FILE'].read_text(encoding='utf-8'))
            self.assertEqual(saved['sessions'], [historic, valid['plan_changes'][0]['session']])
            self.assertIn('old-sunday', saved['retired_session_ids'])
            self.assertEqual(saved['superseded_blocks'][-1]['sessions'], [old_plan['sessions'][1]])
            self.assertFalse(training_day_conflicts(saved, parse_profile(BASE_PROFILE + SELECTED), TODAY))
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT status FROM coach_api_requests ORDER BY id').fetchall(),
                                 [('invalid',), ('complete',)])


if __name__ == '__main__':
    unittest.main()
