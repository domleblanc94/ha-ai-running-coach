import copy
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ai_coach import daily_coaching_requests, ensure_review_schema
from cron_daily_update import pipeline_lock
from database_manager import ensure_schema
from race_gpx import parse_gpx
from race_planner import RacePlanningError, fitness_context, read_saved_race_plan, run_race_review, validate_race_plan
from tests.test_race_gpx import gpx


def response(route, numeric=False):
    sections = [{'section_id': section['id'], 'seconds_low': 300 if numeric else None,
                 'seconds_high': 360 if numeric else None, 'effort': 'Comfortably controlled',
                 'guidance': 'Use effort on this section and account for the supplied climb.'} for section in route['summary']['sections']]
    checkpoint = next(section['start_m'] for section in route['summary']['sections'] if section['start_m'] >= route['summary']['distance_m'] / 2)
    return {'summary': 'An illustrative plan based on the supplied course and observations.',
            'fitness': {'confidence': 'low', 'evidence': ['Recent running evidence is limited.'], 'limitations': ['Weather and surface remain unknown.']},
            'strategies': [{'id': 'conservative', 'title': 'Controlled race', 'rationale': 'Keep enough in reserve.', 'conditions': ['Back off if effort rises unexpectedly.'],
                            'pickup_after_m': None, 'sections': sections},
                           {'id': 'ambitious', 'title': 'Conditional second-half progression', 'rationale': 'Only progress when recovering well.',
                            'conditions': ['Progress only if comfortable at halfway; otherwise keep the conservative approach.'],
                            'pickup_after_m': checkpoint, 'sections': copy.deepcopy(sections)}]}


class Client:
    def __init__(self, value=None, error=None):
        self.models = self
        self.value, self.error, self.calls = value, error, []
    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text=json.dumps(self.value), finish_reason='STOP', usage_metadata=None)


class RacePlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.route = parse_gpx(gpx([(0, 10), (.01, 50), (.02, 10), (.03, 30)]))
        (self.root / 'athlete.md').write_text('**Athlete Name:** Fictional runner\n**Heart Rate Zones:** Not configured\n**Training Mode:** running\n', encoding='utf-8')
        (self.root / 'training_plan.json').write_text('{"private":"unchanged"}', encoding='utf-8')
        with closing(sqlite3.connect(self.root / 'ai_coach_master.sqlite')) as conn, conn:
            ensure_schema(conn)
        self.environment = patch.dict(os.environ, {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'test-primary,test-fallback',
            'COACH_MAX_DAILY_REQUESTS': '2', 'COACH_MAX_OUTPUT_TOKENS': '4096', 'COACH_ACTIVITY_SOURCE': 'offline'}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def run_review(self, client, question='Plan this race'):
        return run_race_review(self.root, self.route, question, today='2026-11-17', client=client)

    def test_single_request_cached_result_and_no_training_mutation_or_raw_gps_prompt(self):
        client = Client(response(self.route))
        result = self.run_review(client)
        self.assertFalse(result['cached'])
        self.assertEqual(self.run_review(client)['cached'], True)
        self.assertEqual(len(client.calls), 1)
        prompt = client.calls[0]['contents']
        self.assertNotIn('"points"', prompt)
        self.assertNotIn('"lat"', prompt)
        self.assertNotIn('"lon"', prompt)
        self.assertEqual((self.root / 'training_plan.json').read_text(), '{"private":"unchanged"}')
        self.assertIsNone(result['strategies'][0]['finish_seconds'])
        with closing(sqlite3.connect(self.root / 'ai_coach_master.sqlite')) as conn:
            self.assertEqual(len(daily_coaching_requests(conn)), 1)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM coach_review_jobs').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM daily_coach_reviews').fetchone()[0], 0)

    def test_failure_retains_saved_plan_counts_allowance_and_never_falls_back(self):
        prior = self.run_review(Client(response(self.route)))
        failure = Client(error=SimpleProviderError(503))
        with self.assertRaisesRegex(RacePlanningError, 'unavailable'):
            self.run_review(failure, 'Revise for a slower first half')
        self.assertEqual(len(failure.calls), 1)
        self.assertEqual(read_saved_race_plan(self.root, self.route['route_hash']), prior)
        blocked = Client(response(self.route))
        with self.assertRaises(RacePlanningError) as error:
            self.run_review(blocked, 'Another request')
        self.assertEqual(error.exception.code, 'daily_budget')
        self.assertEqual(blocked.calls, [])

    def test_existing_training_requests_use_the_same_budget_and_lock(self):
        with closing(sqlite3.connect(self.root / 'ai_coach_master.sqlite')) as conn, conn:
            ensure_review_schema(conn)
            for kind in ('daily', 'manual_override'):
                conn.execute('INSERT INTO coach_api_requests(request_kind,started_at,status) VALUES(?,?,?)', (kind, datetime.now(timezone.utc).isoformat(), 'failed'))
        client = Client(response(self.route))
        with self.assertRaises(RacePlanningError) as failure:
            self.run_review(client)
        self.assertEqual(failure.exception.code, 'daily_budget')
        self.assertEqual(client.calls, [])
        with pipeline_lock(self.root / '.coach.lock'):
            with self.assertRaises(RacePlanningError) as failure:
                self.run_review(client)
        self.assertEqual(failure.exception.code, 'busy')

    def test_numeric_estimates_require_observed_recent_distance_duration(self):
        payload = response(self.route, numeric=True)
        with self.assertRaises(ValueError):
            validate_race_plan(payload, self.route, {'numeric_pacing_evidence_available': False})
        valid = validate_race_plan(payload, self.route, {'numeric_pacing_evidence_available': True})
        count = len(self.route['summary']['sections'])
        self.assertEqual(valid['strategies'][0]['finish_seconds'], {'low': 300 * count, 'high': 360 * count})
        for change in ('order', 'early', 'missing', 'nonfinite', 'off_boundary', 'mixed_unknown', 'slower_opening'):
            invalid = copy.deepcopy(payload)
            if change == 'order': invalid['strategies'][0]['sections'].reverse()
            if change == 'early': invalid['strategies'][1]['sections'][0]['seconds_low'] = 200
            if change == 'missing': invalid['strategies'][1]['sections'].pop()
            if change == 'nonfinite': invalid['strategies'][1]['sections'][0]['seconds_low'] = float('nan')
            if change == 'off_boundary': invalid['strategies'][1]['pickup_after_m'] += 10
            if change == 'mixed_unknown':
                invalid['strategies'][1]['sections'][0].update(seconds_low=None, seconds_high=None)
            if change == 'slower_opening': invalid['strategies'][1]['sections'][0]['seconds_high'] = 400
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_race_plan(invalid, self.route, {'numeric_pacing_evidence_available': True})

    def test_invalid_model_output_keeps_last_good_and_bounded_followup_context(self):
        previous = self.run_review(Client(response(self.route)))
        bad = response(self.route)
        bad['strategies'][1]['pickup_after_m'] = 0
        client = Client(bad)
        with self.assertRaises(RacePlanningError) as failure:
            self.run_review(client, 'Please explain the hills')
        self.assertEqual(failure.exception.code, 'response_invalid')
        self.assertIn('previous_race_plan', client.calls[0]['contents'])
        self.assertEqual(read_saved_race_plan(self.root, self.route['route_hash']), previous)
        with closing(sqlite3.connect(self.root / 'ai_coach_master.sqlite')) as conn:
            self.assertEqual([row[3] for row in daily_coaching_requests(conn)], ['complete', 'invalid'])

    def test_request_is_durably_reserved_before_transport_and_profile_change_invalidates_cache(self):
        owner = self
        class InspectingClient(Client):
            def generate_content(self, **kwargs):
                with closing(sqlite3.connect(owner.root / 'ai_coach_master.sqlite')) as conn:
                    rows = daily_coaching_requests(conn)
                    owner.assertEqual(rows[-1][3], 'started')
                    owner.assertEqual(rows[-1][5], 'daily')
                return super().generate_content(**kwargs)
        client = InspectingClient(response(self.route))
        self.run_review(client)
        with (self.root / 'athlete.md').open('a', encoding='utf-8') as stream:
            stream.write('**Coaching Style:** Explain terrain uncertainty\n')
        self.assertFalse(self.run_review(client)['cached'])
        self.assertEqual(len(client.calls), 2)

    def test_timer_only_evidence_is_labelled_and_stale_or_other_sports_do_not_enable_pace(self):
        with closing(sqlite3.connect(self.root / 'ai_coach_master.sqlite')) as conn, conn:
            conn.execute('INSERT INTO activities(id,type,start_date,distance_m,timer_duration_s,source,metrics_source) VALUES(1,?,?,?,?,?,?)',
                         ('running', '2026-11-15T08:00:00', 5000, 2100, 'local:race-test', 'local:race-test'))
            fresh = fitness_context(conn, '2026-11-17')
            self.assertTrue(fresh['numeric_pacing_evidence_available'])
            self.assertEqual(fresh['running_observations'][0]['observed_duration_s'], 2100)
            self.assertIn('may include rests', fresh['running_observations'][0]['duration_basis'])
            self.assertFalse(fitness_context(conn, '2026-12-17')['numeric_pacing_evidence_available'])
            conn.execute("UPDATE activities SET type='cycling'")
            self.assertFalse(fitness_context(conn, '2026-11-17')['numeric_pacing_evidence_available'])

    def test_dates_and_source_policy_fail_before_any_provider_call(self):
        client = Client(response(self.route))
        for invalid in (0, False, '20261117', '2026-99-99'):
            with self.subTest(date=invalid), self.assertRaises(ValueError):
                run_race_review(self.root, self.route, 'Plan', race_date=invalid, today='2026-11-17', client=client)
        with closing(sqlite3.connect(self.root / 'ai_coach_master.sqlite')) as conn, conn:
            conn.execute('INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,source,metrics_source) VALUES(1,?,?,?,?,?,?)',
                         ('running', '2026-11-15T08:00:00', 5000, 2100, 'legacy', 'legacy'))
        with self.assertRaises(ValueError):
            self.run_review(client)
        self.assertEqual(client.calls, [])

    def test_profile_only_first_use_initializes_history_only_on_explicit_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'athlete.md').write_bytes((self.root / 'athlete.md').read_bytes())
            self.assertIsNone(read_saved_race_plan(root, self.route['route_hash']))
            self.assertFalse((root / 'ai_coach_master.sqlite').exists())
            client = Client(response(self.route))
            result = run_race_review(root, self.route, 'Plan with limited evidence', today='2026-11-17', client=client)
            self.assertEqual(result['context']['activity_count'], 0)
            self.assertIsNone(result['strategies'][0]['finish_seconds'])
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(read_saved_race_plan(root, self.route['route_hash']), result)


class SimpleProviderError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__('Provider failure')


if __name__ == '__main__':
    unittest.main()
