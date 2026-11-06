"""A fresh block can acknowledge retirement, but cannot bypass plan constraints."""
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
from planning_engine import apply_coach_changes, new_session_id_prefix, parse_profile, render_plan


TODAY = '2026-11-06'
PROFILE = '**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n**Weekly Availability:** Monday, Tuesday, Thursday, Friday, Saturday\n'


def session(identifier, day='2026-11-07', kind='Run', **extra):
    return {'id': identifier, 'date': day, 'workout_type': kind, 'amount': '7 km', 'target': 'Coach-selected effort', **extra}


def addition(value):
    return {'op': 'add', 'session_id': value['id'], 'session': value, 'reason': 'A fresh block from current evidence.'}


def deletion(identifier):
    return {'op': 'delete', 'session_id': identifier, 'session': None, 'reason': 'Retire the old future block.'}


def fixture():
    profile = parse_profile(PROFILE)
    plan = {'schema_version': 1, 'revision': 3, 'profile_hash': profile['source_hash'], 'goals': profile['goals'],
            'new_plan_requested': True, 'requires_coach_reconciliation': True, 'retired_session_ids': ['retired-run'],
            'sessions': [session('historic', '2026-11-05'), session('old-easy'), session('old-long', kind='Long run')]}
    return profile, plan


class NewPlanContractTests(unittest.TestCase):
    def test_redundant_known_future_deletes_match_automatic_retirement_without_mutation(self):
        profile, plan = fixture()
        fresh = addition(session('fresh-run'))
        changes = [deletion('old-easy'), deletion('old-long'), fresh]
        before = copy.deepcopy((plan, changes))
        explicit = apply_coach_changes(plan, changes, profile, TODAY)
        automatic = apply_coach_changes(plan, [fresh], profile, TODAY)
        self.assertEqual(explicit, automatic)
        self.assertEqual((plan, changes), before)
        self.assertEqual([item['id'] for item in explicit['sessions']], ['historic', 'fresh-run'])
        self.assertEqual(explicit['retired_session_ids'], ['old-easy', 'old-long', 'retired-run'])
        self.assertEqual(explicit['superseded_blocks'][0]['sessions'], plan['sessions'][1:])

    def test_redundant_delete_admission_stays_strict(self):
        profile, plan = fixture()
        fresh = addition(session('fresh-run'))
        invalid = [
            [deletion('unknown'), fresh], [deletion('historic'), fresh],
            [deletion('old-easy'), deletion('old-easy'), fresh],
            [dict(deletion('old-easy'), session=session('old-easy')), fresh],
            [dict(deletion('old-easy'), reason=''), fresh],
            [dict(deletion('old-easy'), extra='unexpected'), fresh],
            [dict(fresh, op='update')], [deletion('old-easy')], ['invalid', fresh],
            [deletion('old-easy'), addition(session('old-easy'))],
            [deletion('old-easy'), addition(session('retired-run'))],
        ]
        for changes in invalid:
            with self.subTest(changes=changes):
                before = copy.deepcopy(plan)
                with self.assertRaises(ValueError):
                    apply_coach_changes(plan, changes, profile, TODAY)
                self.assertEqual(plan, before)

    def test_full_replacement_guards_reject_unavailable_day_double_runs_race_and_workout(self):
        profile, plan = fixture()
        cases = [([addition(session('fresh-sunday', '2026-11-08'))], 'Training-day conflict:'),
                 ([addition(session('fresh-one')), addition(session('fresh-two'))], 'Multiple-running-session conflict:'),
                 ([addition(session('fresh-race', kind='Race', goal_priority='A-Race'))], 'Race calendar conflict:'),
                 ([addition(session('fresh-workout', workout={'sport': 'running', 'steps': []}))], 'Workout')]
        for additions, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                apply_coach_changes(plan, [deletion('old-easy'), deletion('old-long'), *additions], profile, TODAY)

    def test_prefix_is_deterministic_unused_and_does_not_rewrite_ids(self):
        _, plan = fixture()
        prefix = new_session_id_prefix(plan, TODAY)
        self.assertEqual(prefix, new_session_id_prefix(plan, TODAY))
        before = copy.deepcopy(plan)
        plan['retired_session_ids'] += [prefix+'reserved', prefix+'1-reserved']
        next_prefix = new_session_id_prefix(plan, TODAY)
        reserved = plan['retired_session_ids'] + [item['id'] for item in plan['sessions']]
        self.assertNotEqual(prefix, next_prefix)
        self.assertTrue(all(not identifier.startswith(next_prefix) for identifier in reserved))
        self.assertEqual(plan['sessions'], before['sessions'])

    def test_new_prompt_separates_old_reference_from_replacement_and_supplies_exact_calendar(self):
        profile, plan = fixture()
        evidence = {'today': TODAY, 'recent_7d': []}
        prompt = ai_coach.make_prompt(profile, plan, evidence, 'New plan please')
        data = json.loads(prompt.split('\nDATA:\n', 1)[1])
        self.assertIn('do not update, delete or repair those old entries', prompt)
        self.assertNotIn('Correct every supplied training_day_conflicts', prompt)
        self.assertNotIn('Correct every supplied running_session_conflicts', prompt)
        self.assertEqual(data['new_session_id_prefix'], new_session_id_prefix(plan, TODAY))
        self.assertEqual([(item['date'], item['weekday'], item['training_allowed']) for item in data['starter_calendar']], [
            ('2026-11-06', 'Friday', True), ('2026-11-07', 'Saturday', True), ('2026-11-08', 'Sunday', False),
            ('2026-11-09', 'Monday', True), ('2026-11-10', 'Tuesday', True), ('2026-11-11', 'Wednesday', False),
            ('2026-11-12', 'Thursday', True)])
        review = copy.deepcopy(plan); review.pop('new_plan_requested')
        normal_prompt = ai_coach.make_prompt(profile, review, evidence, 'Review please')
        self.assertIn('Correct every supplied training_day_conflicts', normal_prompt)
        self.assertNotIn('new_session_id_prefix', normal_prompt)
        unknown = parse_profile('**Heart Rate Zones:** Not configured\n')
        unknown_data = json.loads(ai_coach.make_prompt(unknown, plan, evidence, 'New plan').split('\nDATA:\n', 1)[1])
        self.assertTrue(all(item['training_allowed'] is None for item in unknown_data['starter_calendar']))

    def test_valid_raw_delete_add_response_is_saved_unchanged_with_one_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            profile, plan = fixture(); plan.pop('new_plan_requested')
            paths = {'ATHLETE_FILE': root/'athlete.md', 'PLAN_FILE': root/'training_plan.md',
                     'CANONICAL_FILE': root/'training_plan.json', 'DB_PATH': root/'data.sqlite', 'SCRIPT_DIR': root}
            paths['ATHLETE_FILE'].write_text(PROFILE, encoding='utf-8')
            paths['PLAN_FILE'].write_text(render_plan(plan), encoding='utf-8')
            paths['CANONICAL_FILE'].write_text(json.dumps(plan), encoding='utf-8')
            payload = {'summary': 'Replace the future block while preserving history.', 'trajectory': 'insufficient_evidence',
                       'reviews': [], 'plan_changes': [deletion('old-easy'), deletion('old-long'), addition(session('fresh-run'))]}
            original_response = json.dumps(payload)
            generate = Mock(return_value=types.SimpleNamespace(text=original_response, usage_metadata=None))
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'primary', 'COACH_ACTIVITY_SOURCE': 'offline', 'COACH_MAX_DAILY_REQUESTS': '2'}
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, env, clear=True), \
                    patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                self.assertEqual(ai_coach.run_daily_coach_check(today=TODAY, new_plan=True)['status'], 'complete')
            self.assertEqual(generate.call_count, 1)
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT status,response_text FROM coach_api_requests').fetchone(), ('complete', original_response))
                saved = json.loads(conn.execute('SELECT response_json FROM coach_review_jobs WHERE response_json IS NOT NULL LIMIT 1').fetchone()[0])
                self.assertEqual(saved['plan_changes'], payload['plan_changes'])
            resulting = json.loads(paths['CANONICAL_FILE'].read_text(encoding='utf-8'))
            self.assertEqual([item['id'] for item in resulting['sessions']], ['historic', 'fresh-run'])


if __name__ == '__main__':
    unittest.main()
