"""Discipline boundaries, legacy compatibility and joint coaching, entirely offline."""
import copy
import hashlib
import json
import sqlite3
import unittest
from contextlib import closing, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import types
from unittest.mock import Mock,patch

import ai_coach
from import_activities import validate_activity
from planning_engine import (parse_profile,parse_plan,render_plan,validate_plan,apply_plan_changes,
                             validate_review,configured_zone_conflicts,build_evidence)
from sports import (effective_goal_sport,session_sport,activity_sport,activity_sub_sport)
from training_calendar import running_session_conflicts,training_day_conflicts,race_calendar_conflicts


TODAY='2026-11-06'
BASE='**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n'
GOAL='**A-Race:** Autumn event\n**Date:** 2026-11-08\n**Target Time:** Finish\n'


def session(sid,sport='running',day='2026-11-07',**extra):
    return dict(id=sid,date=day,sport=sport,workout_type='Intervals',amount='30 min',target='Coach-selected effort',**extra)


def plan(profile,sessions):
    return {'schema_version':1,'revision':1,'profile_hash':profile['source_hash'],'goals':copy.deepcopy(profile['goals']),'sessions':sessions}


def add(value):
    return {'op':'add','session_id':value['id'],'session':value,'reason':'Joint evidence-led schedule.'}


def workout(sport='running',**extra):
    return {'name':'Coach workout','sport':sport,'steps':[{'intensity':'active','duration':{'type':'time','seconds':600},'target':{'type':'open'}}],**extra}


