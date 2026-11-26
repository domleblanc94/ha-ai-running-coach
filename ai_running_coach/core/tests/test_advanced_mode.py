import contextlib
import copy
from datetime import date, timedelta
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import ai_coach
from coaching_mode import planning_settings, replace_future_overview, validate_overview
from coaching_detail import review_instructions
from dashboard_panels import build_phase_display
from model_provider import ProviderClient
from planning_engine import apply_coach_changes, build_evidence, parse_plan, parse_profile, render_plan, validate_review
from race_planner import make_race_prompt, validate_race_plan
from tests.test_race_planner import response
from tests.test_race_gpx import gpx
from race_gpx import parse_gpx


def note(start, end, focus='Recovery and progression'):
    return {'start_date': start, 'end_date': end, 'focus': focus,
            'rationale': 'Match recent duration and use an easier follow-up day.',
            'adaptation': 'Shorten the next session if recovery is incomplete; do not make up missed distance.',
            'review_trigger': 'Review after the longer session with perceived effort and next-day recovery.'}


class AdvancedModeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'test-model', 'COACH_ACTIVITY_SOURCE': 'offline'}, clear=True)
        self.env.start(); self.addCleanup(self.env.stop)
        self.today = date.today()
        self.day = self.today.isoformat()
        self.future = (self.today + timedelta(days=1)).isoformat()
        self.profile = parse_profile('**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n')
        self.plan = parse_plan('| Day | '+self.future+' | Run | 5 km | Comfortable effort |', self.profile)

    def test_effective_limits_and_invalid_modes(self):
        economy = planning_settings({})
        self.assertEqual((economy['max_output_tokens'], economy['max_input_chars'], economy['planning_days']), (4096, 60000, 7))
        env = {'COACH_PLANNING_MODE':'advanced', 'COACH_MAX_OUTPUT_TOKENS':'512'}
        advanced = planning_settings(env)
        self.assertEqual((advanced['max_output_tokens'], advanced['max_input_chars'], advanced['planning_days']), (16384, 120000, 28))
        env.update(COACH_ADVANCED_MAX_OUTPUT_TOKENS='999999', COACH_ADVANCED_MAX_INPUT_CHARS='999999', COACH_ADVANCED_HTTP_TIMEOUT_SECONDS='999')
        capped = planning_settings(env)
        self.assertEqual((capped['max_output_tokens'], capped['max_input_chars'], capped['http_timeout_seconds']), (32768,240000,300))
        with self.assertRaises(ValueError): planning_settings({'COACH_PLANNING_MODE':'anything'})
        with self.assertRaises(ValueError): planning_settings({'COACH_PLANNING_MODE':'advanced','COACH_ADVANCED_MAX_OUTPUT_TOKENS':'many'})

    def test_provider_budget_default_thinking_and_single_request(self):
        for provider, data in [('gemini', {'candidates':[{'content':{'parts':[{'text':'{}'}]}}]}),
                               ('openai', {'choices':[{'message':{'content':'{}'}}]}),
                               ('anthropic', {'content':[{'type':'text','text':'{}'}]})]:
            session = Mock(); session.post.return_value=SimpleNamespace(status_code=200,json=lambda:data)
            with self.subTest(provider=provider), patch.dict(os.environ, {'COACH_PLANNING_MODE':'advanced', provider.upper()+'_API_KEY':'synthetic-key'}):
                client=ProviderClient(provider,session)
                client.generate_content('gemini-3.5-flash-lite' if provider=='gemini' else 'explicit-model','JSON.',{'response_mime_type':'application/json'})
            payload=session.post.call_args.kwargs['json']
            self.assertEqual(session.post.call_count,1)
            self.assertEqual(session.post.call_args.kwargs['timeout'],(10,180))
            if provider=='gemini':
                self.assertEqual(payload['generationConfig']['maxOutputTokens'],16384)
                self.assertNotIn('thinkingConfig',payload['generationConfig'])
            elif provider=='openai':
                self.assertEqual(payload['max_completion_tokens'],16384)
                self.assertNotIn('temperature',payload);self.assertNotIn('reasoning_effort',payload)
            else:self.assertEqual(payload['max_tokens'],16384)

    def evidence(self):
        with contextlib.closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL)')
            for i, days in enumerate((2,10,20,40),1):
                conn.execute('INSERT INTO activities VALUES(?,?,?,?,?)',(i,'Run',(self.today-timedelta(days=days)).isoformat(),5000,1800))
            return build_evidence(conn,self.day)

    def test_richer_evidence_prompt_and_identity_without_changing_caps(self):
        evidence=self.evidence(); base=ai_coach.review_identity(self.plan,self.day)
        self.assertNotIn('recent_14d',evidence)
        os.environ['COACH_PLANNING_MODE']='advanced'
        expanded=self.evidence()
        self.assertEqual({row['activity_id'] for row in expanded['recent_14d']},{1,2})
        self.assertEqual({row['activity_id'] for row in expanded['context_previous_14d']},{3})
        self.plan['new_plan_requested']=True
        prompt=ai_coach.make_prompt(self.profile,self.plan,expanded,'Make a plan')
        self.assertIn('up to 28 days',prompt);self.assertIn('planning_overview',prompt)
        self.assertIn('specific recovery or missed-session alternatives',prompt)
        self.assertNotIn('Save subsequent weeks for a later review',prompt)
        self.assertNotEqual(base,ai_coach.review_identity(self.plan,self.day))
        self.assertEqual(ai_coach.daily_request_limit(),2)
        self.assertEqual(ai_coach.limits()[2],2)
        identity=ai_coach.review_identity(self.plan,self.day)
        os.environ['COACH_ADVANCED_MAX_OUTPUT_TOKENS']='20000'
        self.assertNotEqual(identity,ai_coach.review_identity(self.plan,self.day))

    def test_saved_evidence_controls_long_text_validation_and_review_ids(self):
        evidence=self.evidence()
        payload={'summary':'Useful explanation. '*160,'trajectory':'insufficient_evidence','reviews':[{'activity_id':2,'feedback':'Detailed action. '*130}],'plan_changes':[]}
        with self.assertRaises(ValueError):validate_review(payload,evidence,self.plan,self.day,self.profile)
        os.environ['COACH_PLANNING_MODE']='advanced'; advanced=self.evidence()
        os.environ['COACH_PLANNING_MODE']='economical'
        self.assertIs(validate_review(payload,advanced,self.plan,self.day,self.profile),payload)
        bad=copy.deepcopy(payload);bad['reviews'][0]['activity_id']=3
        with self.assertRaises(ValueError):validate_review(bad,advanced,self.plan,self.day,self.profile)
        concise=review_instructions({'review_detail':'concise'},planning_settings({'COACH_PLANNING_MODE':'advanced'}))
        self.assertIn('40-70 word summary',concise)

    def test_overview_history_rendering_and_replacement(self):
        past=(self.today-timedelta(days=3)).isoformat();end=(self.today+timedelta(days=3)).isoformat()
        old=note(past,end,'Old focus');new=note(self.day,end,'<script>alert(1)</script>')
        combined=replace_future_overview([old],[new],self.day)
        self.assertEqual(combined[0]['end_date'],(self.today-timedelta(days=1)).isoformat())
        self.assertEqual(old['end_date'],end)
        self.plan['planning_overview']=[old]
        updated=apply_coach_changes(self.plan,[],self.profile,self.day,planning_overview=[new])
        self.assertGreater(updated['revision'],self.plan['revision'])
        markdown=render_plan(updated)
        self.assertIn('Coaching plan overview',markdown);self.assertIn('Review when',markdown)
        html=build_phase_display(updated,self.today,[self.future])['overview']
        self.assertIn('Adjust if needed',html);self.assertNotIn('<script>',html)
        with self.assertRaises(ValueError):validate_overview([new,copy.deepcopy(new)])
        with self.assertRaises(ValueError):replace_future_overview([], [old], self.day)
        requested=copy.deepcopy(self.plan);requested['new_plan_requested']=True
        session=copy.deepcopy(requested['sessions'][0]);session['id']='fresh-session'
        result=apply_coach_changes(requested,[{'op':'add','session_id':session['id'],'session':session,'reason':'Fresh block'}],self.profile,self.day)
        self.assertTrue(all(item['end_date']<self.day for item in result['planning_overview']))
        self.assertEqual(result['superseded_blocks'][-1]['planning_overview'],[old])

    def test_overview_validation_rejects_malformed_dates_and_preserves_omitted_guidance(self):
        saved = note(self.day, self.future)
        self.plan['planning_overview'] = [saved]
        unchanged = apply_coach_changes(self.plan, [], self.profile, self.day)
        self.assertEqual(unchanged['planning_overview'], [saved])
        self.assertEqual(unchanged['revision'], self.plan['revision'])
        cleared = apply_coach_changes(self.plan, [], self.profile, self.day, planning_overview=[])
        self.assertEqual(cleared['planning_overview'], [])
        self.assertGreater(cleared['revision'], self.plan['revision'])
        for bad in ({**saved, 'start_date': 2}, {**saved, 'end_date': '2026-02-30'},
                    {**saved, 'end_date': (self.today + timedelta(days=14)).isoformat()},
                    {**saved, 'focus': 'line one\nline two'}, {'start_date': self.day}, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_overview([saved, bad])
        with self.assertRaises(ValueError):
            replace_future_overview([], [saved] * 9, self.day)

    def test_race_detail_limits_and_missing_fitness_guards(self):
        route=parse_gpx(gpx([(0,10),(.01,50),(.02,10),(.03,30)]))
        payload=response(route);payload['summary']='Meaningful course explanation. '*100
        payload['strategies'][0]['sections'][0]['guidance']='Adapt effort to the measured climb. '*20
        evidence={'numeric_pacing_evidence_available':False,'planning_mode':'advanced'}
        result=validate_race_plan(payload,route,evidence)
        self.assertGreater(len(result['summary']),1600)
        prompt=make_race_prompt(self.profile,route,evidence,'Plan the race','Example',None)
        self.assertIn('conditional second-half choices',prompt)
        with self.assertRaises(ValueError):validate_race_plan(payload,route,{'numeric_pacing_evidence_available':False})
        numeric=response(route,numeric=True)
        with self.assertRaises(ValueError):validate_race_plan(numeric,route,evidence)

    def test_advanced_live_path_commits_overview_and_keeps_budget_and_last_good(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths={'ATHLETE_FILE':root/'athlete.md','PLAN_FILE':root/'training_plan.md','CANONICAL_FILE':root/'training_plan.json','DB_PATH':root/'data.sqlite'}
            paths['ATHLETE_FILE'].write_text('**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n')
            paths['PLAN_FILE'].write_text(render_plan(self.plan));paths['CANONICAL_FILE'].write_text(json.dumps(self.plan))
            with contextlib.closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL)');conn.commit()
            payload={'summary':'Detailed coaching explanation. '*100,'trajectory':'insufficient_evidence','reviews':[],'plan_changes':[],
                     'planning_overview':[note(self.day,self.future)]}
            generator=Mock(return_value=SimpleNamespace(text=json.dumps(payload),finish_reason='STOP',usage_metadata=None))
            client=SimpleNamespace(models=SimpleNamespace(generate_content=generator))
            with patch.multiple(ai_coach,**paths),patch.object(ai_coach,'api_client',return_value=client),patch.dict(os.environ,{'COACH_PLANNING_MODE':'advanced','COACH_MAX_DAILY_REQUESTS':'2'}),contextlib.redirect_stdout(io.StringIO()):
                result=ai_coach.run_daily_coach_check('Detailed planning',today=self.day)
                self.assertEqual(result['status'],'complete')
                self.assertEqual(ai_coach.run_daily_coach_check('Detailed planning',today=self.day)['status'],'unchanged')
                self.assertEqual(generator.call_count,1)
                self.assertEqual(generator.call_args.kwargs['config']['max_output_tokens'],16384)
                saved=paths['CANONICAL_FILE'].read_bytes()
                generator.return_value=SimpleNamespace(text='invalid JSON',finish_reason='STOP',usage_metadata=None)
                with self.assertRaises(Exception):ai_coach.run_daily_coach_check('A different question',today=self.day)
                self.assertEqual(paths['CANONICAL_FILE'].read_bytes(),saved)
                with self.assertRaisesRegex(RuntimeError,'budget exhausted'):ai_coach.run_daily_coach_check('Third question',today=self.day)
                self.assertEqual(generator.call_count,2)
            self.assertIn('Coaching plan overview',paths['PLAN_FILE'].read_text())
            with contextlib.closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                snapshot=json.loads(conn.execute('SELECT input_snapshot_json FROM coach_api_requests ORDER BY id LIMIT 1').fetchone()[0])
                self.assertEqual(snapshot['generation_identity']['planning_settings']['mode'],'advanced')


if __name__=='__main__':unittest.main()
