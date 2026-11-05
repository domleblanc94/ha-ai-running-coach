import contextlib
import copy
from datetime import date
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

import ai_coach
import coaching_eval
from planning_engine import (apply_plan_changes,build_evidence,configured_zone_conflicts,parse_profile,parse_plan,plan_conflicts,race_calendar_conflicts,validate_review)

PROFILE='''**Zone 1 (Recovery):** < 158 bpm
**Zone 2 (Endurance):** 158 - 166 bpm
**Zone 5 (Max):** >= 185 bpm
**Active Injuries:** None
**A-Race:** Example Trail 50km
**Date:** November 21, 2026
**Target Time:** Finish
'''


class CoachingExtensionsTests(unittest.TestCase):
    def setUp(self):
        self.profile=parse_profile(PROFILE)
        self.plan=parse_plan('| Saturday | 2026-11-07 | Run | 5 km | Comfortable effort |',self.profile)
        self.evidence={'today':'2026-11-05','recent_7d':[{'activity_id':101}]}
        self.response={'summary':'Review training in context.','trajectory':'on_track','reviews':[{'activity_id':101,'feedback':'Useful session.'}],'plan_changes':[]}

    def test_optional_explanations_backward_compatible_and_bounded(self):
        validate_review(self.response,self.evidence,self.plan,'2026-11-05',self.profile)
        extended=dict(self.response,weekly_priorities=[{'priority':'Discuss recovery','reason':'Subjective report is missing.','evidence':['activity 101: RPE unknown']},{'priority':'Review terrain preparation','reason':'Trail event is configured.','evidence':['A-Race goal']}],assessment={'reason':'Progress appears consistent with the available evidence.','evidence':['Recent training'],'uncertainties':['Subjective fatigue is not recorded.']},next_review_trigger='After the long run or a new athlete report.')
        validate_review(extended,self.evidence,self.plan,'2026-11-05',self.profile)
        with self.assertRaises(ValueError):validate_review(dict(extended,weekly_priorities=[]),self.evidence,self.plan,'2026-11-05',self.profile)
        with self.assertRaises(ValueError):validate_review(dict(extended,assessment={'reason':'fine'}),self.evidence,self.plan,'2026-11-05',self.profile)

    def test_race_date_calendar_guard_includes_unchanged_events(self):
        race={'id':'event-a','date':'2026-11-26','workout_type':'Race Day','amount':'Example Trail 50km','target':'Configured event goal.'}
        bad=copy.deepcopy(self.plan);bad['sessions'].append(race)
        with self.assertRaisesRegex(ValueError,'Race calendar conflict'):apply_plan_changes(bad,[],self.profile,'2026-11-05')
        corrected=dict(race,date='2026-11-21',goal_priority='A-Race')
        result=apply_plan_changes(bad,[{'op':'update','session_id':'event-a','session':corrected,'reason':'Use the confirmed athlete goal date.'}],self.profile,'2026-11-05')
        self.assertFalse(race_calendar_conflicts(result,self.profile,'2026-11-05'))
        unknown=dict(race,id='unidentified',amount='Unknown event',goal_priority='Unconfigured')
        with self.assertRaises(ValueError):apply_plan_changes(self.plan,[{'op':'add','session_id':unknown['id'],'session':unknown,'reason':'Test'}],self.profile,'2026-11-05')
        training=dict(self.plan['sessions'][0],workout_type='Race-pace training')
        valid=dict(self.plan,sessions=[training])
        apply_plan_changes(valid,[],self.profile,'2026-11-05')

    def test_zone_punctuation_and_valid_conditional_advice(self):
        for target in ('Z2 (123-143 bpm)','Zone 2 HR 123\u2013143 bpm','Z2:123-143','Zone 2 range 123 to 143'):
            self.assertTrue(configured_zone_conflicts(target,self.profile),target)
        self.assertFalse(configured_zone_conflicts('Zone 2:158-166 bpm',self.profile))
        self.assertFalse(configured_zone_conflicts('Aim for lower Zone 2 HR (158-162 bpm).',self.profile))
        self.assertFalse(configured_zone_conflicts('Zone 2: 162-166 bpm',self.profile))
        self.assertFalse(configured_zone_conflicts('Zone 1 (120-145 bpm)',self.profile))
        self.assertFalse(configured_zone_conflicts('Zone 1-2 (<162 bpm)',self.profile))
        self.assertFalse(configured_zone_conflicts('Zone 5 (190-200 bpm)',self.profile))
        self.assertTrue(configured_zone_conflicts('Zone 2 (158-170 bpm)',self.profile))
        self.assertFalse(configured_zone_conflicts('Recovery cap 140 bpm; walking if needed.',self.profile))
        self.assertFalse(plan_conflicts('Run or walk as needed. Stop if hamstring pain or injury symptoms occur.',self.profile))

    def test_evidence_weekly_ascent_and_duration_with_missing_coverage(self):
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('CREATE TABLE activities(id INTEGER,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL,elevation_gain_m REAL)')
            conn.executemany('INSERT INTO activities VALUES(?,?,?,?,?,?)',[(1,'Run','2026-11-04',6000,2400,200),(2,'Run','2026-11-03',3000,None,None),(3,'Ride','2026-11-04',100000,6000,2000)])
            before=conn.total_changes
            result=build_evidence(conn,'2026-11-05');week=result['running_weeks_8'][-1]
            self.assertEqual((week['run_count'],week['ascent_m'],week['ascent_coverage'],week['longest_duration_min'],week['duration_coverage']),(2,200,1,40,1))
            self.assertEqual(result['feedback_history'],[])
            self.assertIn('total',str(result['feedback_units']['carbs_g']).lower())
            self.assertEqual(conn.total_changes,before)
        finally:conn.close()


