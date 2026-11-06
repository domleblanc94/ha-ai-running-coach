"""The AI chooses replacements; validation rejects accidental stacked runs."""
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
from planning_engine import apply_plan_changes, apply_coach_changes, parse_profile, render_plan, fingerprint
from training_calendar import planned_running_session, preferred_long_run_days, running_session_conflicts


TODAY = '2026-11-06'
PROFILE = '**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n**Weekly Availability:** Monday, Tuesday, Thursday, Friday, Saturday\n'


def session(identifier, day, kind, amount='7 km', **extra):
    return {'id': identifier, 'date': day, 'workout_type': kind, 'amount': amount,
            'target': 'Coach-selected effort', **extra}


def plan_for(profile, sessions):
    return {'schema_version': 1, 'revision': 2, 'profile_hash': profile['source_hash'],
            'goals': profile['goals'], 'sessions': sessions}


def change(value, op='update'):
    return {'op': op, 'session_id': value['id'], 'session': value,
            'reason': 'Fit the complete week to the athlete schedule.'}


def removal(identifier):
    return {'op': 'delete', 'session_id': identifier, 'session': None,
            'reason': 'Replace this run with the main session; avoid a duplicate run.'}


def original_sessions():
    return [session('sat-easy', '2026-11-07', 'easy_run'),
            session('sun-long', '2026-11-08', 'long_run', '16 km'),
            session('tue-easy', '2026-11-10', 'easy_run', '8 km'),
            session('wed-intervals', '2026-11-11', 'interval_run', '9 km')]


def moved_sessions():
    before = original_sessions()
    return [change(dict(before[1], date='2026-11-07')), change(dict(before[3], date='2026-11-10'))]


