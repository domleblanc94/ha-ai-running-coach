import contextlib
import copy
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import ai_coach
from migrate_plan import migrate
from planning_engine import *

PROFILE = '''**Zone 1 (Recovery):** < 158 bpm
**Zone 2 (Endurance):** 158 - 166 bpm
**Zone 3 (Tempo):** 167 - 175 bpm
**Zone 4 (Hard):** 176 - 184 bpm
**Zone 5 (Max):** >= 185 bpm
**Active Injuries:** None
**A-Race:** Trail 50km
**Date:** November 26, 2026
**Target Time:** Sub 7 hours
'''
PLAN = '''# NO RUNNING ALLOWED; non-negotiable
| Day | Date | Workout Type | Distance / Duration | Target |
|---|---|---|---|---|
| Monday | 2026-10-26 | Easy Power Walk | 4 km | STRICT Z2 (123-143 bpm); hamstring caution |
| Tuesday | 2026-10-27 | Easy Power Walk with Strides | 4 km (incl. 4x 30s strides) | STRICT Z2 (123-143 bpm) |
'''


class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.profile = parse_profile(PROFILE)
        self.plan = parse_plan(PLAN,self.profile)
        self.plan['requires_coach_reconciliation']=False
        for session in self.plan['sessions']: session['target']='Coach-authored target.'
        self.evidence = {'today':'2026-10-26','window_start':'2026-09-29','recent_7d':[{'activity_id':1},{'activity_id':2}]}
        self.response = {'summary':'Good work; effort data are limited.','trajectory':'insufficient_evidence','reviews':[{'activity_id':1,'feedback':'First session reviewed.'},{'activity_id':2,'feedback':'Second session reviewed.'}], 'plan_changes':[]}

    def test_conflict_migration_retains_amounts_and_archive(self):
        self.assertEqual(len(plan_conflicts(PLAN,self.profile)),2)
        self.assertEqual(self.plan['sessions'][0]['amount'],'4 km')
        self.assertEqual(self.plan['source_schedule'][0]['target'],'STRICT Z2 (123-143 bpm); hamstring caution')
        rendered = render_plan(self.plan)
        self.assertNotIn('123-143',rendered)
        self.assertNotIn('non-negotiable',rendered)
        self.assertIn('Trail 50km',rendered)
        self.assertIn('Coach-authored target.',rendered)
        self.assertIn('4x 30s',rendered)

    def test_invalid_ids_past_and_invented_proposals_rejected(self):
        validate_review(self.response,self.evidence,self.plan,'2026-10-26')
        for reviews in ([{'activity_id':99,'feedback':'x'}],[{'activity_id':1,'feedback':'x'},{'activity_id':1,'feedback':'y'}]):
            with self.assertRaises(ValueError):
                validate_review(dict(self.response,reviews=reviews),self.evidence,self.plan,'2026-10-26')
        changed=dict(self.plan['sessions'][0],target='Recovery running or walking as needed.')
        bad=dict(self.response,plan_changes=[{'op':'update','session_id':changed['id'],'session':changed,'reason':'Athlete reports fatigue.'}])
        with self.assertRaises(ValueError): validate_review(bad,self.evidence,self.plan,'2026-10-27',self.profile)
        validate_review(bad,self.evidence,self.plan,'2026-10-26',self.profile)
        validate_review(dict(self.response,trajectory='behind'),self.evidence,self.plan,'2026-10-26',self.profile)

    def test_next_day_unchanged_and_updated_activity_fingerprint(self):
        e=copy.deepcopy(self.evidence);e['today']='2026-10-24'
        first=fingerprint(self.profile,self.plan,e,'review')
        second=copy.deepcopy(e);second['today']='2026-10-25';second['window_start']='2026-09-30'
        self.assertEqual(first,fingerprint(self.profile,self.plan,second,'review'))
        second['recent_7d'][0]['notes']='Updated athlete note'
        self.assertNotEqual(first,fingerprint(self.profile,self.plan,second,'review'))

    def test_date_window_sports_zero_days_same_day(self):
        db=sqlite3.connect(':memory:')
        db.execute('CREATE TABLE activities(id INTEGER,type TEXT,start_date TEXT,distance_m REAL,moving_time_s INTEGER,avg_heartrate REAL,cardiac_drift_pct REAL)')
        db.executemany('INSERT INTO activities VALUES(?,?,?,?,?,?,?)',[(1,'Run','2026-10-26',5000,1800,150,10),(2,'Run','2026-10-26',2000,900,155,2),(3,'Ride','2026-10-25',100000,5000,140,5),(4,'Run','2026-09-01',5000,1800,150,10),(5,'Run','2026-10-27',9000,3000,150,10)])
        e=build_evidence(db,'2026-10-26');db.close()
        self.assertEqual(e['running_28d']['total_km'],7)
        self.assertEqual(e['running_28d']['avg_weekly_km'],1.75)
        self.assertEqual([r['activity_id'] for r in e['recent_7d']], [3,1,2])
        self.assertEqual(e['recent_7d'][1]['pace_min_km'],6)
        self.assertIsNone(e['recent_7d'][1]['cardiac_drift_pct'])

    def test_migration_preview_no_writes_apply_backups(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);p=root/'athlete.md';m=root/'source.md';out=root/'output'
            p.write_text(PROFILE);m.write_text(PLAN)
            migrate(p,m,out)
            self.assertFalse(out.exists())
            result=migrate(p,m,out,True)
            self.assertEqual(m.read_text(),PLAN)
            self.assertEqual((Path(result['backup'])/'source_training_plan.md').read_text(),PLAN)
            validate_plan(json.loads((out/'training_plan.json').read_text()),self.profile)

    def test_real_supplied_profile_if_available(self):
        fixture_dir=os.getenv('COACH_FIXTURE_DIR')
        if not fixture_dir:self.skipTest('Set COACH_FIXTURE_DIR to opt in to private fixture checks.')
        root=Path(fixture_dir)
        if not (root/'athlete.md').exists():self.skipTest('Private fixture directory has no athlete profile.')
        p=parse_profile((root/'athlete.md').read_text())
        self.assertEqual(p['zones']['2']['min'],158)
        self.assertEqual(p['active_injuries'],'None')
        self.assertEqual(len(p['goals']),3)
        text=(root/'training_plan.md').read_text()
        self.assertTrue(plan_conflicts(text,p))
        self.assertEqual(len(parse_plan(text,p)['sessions']),35)

    def test_automatic_patch_version_commit_and_rollback(self):
        changed=dict(self.plan['sessions'][1],workout_type='Tempo intervals',amount='7 km',target='Coach-selected tempo effort; recoveries easy.')
        payload=dict(self.response,trajectory='on_track',plan_changes=[{'op':'update','session_id':changed['id'],'session':changed,'reason':'Recent training and athlete request support this change.'}])
        validate_review(payload,self.evidence,self.plan,'2026-10-26',self.profile)
        next_plan=apply_plan_changes(self.plan,payload['plan_changes'],self.profile,'2026-10-26')
        self.assertEqual(next_plan['revision'],2)
        self.assertEqual(next_plan['sessions'][1]['amount'],'7 km')
        self.assertEqual(self.plan['sessions'][1]['amount'],'4 km (incl. 4x 30s strides)')
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);canonical=root/'training_plan.json';markdown=root/'training_plan.md'
            canonical.write_text(json.dumps(self.plan),encoding='utf-8');markdown.write_text(render_plan(self.plan),encoding='utf-8')
            conn=sqlite3.connect(root/'db.sqlite')
            conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,ai_feedback TEXT)')
            conn.executemany('INSERT INTO activities VALUES(?,NULL)',[(1,),(2,)])
            ai_coach.ensure_review_schema(conn)
            conn.execute("INSERT INTO coach_review_jobs(fingerprint,status) VALUES('test','pending')");conn.commit()
            original=canonical.read_bytes();original_md=markdown.read_bytes()
            with patch.multiple(ai_coach,CANONICAL_FILE=canonical,PLAN_FILE=markdown):
                real=ai_coach.restore_plan_files
                calls=[]
                def fail_midway(files):
                    calls.append(1)
                    if len(calls)==1:
                        canonical.write_text(files[canonical.name],encoding='utf-8')
                        raise OSError('Injected failure between file replacements')
                    return real(files)
                with patch.object(ai_coach,'restore_plan_files',side_effect=fail_midway):
                    with self.assertRaises(OSError): ai_coach.commit_review(conn,'test',payload,self.plan,next_plan,'{}')
                self.assertEqual(canonical.read_bytes(),original)
                self.assertEqual(markdown.read_bytes(),original_md)
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM daily_coach_reviews').fetchone()[0],0)
                saved=ai_coach.commit_review(conn,'test',payload,self.plan,next_plan,'{}')
                self.assertEqual(saved['applied_revision'],2)
                self.assertEqual(json.loads(canonical.read_text())['sessions'][1]['amount'],'7 km')
                self.assertIn('Tempo intervals',markdown.read_text())
                self.assertEqual(conn.execute('SELECT revision FROM coach_plan_versions').fetchone()[0],2)
                self.assertEqual(conn.execute('SELECT status FROM coach_review_jobs').fetchone()[0],'complete')
                self.assertFalse((root/'.plan-commit.json').exists())
            conn.close()

    def test_crash_journal_pending_rolls_back_complete_rolls_forward(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);canonical=root/'training_plan.json';markdown=root/'training_plan.md'
            old={canonical.name:'old json',markdown.name:'old markdown'}
            new={canonical.name:'new json',markdown.name:'new markdown'}
            journal=root/'.plan-commit.json'
            conn=sqlite3.connect(root/'db.sqlite')
            conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY)');ai_coach.ensure_review_schema(conn)
            conn.execute("INSERT INTO coach_review_jobs(fingerprint,status) VALUES('crash','pending')");conn.commit()
            with patch.multiple(ai_coach,CANONICAL_FILE=canonical,PLAN_FILE=markdown):
                for state,expected in [('pending',old),('complete',new)]:
                    journal.write_text(json.dumps({'fingerprint':'crash','old':old,'new':new}),encoding='utf-8')
                    canonical.write_text('partial replacement',encoding='utf-8')
                    conn.execute('UPDATE coach_review_jobs SET status=?',(state,));conn.commit()
                    ai_coach.recover_plan_commit(conn)
                    self.assertEqual(canonical.read_text(),expected[canonical.name])
                    self.assertEqual(markdown.read_text(),expected[markdown.name])
                    self.assertFalse(journal.exists())
            conn.close()

    def test_negative_quantities_unknown_ids_past_reschedule_and_due_cache(self):
        base=dict(self.plan['sessions'][1])
        for op,sid,session in [('update','unknown',base),('update',base['id'],dict(base,date='2026-10-25')),('update',base['id'],dict(base,amount='-5 km'))]:
            changes=[{'op':op,'session_id':sid,'session':session,'reason':'Test'}]
            with self.assertRaises(ValueError): apply_plan_changes(self.plan,changes,self.profile,'2026-10-26')
        first=fingerprint(self.profile,self.plan,self.evidence,'review')
        later=dict(self.evidence,today='2026-10-27')
        self.assertNotEqual(first,fingerprint(self.profile,self.plan,later,'review'))

    def test_failure_keeps_last_good_retry_next_day_and_idempotence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);profile=root/'athlete.md';profile.write_text(PROFILE)
            canonical=root/'training_plan.json';canonical.write_text(json.dumps(self.plan))
            markdown=root/'training_plan.md';markdown.write_text(render_plan(self.plan),encoding='utf-8')
            dbpath=root/'db.sqlite';db=sqlite3.connect(dbpath)
            db.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,type TEXT,start_date TEXT,distance_m REAL,moving_time_s INTEGER)')
            db.executemany('INSERT INTO activities VALUES(?,?,?,?,?)',[(1,'Run','2026-10-26',5000,1800),(2,'Run','2026-10-26',3000,1100)]);db.execute("ALTER TABLE activities ADD COLUMN source TEXT DEFAULT 'local:fixture'");db.commit();db.close()
            api=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=lambda **kw: types.SimpleNamespace(text='broken',usage_metadata=None)))
            fake_genai=types.ModuleType('google.genai');fake_genai.types=types.SimpleNamespace(GenerateContentConfig=lambda **kw: kw,ThinkingConfig=lambda **kw:kw)
            fake_google=types.ModuleType('google');fake_google.genai=fake_genai
            with patch.multiple(ai_coach,ATHLETE_FILE=profile,PLAN_FILE=markdown,CANONICAL_FILE=canonical,DB_PATH=dbpath), patch.object(ai_coach,'api_client',return_value=api), patch.dict(sys.modules,{'google':fake_google,'google.genai':fake_genai}), contextlib.redirect_stdout(io.StringIO()):
                before=canonical.read_bytes()
                with self.assertRaises(RuntimeError): ai_coach.run_daily_coach_check(today='2026-10-26')
                self.assertEqual(before,canonical.read_bytes())
                db=sqlite3.connect(dbpath)
                self.assertEqual(db.execute('SELECT status FROM coach_review_jobs').fetchone()[0],'pending')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM daily_coach_reviews').fetchone()[0],0)
                db.execute("UPDATE coach_review_jobs SET attempts=2,updated_at='2000-01-01'");db.commit();db.close()
                replacement=dict(self.plan['sessions'][1],sport='running',amount='6 km',target='AI-selected running session based on the athlete request.')
                revised=dict(self.response,trajectory='on_track',plan_changes=[{'op':'update','session_id':replacement['id'],'session':replacement,'reason':'Adapt upcoming session to the athlete request and training evidence.'}])
                api.models.generate_content=lambda **kw: types.SimpleNamespace(text=json.dumps(revised),usage_metadata=None)
                result=ai_coach.run_daily_coach_check(today='2026-10-27')
                self.assertEqual(result['status'],'complete')
                self.assertEqual(result['applied_revision'],2)
                self.assertEqual(json.loads(canonical.read_text())['sessions'][1]['amount'],'6 km')
                result=ai_coach.run_daily_coach_check(today='2026-10-27')
                self.assertEqual(result['status'],'unchanged')
                self.assertNotEqual(before,canonical.read_bytes())
                db=sqlite3.connect(dbpath)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM daily_coach_reviews').fetchone()[0],1)
                self.assertEqual(db.execute('SELECT ai_feedback FROM activities WHERE id=1').fetchone()[0],'First session reviewed.')
                self.assertEqual(db.execute('SELECT ai_feedback FROM activities WHERE id=2').fetchone()[0],'Second session reviewed.')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM coach_api_requests').fetchone()[0],2)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM coach_api_requests WHERE status='invalid'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM coach_api_requests WHERE status='complete'").fetchone()[0],1)
                self.assertEqual(db.execute("SELECT response_text FROM coach_api_requests WHERE status='invalid'").fetchone()[0],'broken')
                db.close()
                before_db=dbpath.read_bytes()
                before=canonical.read_bytes()
                with patch.object(ai_coach,'api_client',side_effect=AssertionError('No API in dry-run')):
                    result=ai_coach.run_daily_coach_check(dry_run=True,today='2026-10-27')
                self.assertEqual(result['status'],'dry_run')
                self.assertEqual(before_db,dbpath.read_bytes())
                self.assertEqual(before,canonical.read_bytes())

if __name__=='__main__': unittest.main()
