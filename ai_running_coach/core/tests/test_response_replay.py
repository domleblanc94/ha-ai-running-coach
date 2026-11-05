from contextlib import closing
from datetime import date,timedelta
import json,os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ai_coach
from coach_checkin import record_checkin
from planning_engine import build_evidence,fingerprint,parse_profile,parse_plan,render_plan

PROFILE='''**Zone 1 (Recovery):** < 158 bpm
**Zone 2 (Endurance):** 158 - 166 bpm
**Zone 5 (Max):** >= 185 bpm
**Active Injuries:** None
**A-Race:** Synthetic Trail
**Date:** November 21, 2026
**Target Time:** Finish
'''


class SavedResponseReplayTests(unittest.TestCase):
    def fixture(self,root):
        today=date.today().isoformat();future=(date.today()+timedelta(days=1)).isoformat()
        profile=parse_profile(PROFILE)
        plan=parse_plan('| Day | '+future+' | Run | 5 km | Coach-selected effort |',profile)
        db=root/'data.sqlite';canonical=root/'training_plan.json';md=root/'training_plan.md';athlete=root/'athlete.md'
        (root/'.env').write_text('COACH_PROVIDER=gemini\nCOACH_MODELS=gemini-2.5-flash\n',encoding='utf-8')
        athlete.write_text(PROFILE,encoding='utf-8');canonical.write_text(json.dumps(plan),encoding='utf-8');md.write_text(render_plan(plan),encoding='utf-8')
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL)')
            conn.execute('INSERT INTO activities VALUES(101,?,?,5000,1800)',('Run',today));conn.execute("ALTER TABLE activities ADD COLUMN source TEXT DEFAULT 'local:fixture'");conn.commit()
            ai_coach.ensure_review_schema(conn)
            evidence=build_evidence(conn,today)
            question='Coach my training, assess progress and adapt my upcoming plan as needed.'
            digest=fingerprint(profile,plan,evidence,question)
            session=dict(plan['sessions'][0],target='Aim for lower Zone 2 HR (158-162 bpm).')
            response={'summary':'Review the next session in context.','trajectory':'on_track','reviews':[{'activity_id':101,'feedback':'Useful recorded run.'}],'plan_changes':[{'op':'update','session_id':session['id'],'session':session,'reason':'Select a lower target within the configured zone.'}]}
            snapshot={'profile':profile,'plan':plan,'evidence':evidence,'question':question}
            conn.execute('INSERT INTO coach_review_jobs(fingerprint,status,attempts,updated_at,prompt_version) VALUES(?,?,?,?,?)',(digest,'pending',1,today+'T00:00:00','autonomous-coach-v3'))
            conn.execute('INSERT INTO coach_api_requests(id,job_fingerprint,model,prompt_version,request_kind,started_at,completed_at,status,usage_json,error,response_text,input_snapshot_json) VALUES(1,?,?,?,?,?,?,?,?,?,?,?)',(digest,'gemini-2.5-flash','autonomous-coach-v3','daily',today+'T00:00:00',today+'T00:00:01','invalid','{"total_token_count":100}','original exact-range validation error',json.dumps(response),json.dumps(snapshot)));conn.commit()
        return {'DB_PATH':db,'ATHLETE_FILE':athlete,'CANONICAL_FILE':canonical,'PLAN_FILE':md}

    def test_revalidate_saved_response_once_no_new_api_and_audit_original(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);paths=self.fixture(root)
            with patch.multiple(ai_coach,**paths),patch.object(ai_coach,'SCRIPT_DIR',root),patch.dict(os.environ,{},clear=True),patch.object(ai_coach,'api_client',side_effect=AssertionError('Replay/cache must not call API')):
                originals={p:p.read_bytes() for p in paths.values()}
                preview=ai_coach.replay_saved_request(1,True)
                self.assertEqual((preview['api_attempts'],preview['applied_revision']),(0,2))
                self.assertEqual({p:p.read_bytes() for p in paths.values()},originals)
                result=ai_coach.replay_saved_request(1)
                self.assertEqual(result['status'],'revalidated')
                self.assertEqual(json.loads(paths['CANONICAL_FILE'].read_text())['revision'],2)
                with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                    row=conn.execute('SELECT status,error,completed_at FROM coach_api_requests WHERE id=1').fetchone()
                    self.assertEqual(row,('accepted_after_revalidation','original exact-range validation error',date.today().isoformat()+'T00:00:01'))
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM coach_api_requests').fetchone()[0],1)
                    self.assertEqual(conn.execute('SELECT original_status,original_error,applied_revision FROM coach_response_revalidations').fetchone(),('invalid','original exact-range validation error',2))
                self.assertEqual(ai_coach.run_daily_coach_check()['status'],'unchanged')
                with self.assertRaises(ValueError):ai_coach.replay_saved_request(1)

    def test_legacy_replay_rejects_new_default_or_changed_explicit_model_without_writes(self):
        for config in ('COACH_PROVIDER=gemini\n','COACH_PROVIDER=gemini\nCOACH_MODELS=gemini-3.5-flash-lite\n'):
            with self.subTest(config=config),tempfile.TemporaryDirectory() as d:
                root=Path(d);paths=self.fixture(root)
                env=root/'.env';env.write_text(config,encoding='utf-8')
                originals={p:p.read_bytes() for p in [*paths.values(),env]}
                with patch.multiple(ai_coach,**paths),patch.object(ai_coach,'SCRIPT_DIR',root),patch.dict(os.environ,{},clear=True),patch.object(ai_coach,'api_client') as api:
                    with self.assertRaisesRegex(ValueError,'Legacy replay is limited to its original Gemini model'):
                        ai_coach.replay_saved_request(1)
                    api.assert_not_called()
                self.assertEqual({p:p.read_bytes() for p in originals},originals)

    def test_current_evidence_profile_plan_or_date_mismatch_refuses_without_mutation(self):
        for change in ('checkin','activity','profile','plan','date'):
            with self.subTest(change=change),tempfile.TemporaryDirectory() as d:
                paths=self.fixture(Path(d))
                if change=='checkin':record_checkin(paths['DB_PATH'],date.today().isoformat(),{'pain':'none'})
                elif change=='activity':
                    with closing(sqlite3.connect(paths['DB_PATH'])) as conn:conn.execute('UPDATE activities SET distance_m=6000');conn.commit()
                elif change=='profile':paths['ATHLETE_FILE'].write_text(PROFILE+'\nNew athlete note.',encoding='utf-8')
                elif change=='plan':
                    plan=json.loads(paths['CANONICAL_FILE'].read_text());plan['revision']+=1;paths['CANONICAL_FILE'].write_text(json.dumps(plan),encoding='utf-8')
                else:
                    with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                        snapshot=json.loads(conn.execute('SELECT input_snapshot_json FROM coach_api_requests').fetchone()[0]);snapshot['evidence']['today']=(date.today()-timedelta(days=1)).isoformat()
                        conn.execute('UPDATE coach_api_requests SET input_snapshot_json=?',(json.dumps(snapshot),));conn.commit()
                originals={p:p.read_bytes() for p in paths.values()}
                with patch.multiple(ai_coach,**paths),patch.object(ai_coach,'SCRIPT_DIR',paths['ATHLETE_FILE'].parent),patch.dict(os.environ,{},clear=True),patch.object(ai_coach,'api_client',side_effect=AssertionError('No API')):
                    with self.assertRaises(ValueError):ai_coach.replay_saved_request(1)
                self.assertEqual({p:p.read_bytes() for p in paths.values()},originals)


if __name__=='__main__':unittest.main()