class RunningScheduleTests(unittest.TestCase):
    def test_optional_long_run_alternatives_preserve_legacy_singular_and_never_invent_day(self):
        for extra, expected in [('', None), ('**Preferred Long Run Day:** Saturday\n', ['Saturday']),
                                ('**Preferred Long Run Days:** Friday, Saturday\n', ['Friday', 'Saturday']),
                                ('**preferred long run days:** Saturday or Sunday\n', ['Saturday', 'Sunday']),
                                ('**Preferred Long Run Days:** \n', None),
                                ('**Preferred Long Run Day:** Sunday\n**Preferred Long Run Days:** Friday, Saturday\n', ['Friday', 'Saturday'])]:
            with self.subTest(extra=extra):
                self.assertEqual(preferred_long_run_days(parse_profile(PROFILE+extra)), expected)

    def test_planned_running_classifier_covers_labels_and_structured_running_without_strength_false_positives(self):
        for kind in ('easy_run', 'long_run', 'Tempo', 'Intervals', 'Hill repeats', 'Trail', 'Race',
                     'Recovery run', 'Run/Walk', 'Easy Run + Strides', 'Running + strength',
                     'TrailRun', 'LongRun', 'EasyRun', 'longrun', 'trailrunning',
                     'Easy', 'Long', 'Recovery', 'Progression', 'Aerobic'):
            with self.subTest(kind=kind):
                self.assertTrue(planned_running_session({'workout_type': kind}))
        for kind in ('Strength', 'Cycling intervals', 'Swim tempo', 'Ride', 'Yoga', 'Trail hike',
                     'Hill walk', 'Rest', 'Mobility for runners', 'Rowing intervals', 'Bike intervals', 'Cross-training intervals',
                     'Post-run mobility', 'Post-run strength', 'Pre-run mobility', 'PostRunMobility',
                     'Strength for running', 'Recovery strength', 'TempoRide', 'Easy cycling'):
            with self.subTest(kind=kind):
                self.assertFalse(planned_running_session({'workout_type': kind}))
        self.assertTrue(planned_running_session({'workout_type': 'Baseline assessment', 'workout': {'sport': 'running'}}))
        self.assertTrue(planned_running_session({'workout_type': 'Post-run mobility', 'workout': {'sport': 'running'}}))

    def test_off_day_moves_cannot_stack_on_unchanged_existing_runs(self):
        profile = parse_profile(PROFILE)
        plan = plan_for(profile, original_sessions())
        before = copy.deepcopy(plan)
        with self.assertRaisesRegex(ValueError, 'Multiple-running-session conflict:.*2026-11-07.*2026-11-10'):
            apply_plan_changes(plan, moved_sessions(), profile, TODAY)
        self.assertEqual(plan, before)
        corrected = apply_plan_changes(plan, moved_sessions()+[removal('sat-easy'), removal('tue-easy')], profile, TODAY)
        self.assertEqual([(item['id'], item['date'], item['amount']) for item in corrected['sessions']],
                         [('sun-long', '2026-11-07', '16 km'), ('wed-intervals', '2026-11-10', '9 km')])
        self.assertEqual(corrected['retired_session_ids'], ['sat-easy', 'tue-easy'])

    def test_existing_duplicates_must_be_reconciled_even_with_no_changes(self):
        profile = parse_profile(PROFILE)
        sessions = [session('one', TODAY, 'easy_run'), session('two', TODAY, 'Tempo')]
        plan = plan_for(profile, sessions)
        self.assertEqual(running_session_conflicts(plan, TODAY), [{'date': TODAY, 'session_ids': ['one', 'two']}])
        with self.assertRaisesRegex(ValueError, 'Multiple-running-session conflict:'):
            apply_plan_changes(plan, [], profile, TODAY)

    def test_strength_can_share_date_and_historical_double_runs_remain_unchanged(self):
        profile = parse_profile(PROFILE)
        sessions = [session('past-one', '2026-11-05', 'Easy run'), session('past-two', '2026-11-05', 'Tempo'),
                    session('current-run', TODAY, 'easy_run'), session('current-strength', TODAY, 'Strength', '20 min')]
        plan = plan_for(profile, sessions)
        result = apply_plan_changes(plan, [], profile, TODAY)
        self.assertFalse(running_session_conflicts(result, TODAY))
        self.assertEqual(result['sessions'], sorted(sessions, key=lambda item: (item['date'], item['id'])))

    def test_long_run_preference_does_not_override_availability_or_choose_ai_session(self):
        # A legacy Markdown preference may conflict with later selected days.
        profile = parse_profile(PROFILE+'**Preferred Long Run Day:** Sunday\n')
        plan = plan_for(profile, [session('long', '2026-11-07', 'long_run', '16 km')])
        self.assertEqual(apply_plan_changes(plan, [], profile, TODAY)['sessions'], plan['sessions'])
        with self.assertRaisesRegex(ValueError, 'Training-day conflict:'):
            apply_plan_changes(plan, [change(dict(plan['sessions'][0], date='2026-11-08'))], profile, TODAY)

    def test_new_plan_is_validated_as_whole_block_and_can_replace_existing_duplicates(self):
        profile = parse_profile(PROFILE)
        plan = plan_for(profile, [session('old-one', TODAY, 'Run'), session('old-two', TODAY, 'Run')])
        plan['new_plan_requested'] = True
        new = [change(session('new-one', TODAY, 'Run'), 'add'), change(session('new-two', TODAY, 'Tempo'), 'add')]
        with self.assertRaisesRegex(ValueError, 'Multiple-running-session conflict:'):
            apply_coach_changes(plan, new, profile, TODAY)
        result = apply_coach_changes(plan, new[:1], profile, TODAY)
        self.assertEqual([item['id'] for item in result['sessions']], ['new-one'])

    def test_prompt_lists_conflicts_and_soft_alternatives_with_new_generation_identity(self):
        profile = parse_profile(PROFILE+'**Preferred Long Run Days:** Friday, Saturday\n')
        plan = plan_for(profile, [session('easy', '2026-11-07', 'easy_run'), session('long', '2026-11-07', 'long_run')])
        evidence = {'today': TODAY, 'window_start': '2026-10-10', 'recent_7d': []}
        prompt = ai_coach.make_prompt(profile, plan, evidence, 'Review my schedule')
        data = json.loads(prompt.split('\nDATA:\n', 1)[1])
        self.assertEqual(data['preferred_long_run_days'], ['Friday', 'Saturday'])
        self.assertEqual(data['running_session_conflicts'], [{'date': '2026-11-07', 'session_ids': ['easy', 'long']}])
        self.assertIn('not a request for a long run on every listed day', prompt)
        self.assertIn('explicitly replace or remove the conflicting run', prompt)
        self.assertIn('allowed_training_days and configured race dates take precedence', prompt)
        free_text_profile = parse_profile(PROFILE+'**Preferred Long Run Day:** Weekend mornings\n')
        free_text_prompt = ai_coach.make_prompt(free_text_profile, plan, evidence, 'Review my schedule')
        free_text_data = json.loads(free_text_prompt.split('\nDATA:\n', 1)[1])
        self.assertIsNone(free_text_data['preferred_long_run_days'])
        self.assertEqual(free_text_data['profile']['preferences']['Preferred Long Run Day'], 'Weekend mornings')
        self.assertIn('still honor any meaningful free-text', free_text_prompt)
        old = {'provider': 'gemini', 'prompt_version': 'autonomous-coach-v5-availability'}
        current = dict(old, prompt_version=ai_coach.PROMPT_VERSION)
        self.assertNotEqual(fingerprint(profile, plan, evidence, 'Review', old),
                            fingerprint(profile, plan, evidence, 'Review', current))

    def test_invalid_ai_move_retains_exact_plan_and_no_retry_then_explicit_corrected_review_applies(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile = parse_profile(PROFILE)
            plan = plan_for(profile, original_sessions())
            paths = {'ATHLETE_FILE': root/'athlete.md', 'PLAN_FILE': root/'training_plan.md',
                     'CANONICAL_FILE': root/'training_plan.json', 'DB_PATH': root/'data.sqlite', 'SCRIPT_DIR': root}
            paths['ATHLETE_FILE'].write_text(PROFILE, encoding='utf-8')
            paths['PLAN_FILE'].write_text(render_plan(plan), encoding='utf-8')
            paths['CANONICAL_FILE'].write_text(json.dumps(plan), encoding='utf-8')
            originals = {path: path.read_bytes() for path in (paths['PLAN_FILE'], paths['CANONICAL_FILE'])}
            payload = {'summary': 'Review the whole week after availability changed.', 'trajectory': 'insufficient_evidence',
                       'reviews': [], 'plan_changes': moved_sessions()}
            corrected = {**payload, 'plan_changes': moved_sessions()+[removal('sat-easy'), removal('tue-easy')]}
            generate = Mock(side_effect=[types.SimpleNamespace(text=json.dumps(value), usage_metadata=None)
                                         for value in (payload, corrected)])
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'primary,fallback', 'COACH_ACTIVITY_SOURCE': 'offline',
                   'COACH_MAX_DAILY_REQUESTS': '2'}
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, env, clear=True), \
                    patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(RuntimeError, 'Multiple-running-session conflict:'):
                    ai_coach.run_daily_coach_check(today=TODAY)
                self.assertEqual(generate.call_count, 1)
                self.assertEqual(originals, {path: path.read_bytes() for path in originals})
                self.assertFalse((root/'.plan-commit.json').exists())
                self.assertEqual(ai_coach.run_daily_coach_check(today=TODAY)['status'], 'complete')
            self.assertEqual(generate.call_count, 2)
            saved = json.loads(paths['CANONICAL_FILE'].read_text(encoding='utf-8'))
            self.assertFalse(running_session_conflicts(saved, TODAY))
            self.assertEqual([item['id'] for item in saved['sessions']], ['sun-long', 'wed-intervals'])
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT status FROM coach_api_requests ORDER BY id').fetchall(), [('invalid',), ('complete',)])


if __name__ == '__main__':
    unittest.main()
