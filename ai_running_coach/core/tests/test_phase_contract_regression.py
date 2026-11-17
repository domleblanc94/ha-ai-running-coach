"""Independent checks across phase updates, persisted reviews and presentation."""
from contextlib import closing, redirect_stdout
import copy
from datetime import date
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import ai_coach
from generate_dashboard import build_dashboard, parse_sessions
from planning_engine import apply_coach_changes, parse_profile, render_plan, validate_review


TODAY = '2026-11-17'
PROFILE = '**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n'


def phase(start, end, name='Build', purpose='The coach-authored purpose for this block.'):
    return {'start_date': start, 'end_date': end, 'name': name, 'purpose': purpose}


def session(identifier, day):
    return {'id': identifier, 'date': day, 'sport': 'running', 'workout_type': 'Easy run',
            'amount': '30 min', 'target': 'Coach-selected comfortable effort.'}


def fixture():
    profile = parse_profile(PROFILE)
    plan = {'schema_version': 1, 'revision': 3, 'profile_hash': profile['source_hash'],
            'goals': profile['goals'], 'sessions': [session('past', '2026-11-16'),
                session('today', TODAY), session('future', '2026-11-21')],
            'phases': [phase('2026-11-01', '2026-11-10', 'Baseline'),
                phase('2026-11-11', '2026-11-20'), phase('2026-11-21', '2026-11-30', 'Peak')]}
    return profile, plan


def response(**extra):
    return {'summary': 'The phase labels explain the purpose of the saved schedule.',
            'trajectory': 'insufficient_evidence', 'reviews': [], 'plan_changes': [], **extra}