class CoachingEvaluationTests(unittest.TestCase):
    def response(self):return {'summary':'Useful training; subjective recovery remains uncertain.','trajectory':'insufficient_evidence','reviews':[],'plan_changes':[]}

    def test_offline_default_never_calls_model_and_does_not_certify_quality(self):
        with patch.object(coaching_eval,'run_live',side_effect=AssertionError('Offline must not call models')),contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(coaching_eval.main([]),0)
        report=json.loads(output.getvalue())
        self.assertEqual(report['api_attempts'],0);self.assertEqual(len(report['cases']),5)
        case=coaching_eval.load_cases()[0]
        result=coaching_eval.assess_response(case,self.response())
        self.assertTrue(result['schema_pass']);self.assertTrue(result['manual_review_required'])
        self.assertFalse(result['structured_explanation_present'])
        self.assertFalse(coaching_eval.assess_response(case,'truncated JSON')['schema_pass'])

    def test_explicit_live_evaluation_caps_calls_and_never_applies_plans(self):
        response=types.SimpleNamespace(text=json.dumps(self.response()),usage_metadata=None)
        generator=unittest.mock.Mock(return_value=response)
        client=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generator))
        with tempfile.TemporaryDirectory() as d,patch.object(ai_coach,'api_client',return_value=client):
            path=Path(d)/'evaluation.json'
            report=coaching_eval.run_live(coaching_eval.load_cases(),path,max_calls=2,max_tokens=1024)
            self.assertEqual(generator.call_count,2);self.assertEqual(report['api_attempts'],2)
            self.assertEqual(len(report['requests']),2)
            self.assertEqual(list(Path(d).iterdir()),[path])
        with self.assertRaises(ValueError):coaching_eval.run_live([],Path('not-written.json'),max_calls=6)

    def test_saved_request_evaluation_reads_database_only(self):
        case=coaching_eval.load_cases()[0]
        snapshot={k:case[k] for k in ('profile','plan','evidence','question')}
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'requests.sqlite'
            conn=sqlite3.connect(path)
            conn.execute('CREATE TABLE coach_api_requests(id INTEGER,input_snapshot_json TEXT,response_text TEXT)')
            conn.execute('INSERT INTO coach_api_requests VALUES(1,?,?)',(json.dumps(snapshot),json.dumps(self.response())));conn.commit();conn.close()
            before=path.read_bytes()
            loaded,response=coaching_eval.saved_request_case(path,1)
            self.assertEqual(loaded['case_id'],'saved-request-1')
            self.assertTrue(coaching_eval.assess_response(loaded,response)['schema_pass'])
            self.assertEqual(path.read_bytes(),before)


if __name__=='__main__':unittest.main()
