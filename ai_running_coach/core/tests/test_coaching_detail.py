"""Review preferences and useful interval instructions without extra model calls."""
from contextlib import closing, redirect_stdout
import copy
from datetime import date
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import ai_coach
from generate_dashboard import build_dashboard
from planning_engine import parse_profile, apply_plan_changes, render_plan, validate_plan, validate_review
from session_instructions import quality_details_issue, workout_instruction_lines

PROFILE='**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n'
TODAY='2026-11-17'
PRESCRIPTION='Warm-up: 10 min easy; 3 x 2 min at controlled hard effort; Recovery: 2 min easy jog between efforts; Cooldown: 10 min easy.'


def workout():
    def step(kind,seconds,notes):
        return {'intensity':kind,'duration':{'type':'time','seconds':seconds},'target':{'type':'open'},'notes':notes}
    return {'name':'Clear intervals','sport':'running','steps':[
        step('warmup',600,'Easy jog'),step('active',120,'Controlled hard effort'),step('recovery',120,'Easy jog'),
        step('active',120,'Controlled hard effort'),step('recovery',120,'Easy jog'),
        step('active',120,'Controlled hard effort'),step('cooldown',600,'Easy jog')]}


def session(kind='Intervals',target=PRESCRIPTION,**extra):
    return {'id':'session','date':TODAY,'sport':'running','workout_type':kind,'amount':'30 min','target':target,**extra}


def plan(profile,entry):
    return {'schema_version':1,'revision':1,'profile_hash':profile['source_hash'],'goals':profile['goals'],'sessions':[entry]}