class PhaseContractRegressionTests(unittest.TestCase):
    def test_partial_mixed_week_dashboard_escapes_text_without_changing_sources(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()):
            root = Path(directory)
            _, saved = fixture()
            attack = '</script><img src=x onerror=alert(1)>'
            saved['sessions'] = [session('friday', '2026-11-20'), session('sunday', '2026-11-22')]
            saved['phases'] = [phase('2026-11-10', '2026-11-18', 'Build <b>& steady</b>', attack),
                               phase('2026-11-20', '2026-11-30', 'Taper', 'Preserve freshness.')]
            markdown = render_plan(saved)
            self.assertEqual([(item['date'], item['kind']) for item in parse_sessions(markdown)],
                             [(item['date'], item['workout_type']) for item in saved['sessions']])
            (root/'athlete.md').write_text(PROFILE, encoding='utf-8')
            (root/'training_plan.json').write_text(json.dumps(saved), encoding='utf-8')
            (root/'training_plan.md').write_text(markdown, encoding='utf-8')
            db = root/'data.sqlite'
            with closing(sqlite3.connect(db)) as conn:
                ai_coach.ensure_review_schema(conn)
                conn.commit()
            inputs = [root/'athlete.md', root/'training_plan.json', root/'training_plan.md', db]
            snapshots = {path: path.read_bytes() for path in inputs}
            rendered = build_dashboard(db, root/'training_plan.md', root/'dashboard.html',
                                       today=date(2026, 11, 17)).read_text(encoding='utf-8')
            self.assertEqual({path: path.read_bytes() for path in inputs}, snapshots)
            self.assertNotIn(attack, rendered)
            self.assertNotIn('Build <b>& steady</b>', rendered)
            self.assertIn('Build &lt;b&gt;&amp; steady&lt;/b&gt;', rendered)
            self.assertIn('id="trainingPhases"', rendered)
            self.assertIn('id="selectedWeekPhases"', rendered)
            weeks = json.loads(re.search(r'const trainingPhaseWeeks = (.*);', rendered)[1])
            spans = weeks['2026-11-16']
            self.assertEqual([(item['start_date'], item['end_date'], item['phase']['name'] if item['phase'] else None)
                              for item in spans], [
                                  ('2026-11-16', '2026-11-18', saved['phases'][0]['name']),
                                  ('2026-11-19', '2026-11-19', None),
                                  ('2026-11-20', '2026-11-22', 'Taper')])

    def test_omitted_clear_and_new_plan_do_not_relabel_historical_days(self):
        profile, saved = fixture()
        before = copy.deepcopy(saved)
        self.assertEqual(apply_coach_changes(saved, [], profile, TODAY), saved)
        cleared = apply_coach_changes(saved, [], profile, TODAY, phases=[])
        past = [saved['phases'][0], dict(saved['phases'][1], end_date='2026-11-16')]
        self.assertEqual(cleared['phases'], past)
        self.assertEqual(cleared['sessions'], saved['sessions'])
        self.assertEqual(cleared['revision'], saved['revision'] + 1)
        self.assertEqual(apply_coach_changes(cleared, [], profile, TODAY, phases=[]), cleared)

        request = dict(saved, new_plan_requested=True, requires_coach_reconciliation=True)
        fresh = session('fresh', '2026-11-18')
        changes = [{'op': 'add', 'session_id': 'fresh', 'session': fresh, 'reason': 'Start a new block.'}]
        replaced = apply_coach_changes(request, changes, profile, TODAY)
        self.assertEqual(replaced['phases'], past)
        self.assertEqual(replaced['sessions'], [saved['sessions'][0], fresh])
        self.assertEqual(replaced['revision'], saved['revision'] + 1)
        self.assertEqual(replaced['superseded_blocks'][-1]['phases'], saved['phases'])
        self.assertEqual(replaced['superseded_blocks'][-1]['sessions'], saved['sessions'][1:])
        self.assertEqual(saved, before)

    def test_phase_and_session_change_advance_one_revision_and_validation_is_pure(self):
        profile, saved = fixture()
        changed = dict(saved['sessions'][1], amount='35 min')
        payload = response(phases=[phase(TODAY, '2026-11-22', 'Recovery')],
                           plan_changes=[{'op': 'update', 'session_id': changed['id'],
                                          'session': changed, 'reason': 'Current athlete feedback.'}])
        before = copy.deepcopy((saved, payload))
        self.assertEqual(validate_review(payload, {'recent_7d': []}, saved, TODAY, profile), payload)
        resulting = apply_coach_changes(saved, payload['plan_changes'], profile, TODAY, phases=payload['phases'])
        self.assertEqual(resulting['revision'], saved['revision'] + 1)
        self.assertEqual(resulting['sessions'][1], changed)
        self.assertEqual(resulting['phases'][-1], payload['phases'][0])
        self.assertEqual((saved, payload), before)
        for invalid in (None, [phase('2026-11-16', '2026-11-22')],
                        [phase(TODAY, '2026-11-20'), phase('2026-11-20', '2026-11-23')]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_review(dict(payload, phases=invalid), {'recent_7d': []}, saved, TODAY, profile)
            self.assertEqual((saved, payload), before)

    def test_phase_only_commit_cache_and_invalid_followup_preserve_last_good_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, saved = fixture()
            paths = {'ATHLETE_FILE': root/'athlete.md', 'PLAN_FILE': root/'training_plan.md',
                     'CANONICAL_FILE': root/'training_plan.json', 'DB_PATH': root/'data.sqlite', 'SCRIPT_DIR': root}
            paths['ATHLETE_FILE'].write_text(PROFILE, encoding='utf-8')
            paths['PLAN_FILE'].write_text(render_plan(saved), encoding='utf-8')
            paths['CANONICAL_FILE'].write_text(json.dumps(saved), encoding='utf-8')
            payload = response(phases=[phase(TODAY, '2026-11-20', 'Recovery'),
                                       phase('2026-11-21', '2026-11-28', 'Build again')])
            invalid = response(summary='This invalid follow-up must not replace the saved review.',
                               phases=[phase(TODAY, '2026-11-21'), phase('2026-11-20', '2026-11-22')])
            generate = Mock(side_effect=[types.SimpleNamespace(text=json.dumps(value), usage_metadata=None)
                                        for value in (payload, invalid)])
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'test-model',
                   'COACH_ACTIVITY_SOURCE': 'offline', 'COACH_MAX_DAILY_REQUESTS': '2'}
            with patch.multiple(ai_coach, **paths), patch.dict(os.environ, env, clear=True), \
                    patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                first = ai_coach.run_daily_coach_check(today=TODAY)
                self.assertEqual((first['status'], first['applied_revision']), ('complete', 4))
                resulting = json.loads(paths['CANONICAL_FILE'].read_text(encoding='utf-8'))
                self.assertEqual(resulting['sessions'], saved['sessions'])
                self.assertEqual(resulting['phases'][-2:], payload['phases'])
                self.assertIn('Build again', paths['PLAN_FILE'].read_text(encoding='utf-8'))
                stable = {path: path.read_bytes() for path in
                          (paths['ATHLETE_FILE'], paths['CANONICAL_FILE'], paths['PLAN_FILE'])}
                self.assertEqual(ai_coach.run_daily_coach_check(today=TODAY)['status'], 'unchanged')
                self.assertEqual(generate.call_count, 1)
                with self.assertRaisesRegex(RuntimeError, 'last-good plan retained'):
                    ai_coach.run_daily_coach_check(today=TODAY, user_question='Reconsider the phase boundaries.')
                self.assertEqual(generate.call_count, 2)
                self.assertEqual({path: path.read_bytes() for path in stable}, stable)
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT status FROM coach_api_requests ORDER BY id').fetchall(),
                                 [('complete',), ('invalid',)])
                self.assertEqual(conn.execute('SELECT summary_text FROM daily_coach_reviews').fetchall(),
                                 [(payload['summary'],)])
                self.assertEqual(conn.execute('SELECT revision FROM coach_plan_versions').fetchall(), [(4,)])


if __name__ == '__main__':
    unittest.main()