class MultisportProfileTests(unittest.TestCase):
    def test_legacy_goals_and_source_hash_unchanged_when_mode_changes(self):
        text=BASE+GOAL
        profile=parse_profile(text)
        self.assertEqual(profile['source_hash'],hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(profile['goals'],[{'priority':'A-Race','event':'Autumn event','date':'2026-11-08','target_time':'Finish'}])
        cycled=parse_profile(text+'**Training Mode:** cycling\n')
        self.assertEqual(cycled['goals'],profile['goals'])
        self.assertEqual(effective_goal_sport(cycled['goals'][0]),'running')
        old=session('old');old.pop('sport')
        saved=plan(profile,[old]);before=copy.deepcopy(saved)
        validate_plan(saved,profile)
        self.assertEqual(saved,before)
        self.assertNotIn('| Sport |',render_plan(saved))

    def test_sport_zones_are_separate_and_unknown_is_not_running_fallback(self):
        p=parse_profile(BASE+'**Zone 2 (Run):** 130 - 150 bpm\n**Training Mode:** cycling\n**Cycling Zone 2 (Bike):** 110 - 130 bpm\n')
        self.assertFalse(configured_zone_conflicts('Zone 2 115-125 bpm',p,'cycling'))
        self.assertTrue(configured_zone_conflicts('Zone 2 135-145 bpm',p,'cycling'))
        self.assertTrue(configured_zone_conflicts('Zone 2 135-145 bpm',p,'swimming'))
        self.assertFalse(configured_zone_conflicts('Zone 2 135-145 bpm',p,'running'))
        for mode in ('cycling','swimming'):
            self.assertEqual(parse_profile('**Training Mode:** '+mode)['zones'],{})
        with self.assertRaises(ValueError):parse_profile('**Training Mode:** triathlon')

    def test_optional_sport_values_and_subset_rules_preserve_inactive_settings(self):
        raw=BASE+'**Training Mode:** cycling\n**Weekly Availability:** Monday, Wednesday\n**Running Availability:** Saturday\n**Cycling Availability:** Wednesday\n**Cycling FTP:** 250 W\n**Swimming CSS:** 2:05 /100m\n**Pool Length:** 25 yd\n**Swimming Type:** lap_swimming\n'
        p=parse_profile(raw)
        self.assertEqual(p['sport_settings']['running']['available_days'],['Saturday'])
        self.assertEqual(p['sport_settings']['cycling']['ftp_watts'],250)
        self.assertEqual(p['sport_settings']['swimming']['css_seconds_per_100m'],125)
        self.assertAlmostEqual(p['sport_settings']['swimming']['pool_length_metres'],22.86)
        self.assertEqual(p['sport_settings']['swimming']['pool_length_unit'],'yd')
        with self.assertRaisesRegex(ValueError,'global training days'):parse_profile(raw.replace('**Training Mode:** cycling','**Training Mode:** triathlon'))
        with self.assertRaises(ValueError):parse_profile(BASE+'**Cycling FTP:** 0 W')
        with self.assertRaises(ValueError):parse_profile(BASE+'**Swimming CSS:** 1:65')
        with self.assertRaises(ValueError):parse_profile(BASE+'**Pool Length:** 25')
        with self.assertRaises(ValueError):parse_profile(BASE+'**Cycling Zone 1 (Easy):** 100 - 150 bpm\n**Cycling Zone 2 (Hard):** 140 - 160 bpm')


class MultisportPlanTests(unittest.TestCase):
    def setUp(self):self.profile=parse_profile(BASE+'**Training Mode:** triathlon\n')

    def test_explicit_cycling_intervals_and_bike_run_brick_allow_shared_day(self):
        legs=[session('bike','cycling',brick={'id':'weekend','order':1}),session('run',brick={'id':'weekend','order':2})]
        saved=plan(self.profile,legs)
        self.assertFalse(running_session_conflicts(saved,TODAY))
        self.assertEqual(session_sport(legs[0]),'cycling')
        result=apply_plan_changes(saved,[],self.profile,TODAY)
        self.assertEqual(result['sessions'],sorted(legs,key=lambda item:item['id']))
        with self.assertRaisesRegex(ValueError,'Multiple-running-session'):
            apply_plan_changes(saved,[add(session('second-run'))],self.profile,TODAY)
        apply_plan_changes(saved,[add(session('strength','strength'))],self.profile,TODAY)

    def test_brick_links_cannot_be_partial_duplicate_or_cross_date(self):
        base=[session('bike','cycling',brick={'id':'b','order':1}),session('run',brick={'id':'b','order':2})]
        cases=[base[:1], [base[0],dict(base[1],brick={'id':'b','order':1})],
               [base[0],dict(base[1],date='2026-11-08')],
               [base[0],dict(base[1],sport='cycling')],
               [base[0],dict(base[1],brick={'id':'b','order':3})]]
        for values in cases:
            with self.subTest(values=values),self.assertRaises(ValueError):validate_plan(plan(self.profile,values),self.profile)

    def test_multisport_event_needs_matching_configured_goal_and_excludes_other_endurance(self):
        p=parse_profile(BASE+GOAL+'**Sport:** multisport\n**Weekly Availability:** Saturday\n')
        event=session('event','multisport','2026-11-08',goal_priority='A-Race')
        saved=plan(p,[event])
        self.assertFalse(race_calendar_conflicts(saved,p,TODAY))
        self.assertFalse(training_day_conflicts(saved,p,TODAY))
        apply_plan_changes(saved,[],p,TODAY)
        for sport in ('running','cycling','swimming','multisport'):
            extra=session('extra',sport,'2026-11-08',**({'goal_priority':'A-Race'} if sport=='multisport' else {}))
            with self.subTest(sport=sport),self.assertRaisesRegex(ValueError,'Race-day session conflict'):
                apply_plan_changes(saved,[add(extra)],p,TODAY)
        for bad in (dict(event,workout=workout()), {k:v for k,v in event.items() if k!='goal_priority'},dict(event,sport='cycling')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):apply_plan_changes(plan(p,[bad]),[],p,TODAY)
        milestone=parse_profile(BASE+GOAL.replace('Autumn event','Training milestone: first brick')+'**Sport:** multisport\n')
        with self.assertRaisesRegex(ValueError,'configured triathlon'):
            apply_plan_changes(plan(milestone,[event]),[],milestone,TODAY)

    def test_specific_training_days_and_goal_sport_are_not_mode_inferred(self):
        p=parse_profile(BASE+GOAL+'**Training Mode:** triathlon\n**Weekly Availability:** Saturday, Sunday\n**Cycling Availability:** Sunday\n')
        self.assertEqual(len(training_day_conflicts(plan(p,[session('bike','cycling')]),p,TODAY)),1)
        self.assertFalse(training_day_conflicts(plan(p,[session('run')]),p,TODAY))
        race=session('bike-event','cycling','2026-11-08',goal_priority='A-Race')
        self.assertIn('race sport',race_calendar_conflicts(plan(p,[race]),p,TODAY)[0])

    def test_explicit_endurance_sport_cannot_use_rest_label_to_bypass_availability(self):
        p=parse_profile(BASE+'**Weekly Availability:** Monday\n')
        for sport in ('running','cycling','swimming'):
            value=session('offday',sport);value['workout_type']='Rest'
            self.assertEqual(len(training_day_conflicts(plan(p,[value]),p,TODAY)),1)
        rest=session('offday','rest');rest['workout_type']='Rest'
        self.assertFalse(training_day_conflicts(plan(p,[rest]),p,TODAY))
        rest.pop('sport')
        self.assertFalse(training_day_conflicts(plan(p,[rest]),p,TODAY))

    def test_structured_workout_sport_and_explicit_pool_config_must_match(self):
        swim=session('pool','swimming',sub_sport='lap_swimming',workout=workout('swimming',subSport='lap_swimming',poolLengthMetres=25,poolLengthUnit='m'))
        p=parse_profile(BASE+'**Pool Length:** 25 m\n')
        apply_plan_changes(plan(p,[session('existing',day='2026-11-09')]),[add(swim)],p,TODAY)
        for bad in (dict(swim,sub_sport='open_water'),dict(swim,sport='cycling'),dict(swim,workout=workout('swimming',subSport='lap_swimming',poolLengthMetres=50,poolLengthUnit='m'))):
            with self.subTest(bad=bad),self.assertRaises(ValueError):apply_plan_changes(plan(p,[session('existing',day='2026-11-09')]),[add(bad)],p,TODAY)
        with self.assertRaisesRegex(ValueError,'explicitly configured'):
            apply_plan_changes(plan(self.profile,[session('existing',day='2026-11-09')]),[add(swim)],self.profile,TODAY)

    def test_typed_markdown_round_trip_sports_multiple_days_and_legacy_rows(self):
        old=session('old',day='2026-11-06');old.pop('sport')
        sessions=[old,session('bike','cycling'),session('swim','swimming',sub_sport='open_water')]
        text=render_plan(plan(self.profile,sessions))
        self.assertIn('| Date | Sport |',text)
        reread=parse_plan(text,self.profile)
        self.assertEqual([row.get('sport') for row in reread['sessions']],[None,'cycling','swimming'])
        self.assertEqual(reread['sessions'][2]['sub_sport'],'open_water')
        self.assertEqual(len({row['id'] for row in reread['sessions']}),3)
        self.assertEqual(render_plan(reread),text)

    def test_changed_pool_setting_requires_review_of_unchanged_future_workouts_only(self):
        p=parse_profile(BASE+'**Pool Length:** 50 m\n')
        swim=session('pool','swimming',sub_sport='lap_swimming',workout=workout('swimming',subSport='lap_swimming',poolLengthMetres=25,poolLengthUnit='m'))
        original=plan(p,[swim]);before=copy.deepcopy(original)
        with self.assertRaisesRegex(ValueError,'explicitly configured'):
            apply_plan_changes(original,[],p,TODAY)
        self.assertEqual(original,before)
        fixed=copy.deepcopy(swim);fixed['workout']['poolLengthMetres']=50
        result=apply_plan_changes(original,[dict(add(fixed),op='update')],p,TODAY)
        self.assertEqual(result['sessions'][0]['workout']['poolLengthMetres'],50)
        historic=copy.deepcopy(swim);historic['date']='2026-11-05'
        apply_plan_changes(plan(p,[historic,session('future')]),[],p,TODAY)

    def test_new_ai_requires_explicit_sport_but_legacy_plan_remains_valid(self):
        old=session('old');old.pop('sport')
        saved=plan(self.profile,[old])
        update={'op':'update','session_id':'old','session':old,'reason':'Keep athlete-defined session.'}
        payload={'summary':'Review','trajectory':'insufficient_evidence','reviews':[],'plan_changes':[update]}
        evidence={'recent_7d':[]}
        validate_review(payload,evidence,saved,TODAY,self.profile)
        with self.assertRaisesRegex(ValueError,'explicit sport'):
            validate_review(payload,evidence,saved,TODAY,self.profile,require_explicit_sport=True)
        update['session']=dict(old,sport='running')
        validate_review(payload,evidence,saved,TODAY,self.profile,require_explicit_sport=True)

    def test_joint_prompt_is_compact_and_keeps_existing_call_output_budget(self):
        empty=plan(self.profile,[]);empty['new_plan_requested']=True
        evidence={'today':TODAY,'recent_7d':[]}
        prompt=ai_coach.make_prompt(self.profile,empty,evidence,'Build a coordinated plan.')
        self.assertIn('at most fourteen calendar entries',prompt)
        self.assertIn('at most one optional structured workout',prompt)
        self.assertIn('Never copy running zones',prompt)
        self.assertIn('Every added or updated session MUST include sport',prompt)
        self.assertIn('joint triathlon schedule',prompt)
        running=parse_profile(BASE)
        self.assertIn('at most seven calendar entries',ai_coach.make_prompt(running,plan(running,[]),evidence,'Run'))


class MultisportEvidenceTests(unittest.TestCase):
    def test_sport_metrics_unknown_coverage_and_unallocated_multisport_duration(self):
        with closing(sqlite3.connect(':memory:')) as conn:
            conn.execute('CREATE TABLE activities(id INTEGER,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL,timer_duration_s REAL,avg_watts REAL,weighted_avg_watts REAL)')
            data=[(1,'running',TODAY,5000,1800,1850,None,None),(2,'indoor_cycling',TODAY,None,3600,3600,180,195),
                  (3,'lap_swimming',TODAY,1000,None,1200,None,None),(4,'triathlon',TODAY,None,None,7200,None,None),
                  (5,'open_water_swimming',TODAY,None,None,None,None,None),(6,'cycling',TODAY,0,0,0,0,0)]
            conn.executemany('INSERT INTO activities VALUES(?,?,?,?,?,?,?,?)',data)
            evidence=build_evidence(conn,TODAY)
            self.assertEqual(conn.execute('SELECT * FROM activities').fetchall(),data)
        week=evidence['sport_weeks_8'][-1]
        self.assertEqual(week['by_sport']['running']['distance_m'],5000)
        self.assertEqual(week['by_sport']['cycling']['timer_minutes'],60)
        self.assertEqual(week['by_sport']['cycling']['timer_duration_coverage'],2)
        self.assertEqual(week['by_sport']['swimming']['activity_count'],2)
        self.assertEqual(week['by_sport']['swimming']['timer_duration_coverage'],1)
        self.assertEqual(week['by_sport']['multisport']['timer_minutes'],120)
        self.assertEqual(week['combined_duration']['timer_minutes'],round((1850+3600+1200+7200)/60,1))
        self.assertNotIn('distance_m',week['combined_duration'])
        self.assertEqual(evidence['sport_weeks_8'][0]['by_sport'],{})
        recent={row['activity_id']:row for row in evidence['recent_7d']}
        self.assertEqual(recent[2]['avg_watts'],180)
        self.assertEqual(recent[3]['swim_timer_pace_seconds_per_100m'],120)
        self.assertIsNone(recent[3]['pace_min_km'])
        self.assertIsNone(recent[5].get('swim_timer_pace_seconds_per_100m'))
        self.assertEqual(recent[4]['sport'],'multisport')
        self.assertEqual(evidence['running_28d']['total_km'],5)

    def test_recorded_aliases_and_local_import_do_not_guess_missing_measurements(self):
        for kind,sport in [('TrailRun','running'),('indoor_cycling','cycling'),('lap_swimming','swimming'),('open_water_swimming','swimming'),('multi_sport','multisport')]:
            self.assertEqual(activity_sport(kind),sport)
        self.assertEqual(activity_sub_sport('lap_swimming'),'lap_swimming')
        row=validate_activity({'id':1,'start_date':TODAY,'type':'Ride','timer_duration_s':3600,'avg_watts':175,'weighted_avg_watts':190})
        self.assertNotIn('moving_time_s',row)
        self.assertEqual(row['avg_watts'],175)
        self.assertEqual(row['timer_duration_s'],3600)


class JointReviewTests(unittest.TestCase):
    def test_one_review_commits_joint_plan_preserves_history_and_reuses_cache(self):
        text=BASE+'**Training Mode:** triathlon\n**Pool Length:** 25 m\n'
        profile=parse_profile(text)
        past=session('past',day='2026-11-05');past.pop('sport')
        old=session('old');old.pop('sport')
        saved=plan(profile,[past,old])
        run=dict(old,sport='running',brick={'id':'weekend','order':2})
        bike=session('bike','cycling',brick={'id':'weekend','order':1},workout=workout('cycling'))
        swim=session('swim','swimming','2026-11-08',sub_sport='lap_swimming',workout=workout('swimming',subSport='lap_swimming',poolLengthMetres=25,poolLengthUnit='m'))
        payload={'summary':'One coordinated review.','trajectory':'insufficient_evidence','reviews':[],
                 'plan_changes':[dict(add(run),op='update'),add(bike),add(swim)]}
        generate=Mock(return_value=types.SimpleNamespace(text=json.dumps(payload),usage_metadata=None))
        client=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
        env={'COACH_PROVIDER':'gemini','COACH_MODELS':'synthetic-primary,synthetic-fallback','COACH_ACTIVITY_SOURCE':'offline','COACH_MAX_DAILY_REQUESTS':'2'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            paths={'SCRIPT_DIR':root,'ATHLETE_FILE':root/'athlete.md','PLAN_FILE':root/'training_plan.md',
                   'CANONICAL_FILE':root/'training_plan.json','DB_PATH':root/'data.sqlite'}
            paths['ATHLETE_FILE'].write_text(text,encoding='utf-8')
            paths['PLAN_FILE'].write_text(render_plan(saved),encoding='utf-8')
            paths['CANONICAL_FILE'].write_text(json.dumps(saved),encoding='utf-8')
            with patch.multiple(ai_coach,**paths),patch.dict(os.environ,env,clear=True),patch.object(ai_coach,'api_client',return_value=client),redirect_stdout(io.StringIO()):
                self.assertEqual(ai_coach.run_daily_coach_check(today=TODAY)['status'],'complete')
                self.assertEqual(ai_coach.run_daily_coach_check(today=TODAY)['status'],'unchanged')
                self.assertEqual(os.environ['COACH_MAX_DAILY_REQUESTS'],'2')
            current=json.loads(paths['CANONICAL_FILE'].read_text(encoding='utf-8'))
            self.assertEqual(current['sessions'][0],past)
            self.assertEqual({s.get('sport') for s in current['sessions'][1:]},{'running','cycling','swimming'})
            self.assertIn('| Date | Sport |',paths['PLAN_FILE'].read_text(encoding='utf-8'))
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT request_kind,status,prompt_version FROM coach_api_requests').fetchall(),[('daily','complete',ai_coach.PROMPT_VERSION)])
                self.assertEqual(json.loads(conn.execute('SELECT response_text FROM coach_api_requests').fetchone()[0]),payload)
            self.assertEqual(generate.call_count,1)


if __name__=='__main__':unittest.main()
