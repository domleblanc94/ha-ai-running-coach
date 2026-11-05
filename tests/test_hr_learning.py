from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ai_running_coach.app.hr_learning import LEARNING_QUESTION
from ai_running_coach.app.runtime import Runtime


NOW = datetime(2026, 11, 5, 12, tzinfo=timezone.utc)
PROFILE = '# Athlete\n**Zone 2 (Endurance):** 120 - 145 bpm\n**Weekly Availability:** 3 days\n'
LEARN_PROFILE = PROFILE + '**HR Zone Learning:** Enabled\n'


class LearningTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.enterContext(patch('ai_running_coach.app.runtime.utc_now', return_value=NOW))
        self.runtime = Runtime(Path(temporary.name) / 'data')
        self.runtime.save_profile(LEARN_PROFILE)
        self.database = self.runtime.workspace / 'ai_coach_master.sqlite'

    def execute_core(self, code):
        return subprocess.check_output([sys.executable, '-B', '-c', code], cwd=self.runtime.workspace,
                                       text=True, encoding='utf-8', timeout=20)

    def seed(self):
        self.execute_core('''
import sqlite3
from database_manager import ensure_schema
from ai_coach import ensure_review_schema
with sqlite3.connect('ai_coach_master.sqlite') as conn:
    ensure_schema(conn)
    ensure_review_schema(conn)
''')
        data = [
            (1, 'running', 'run', 0, 'garmin_personal', 140, 1800),
            (2, 'trail_running', 'run', -1, 'garmin_personal', 0, None),
            (3, 'cycling', 'ride', 0, 'garmin_personal', 140, 1800),
            (4, 'running', 'run', 0, 'local:fixture', 140, 1800),
            (5, 'running', 'run', 1, 'garmin_personal', 140, 1800),
            (6, 'running', 'run', -21, 'garmin_personal', 140, 1800),
        ]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.executemany('''INSERT INTO activities
                (id,type,normalized_type,start_date,source,avg_heartrate,moving_time_s)
                VALUES (?,?,?,?,?,?,?)''', [
                    (activity, raw_type, kind, (NOW.date()+timedelta(days=delta)).isoformat(), source, heart, moving)
                    for activity, raw_type, kind, delta, source, heart, moving in data
                ])

    @staticmethod
    def feedback(activity_id=1, rpe=3):
        return {'activity_id': activity_id, 'rpe': rpe, 'talk_test': 'comfortable', 'sensor': 'chest_strap'}

    def test_recent_garmin_running_only_with_unknown_hr_and_duration(self):
        self.assertEqual(self.runtime.status()['hr_learning']['state'], 'awaiting_runs')
        self.seed()
        status = self.runtime.status()['hr_learning']
        self.assertEqual(status['state'], 'awaiting_feedback')
        self.assertEqual([row['activity_id'] for row in status['candidates']], [1, 2])
        self.assertEqual(status['candidates'][0]['average_hr_bpm'], 140)
        self.assertIsNone(status['candidates'][1]['average_hr_bpm'])
        self.assertIsNone(status['candidates'][1]['moving_minutes'])
        for invalid_id in (3, 4, 5, 6, 99):
            with self.subTest(activity_id=invalid_id), self.assertRaises(ValueError):
                self.runtime.save_baseline_feedback(self.feedback(invalid_id))
        for invalid_rpe in (0, 11, True, '3'):
            with self.subTest(rpe=invalid_rpe), self.assertRaises(ValueError):
                self.runtime.save_baseline_feedback(self.feedback(rpe=invalid_rpe))

    def test_actual_checkins_reach_core_and_edits_supersede_without_duplicate_count(self):
        self.seed()
        self.runtime.save_baseline_feedback(self.feedback())
        self.runtime.save_baseline_feedback(self.feedback())
        changed = self.runtime.save_baseline_feedback(self.feedback(rpe=4))['hr_learning']
        self.assertEqual(changed['feedback_count'], 1)
        self.assertEqual(changed['candidates'][0]['feedback']['rpe'], 4)
        self.assertEqual(changed['state'], 'observations_ready')
        self.assertEqual(self.runtime.profile(), LEARN_PROFILE)
        code = f'''
import json,sqlite3
from planning_engine import build_evidence
from source_policy import assert_coaching_sources
with sqlite3.connect('ai_coach_master.sqlite') as conn:
    assert_coaching_sources(conn)
    evidence = build_evidence(conn, {NOW.date().isoformat()!r})
    print(json.dumps(evidence['feedback_history']))
'''
        history = json.loads(self.execute_core(code))
        self.assertEqual([entry['fields']['rpe'] for entry in history], [3, 4])
        self.assertFalse(history[0]['current_report'])
        self.assertTrue(history[1]['current_report'])
        self.assertEqual(history[1]['activity_id'], 1)
        self.assertIn('Whole-activity average HR: 140 bpm', history[1]['comment'])
        self.assertIn('Talk test: comfortable', history[1]['comment'])
        self.assertIn('HR sensor: chest_strap', history[1]['comment'])
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM coach_api_requests').fetchone()[0], 0)

    def test_learning_mode_uses_fixed_question_only_for_normal_queued_action(self):
        self.runtime.save_config({'gemini_api_key': 'synthetic-test-key'})
        self.runtime.enqueue('new_plan')
        with patch.object(self.runtime, '_command', return_value=1) as command:
            self.assertTrue(self.runtime.tick())
        command.assert_called_once_with('learn_new_plan', timezone_name='UTC', input_text=LEARNING_QUESTION)
        self.assertIn('conversational effort', LEARNING_QUESTION)
        self.assertIn('Do not infer five zones', LEARNING_QUESTION)
        self.runtime.save_profile(PROFILE)
        self.assertEqual(self.runtime.status()['hr_learning']['state'], 'off')
        with self.assertRaises(ValueError):
            self.runtime.save_baseline_feedback(self.feedback())
        self.runtime.enqueue('new_plan')
        with patch.object(self.runtime, '_command', return_value=1) as command:
            self.assertTrue(self.runtime.tick())
        command.assert_called_once_with('new_plan', timezone_name='UTC')

    def test_only_completed_learning_assessment_is_presented(self):
        self.seed()
        fixtures = [
            ('valid', LEARNING_QUESTION, 'complete', 'Observations are still limited.'),
            ('other', 'Ordinary review', 'complete', 'Unrelated review.'),
            ('invalid', LEARNING_QUESTION, 'invalid', 'Unvalidated response.'),
        ]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            for fingerprint, question, status, summary in fixtures:
                conn.execute('INSERT INTO coach_review_jobs(fingerprint,status,response_json) VALUES(?,?,?)',
                             (fingerprint, 'complete', json.dumps({'summary': summary})))
                conn.execute('''INSERT INTO coach_api_requests(job_fingerprint,status,completed_at,input_snapshot_json)
                                VALUES(?,?,?,?)''', (fingerprint, status, NOW.isoformat(), json.dumps({'question': question})))
        status = self.runtime.status()['hr_learning']
        self.assertEqual(status['review'], {'completed_at': NOW.isoformat(), 'summary': 'Observations are still limited.'})
        self.assertEqual(status['state'], 'awaiting_feedback')


if __name__ == '__main__':
    unittest.main()