class CoachingDetailTests(unittest.TestCase):
    def test_profile_default_preferences_and_prompt_use_same_budget(self):
        self.assertEqual(parse_profile(PROFILE)['review_detail'],'standard')
        for detail,sentences in [('concise','1-2 useful sentences'),('standard','3-4 useful sentences'),('detailed','4-6 useful sentences')]:
            profile=parse_profile(PROFILE+'**Review Detail:** '+detail+'\n')
            for starter in (False,True):
                saved=plan(profile,session('Easy run','Comfortable effort'))
                if starter:saved['new_plan_requested']=True
                prompt=ai_coach.make_prompt(profile,saved,{'today':TODAY,'recent_7d':[]},'Review my training')
                self.assertIn(sentences,prompt)
                self.assertIn('do not request extra calls or raise a budget',prompt)
                self.assertIn('what it means for the athlete goal',prompt)
                self.assertNotIn('Keep the summary under 600 characters',prompt)
                self.assertEqual(json.loads(prompt.split('\nDATA:\n')[1])['profile']['review_detail'],detail)
        with self.assertRaisesRegex(ValueError,'Review Detail'):
            parse_profile(PROFILE+'**Review Detail:** unlimited\n')

    def test_new_vague_intervals_rejected_but_legacy_and_continuous_sessions_unchanged(self):
        profile=parse_profile(PROFILE);saved=plan(profile,session(target='Zone 2'))
        before=copy.deepcopy(saved)
        validate_plan(saved,profile)
        self.assertEqual(apply_plan_changes(saved,[],profile,TODAY),saved)
        for kind in ('Intervals','Easy run with strides','Hill repeats','Fartlek'):
            change={'op':'update','session_id':'session','session':session(kind,target='Warm-up: 10 min easy; 3 x 2 min; Cooldown: 10 min easy.'),'reason':'New prescription'}
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'Incomplete workout instructions'):
                apply_plan_changes(saved,[change],profile,TODAY)
        self.assertEqual(saved,before)
        for kind in ('Tempo run','Easy run','Long run','Rest'):
            update=session(kind,target='Coach-selected comfortable effort',sport='rest' if kind=='Rest' else 'running')
            result=apply_plan_changes(saved,[{'op':'update','session_id':'session','session':update,'reason':'Continuous session'}],profile,TODAY)
            self.assertEqual(result['sessions'][0],update)

    def test_complete_readable_prescription_needs_real_effort_and_recovery(self):
        self.assertIsNone(quality_details_issue(session()))
        self.assertIsNone(quality_details_issue(session(target=PRESCRIPTION.replace('3 x 2 min at controlled hard effort','4 × 400 m at 5k pace'))))
        for text in (PRESCRIPTION.replace('at controlled hard effort',''),PRESCRIPTION.replace('Recovery: 2 min easy jog between efforts; ',''),PRESCRIPTION.replace('Warm-up: 10 min easy; ',''),PRESCRIPTION.replace('Cooldown: 10 min easy.','')):
            self.assertIsNotNone(quality_details_issue(session(target=text)),text)
        profile=parse_profile(PROFILE);saved=plan(profile,session(target='Old vague instructions'))
        result=apply_plan_changes(saved,[{'op':'update','session_id':'session','session':session(),'reason':'Clarify the session'}],profile,TODAY)
        self.assertEqual(result['sessions'][0]['target'],PRESCRIPTION)

    def test_structured_steps_require_phase_order_recovery_effort_and_matching_repetitions(self):
        valid=session(target='Follow the explicit steps',workout=workout())
        self.assertIsNone(quality_details_issue(valid))
        for mutation in ('warmup','recovery','effort','count'):
            broken=copy.deepcopy(valid)
            if mutation=='warmup':broken['workout']['steps'].pop(0)
            if mutation=='recovery':broken['workout']['steps'].pop(2)
            if mutation=='effort':broken['workout']['steps'][1].pop('notes')
            if mutation=='count':broken['target']=PRESCRIPTION.replace('3 x','4 x')
            self.assertIsNotNone(quality_details_issue(broken),mutation)
        self.assertIn('2. Work: 2 min · Controlled hard effort',workout_instruction_lines(valid))
        paced=copy.deepcopy(valid);paced['workout']['steps'][1]['target']={'type':'speed','lowMps':1000/330,'highMps':1000/300}
        self.assertIn('5:00-5:30 min/km',workout_instruction_lines(paced)[1])

    def test_next_review_sees_only_upcoming_incomplete_sessions(self):
        profile=parse_profile(PROFILE)
        saved=plan(profile,session(target='Intervals to be specified'))
        saved['sessions'] += [dict(session(target='Old strides'),id='past',date='2026-11-16'),dict(session('Tempo run','Steady controlled effort'),id='continuous',date='2026-11-18')]
        prompt=ai_coach.make_prompt(profile,saved,{'today':TODAY,'recent_7d':[]},'Review')
        data=json.loads(prompt.split('\nDATA:\n')[1])
        self.assertEqual([item['id'] for item in data['incomplete_session_details']],['session'])
        self.assertIn('Clarify the upcoming incomplete_session_details',prompt)

    def test_multiple_repetition_blocks_and_amount_counts_match_explicit_steps(self):
        first=workout()['steps'][1]
        recovery=workout()['steps'][2]
        second=copy.deepcopy(first);second['duration']={'type':'distance','metres':100}
        steps=[workout()['steps'][0],first,recovery,first,recovery,second,recovery,second,workout()['steps'][-1]]
        value=session(target='2 x 2 min controlled; 2 x 100 m relaxed',workout=dict(workout(),steps=steps))
        self.assertIsNone(quality_details_issue(value))
        value['amount']='Including 3 x 100 m strides'
        self.assertIsNotNone(quality_details_issue(value))
        value['amount']='Including 2 x 100 m strides'
        self.assertIsNone(quality_details_issue(value))
        # Explicit lap-button recoveries are intentional and not invented durations.
        for step in steps:
            if step['intensity']=='recovery':step['duration']={'type':'open'}
        self.assertIsNone(quality_details_issue(value))

    def test_saved_steps_and_detailed_feedback_render_without_download_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory,redirect_stdout(io.StringIO()):
            root=Path(directory);(root/'athlete.md').write_text(PROFILE,encoding='utf-8')
            profile=parse_profile(PROFILE);entry=session(target='Follow the explicit steps',workout=workout())
            saved=plan(profile,entry);markdown=render_plan(saved)
            self.assertIn('## Workout instructions',markdown)
            self.assertIn('3. Recovery: 2 min',markdown)
            (root/'training_plan.json').write_text(json.dumps(saved),encoding='utf-8')
            (root/'training_plan.md').write_text(markdown,encoding='utf-8')
            db=root/'data.sqlite'
            feedback=('The supplied session lasted 30 minutes over 5 km, giving an average moving pace of 6 min/km. '
                      'That describes this recording, but without heart-rate, elevation and effort reports it does not establish race fitness. '
                      'Use your next comparable easy run to check whether a conversational effort produces a similar pace, and add an effort report afterward. '
                      'A consistent result would be more informative than chasing a faster split in isolation.\n\n'
                      'Recovery and how you feel the following day will help decide whether progression is appropriate. <script>unsafe()</script>')
            summary='Your recent work provides useful evidence.\n\nRecovery reports would improve the next decision.'
            with closing(sqlite3.connect(db)) as conn,conn:
                ai_coach.ensure_review_schema(conn)
                conn.execute('INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,ai_feedback) VALUES(1,?,?,?,?,?)',('Run',TODAY,5000,1800,feedback))
                payload={'summary':summary,'trajectory':'insufficient_evidence','reviews':[{'activity_id':1,'feedback':feedback}],'plan_changes':[]}
                validate_review(payload,{'recent_7d':[{'activity_id':1}]},saved,TODAY,profile)
                conn.execute('INSERT INTO coach_review_jobs(fingerprint,status,prompt_version,updated_at) VALUES(?,?,?,?)',('example','pending',ai_coach.PROMPT_VERSION,TODAY));conn.commit()
                resulting=apply_plan_changes(saved,[],profile,TODAY)
                with patch.multiple(ai_coach,CANONICAL_FILE=root/'training_plan.json',PLAN_FILE=root/'training_plan.md'):
                    ai_coach.commit_review(conn,'example',payload,saved,resulting,'{}')
                self.assertEqual(conn.execute('SELECT summary_text FROM daily_coach_reviews').fetchone()[0],summary)
                self.assertEqual(conn.execute('SELECT ai_feedback FROM activities WHERE id=1').fetchone()[0],feedback)
            before={name:(root/name).read_bytes() for name in ('data.sqlite','training_plan.json','training_plan.md')}
            html=build_dashboard(db,root/'training_plan.md',root/'dashboard.html',today=date.fromisoformat(TODAY)).read_text(encoding='utf-8')
            self.assertIn('Workout instructions &amp; preparation',html)
            self.assertIn('2. Work: 2 min',html)
            self.assertIn('whitespace-pre-line',html)
            self.assertNotIn('<script>unsafe()</script>',html)
            payload=json.loads(re.search(r'const workoutSessions = (.*);',html)[1])
            self.assertEqual(payload['session']['instruction_lines'],workout_instruction_lines(entry))
            activities=json.loads(re.search(r'const completedActivities = (.*);',html)[1])
            self.assertTrue(activities[TODAY][0]['feedback_current'])
            self.assertEqual(activities[TODAY][0]['comment'],feedback)
            self.assertEqual(before,{name:(root/name).read_bytes() for name in before})

    def test_review_text_bounds_reject_controls_and_empty_feedback_but_allow_paragraphs(self):
        profile=parse_profile(PROFILE);saved=plan(profile,session())
        value={'summary':'Useful observation.\n\nPractical next action.','trajectory':'insufficient_evidence','reviews':[{'activity_id':1,'feedback':'Specific evidence.\n\nSpecific next action.'}],'plan_changes':[]}
        evidence={'recent_7d':[{'activity_id':1}]}
        validate_review(value,evidence,saved,TODAY,profile)
        for text in ('\x00bad','\x1bbad','\ud800','   ','x'*2001):
            with self.subTest(text=repr(text[:8])),self.assertRaises(ValueError):validate_review(dict(value,summary=text),evidence,saved,TODAY,profile)
        for text in ('\x00bad','   ','x'*1801):
            with self.assertRaises(ValueError):validate_review(dict(value,reviews=[{'activity_id':1,'feedback':text}]),evidence,saved,TODAY,profile)

    def test_invalid_quality_response_retains_files_then_detailed_review_uses_one_attempt(self):
        with tempfile.TemporaryDirectory() as directory,redirect_stdout(io.StringIO()):
            root=Path(directory);profile_text=PROFILE+'**Review Detail:** detailed\n';profile=parse_profile(profile_text)
            saved=plan(profile,session(target='Old interval detail is missing'))
            paths={'ATHLETE_FILE':root/'athlete.md','PLAN_FILE':root/'training_plan.md','CANONICAL_FILE':root/'training_plan.json','DB_PATH':root/'data.sqlite','SCRIPT_DIR':root}
            paths['ATHLETE_FILE'].write_text(profile_text,encoding='utf-8');paths['CANONICAL_FILE'].write_text(json.dumps(saved),encoding='utf-8');paths['PLAN_FILE'].write_text(render_plan(saved),encoding='utf-8')
            before=(paths['CANONICAL_FILE'].read_bytes(),paths['PLAN_FILE'].read_bytes())
            bad={'summary':'Review training.\n\nMore context.','trajectory':'insufficient_evidence','reviews':[],'plan_changes':[{'op':'update','session_id':'session','session':session(target='Intervals'),'reason':'Clarify'}]}
            good=copy.deepcopy(bad);good['plan_changes'][0]['session']=session()
            generate=Mock(side_effect=[types.SimpleNamespace(text=json.dumps(bad),usage_metadata=None),types.SimpleNamespace(text=json.dumps(good),usage_metadata=None)])
            client=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env={'COACH_PROVIDER':'gemini','COACH_MODELS':'example','COACH_ACTIVITY_SOURCE':'offline','COACH_MAX_DAILY_REQUESTS':'2'}
            with patch.multiple(ai_coach,**paths),patch.dict(os.environ,env,clear=True),patch.object(ai_coach,'api_client',return_value=client):
                with self.assertRaisesRegex(RuntimeError,'Incomplete workout instructions'):
                    ai_coach.run_daily_coach_check(today=TODAY,max_attempts_this_run=1)
                self.assertEqual(before,(paths['CANONICAL_FILE'].read_bytes(),paths['PLAN_FILE'].read_bytes()))
                self.assertEqual(generate.call_count,1)
                result=ai_coach.run_daily_coach_check(today=TODAY,max_attempts_this_run=1)
                self.assertEqual(result['status'],'complete');self.assertEqual(generate.call_count,2)
                self.assertEqual(ai_coach.run_daily_coach_check(today=TODAY,max_attempts_this_run=1)['status'],'unchanged')
                self.assertEqual(generate.call_count,2)
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT status FROM coach_api_requests ORDER BY id').fetchall(),[('invalid',),('complete',)])
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM daily_coach_reviews').fetchone()[0],1)

    @unittest.skipUnless(shutil.which('node'),'Node required for browser export checks')
    def test_browser_does_not_turn_text_intervals_into_continuous_fit(self):
        source=Path(__file__).resolve().parents[1]/'dashboard_assets/workout-ui.js'
        script="const fs=require('fs'),vm=require('vm');const scope={window:{}};vm.createContext(scope);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);const api=scope.window.WorkoutDownload;console.log(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(s=>({allowed:api.eligible(s),reason:api.unsupportedReason(s)}))));"
        values=[dict(session(),requires_structured_workout=True),dict(session(workout=workout()),requires_structured_workout=True),dict(session(workout=workout()),requires_structured_workout=True,details_issue='Missing recovery'),session('Easy run','Comfortable effort')]
        result=subprocess.run([shutil.which('node'),'-e',script,str(source)],input=json.dumps(values),capture_output=True,text=True,check=True)
        data=json.loads(result.stdout)
        self.assertEqual([item['allowed'] for item in data],[False,True,False,True])
        self.assertIn('repetitions',data[0]['reason']);self.assertIn('missing steps',data[2]['reason'])


if __name__=='__main__':unittest.main()
