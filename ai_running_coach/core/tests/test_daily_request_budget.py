from contextlib import closing,redirect_stdout
from datetime import date,timedelta,datetime,timezone
import io,json,os
from pathlib import Path
import sqlite3,tempfile,types,unittest
from unittest.mock import Mock,patch
import ai_coach
from planning_engine import parse_profile,parse_plan,render_plan

class Unavailable(Exception):
    code=503

class DailyBudgetTests(unittest.TestCase):
    def fixture(self,root):
        text='**Zone 2 (Endurance):** 130 - 150 bpm\n**Active Injuries:** None\n'
        profile=parse_profile(text);future=(date.today()+timedelta(days=1)).isoformat()
        plan=parse_plan('| Day | '+future+' | Run | 5 km | Coach target |',profile)
        paths={'ATHLETE_FILE':root/'athlete.md','PLAN_FILE':root/'training_plan.md','CANONICAL_FILE':root/'training_plan.json','DB_PATH':root/'data.sqlite'}
        paths['ATHLETE_FILE'].write_text(text,encoding='utf-8');paths['PLAN_FILE'].write_text(render_plan(plan),encoding='utf-8');paths['CANONICAL_FILE'].write_text(json.dumps(plan),encoding='utf-8')
        with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
            conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL)')
            conn.execute('INSERT INTO activities VALUES(1,?,?,5000,1800)',('Run',date.today().isoformat()));conn.execute("ALTER TABLE activities ADD COLUMN source TEXT DEFAULT 'local:fixture'");conn.commit()
        return paths

    def test_two_scheduled_attempts_then_one_explicit_audited_recovery(self):
        for extra_succeeds in (True,False):
            with self.subTest(extra_succeeds=extra_succeeds),tempfile.TemporaryDirectory() as d:
                paths=self.fixture(Path(d))
                response=types.SimpleNamespace(text=json.dumps({'summary':'Review complete.','trajectory':'on_track','reviews':[],'plan_changes':[]}),usage_metadata=None)
                generate=Mock(side_effect=[Unavailable('503 UNAVAILABLE high demand'),Unavailable('503 UNAVAILABLE high demand'),response if extra_succeeds else Unavailable('503 UNAVAILABLE high demand')])
                client=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
                env={'COACH_MODELS':'gemini-2.5-flash','COACH_MAX_ATTEMPTS':'2','COACH_MAX_DAILY_REQUESTS':'2'}
                with patch.multiple(ai_coach,**paths),patch.dict(os.environ,env),patch.object(ai_coach,'api_client',return_value=client),redirect_stdout(io.StringIO()):
                    # Default same-model outage does not spend both calls one second apart.
                    with self.assertRaises(RuntimeError):ai_coach.run_daily_coach_check()
                    self.assertEqual(generate.call_count,1)
                    with self.assertRaises(RuntimeError):ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                    self.assertEqual(generate.call_count,2)
                    with self.assertRaisesRegex(RuntimeError,'Daily coaching'):ai_coach.run_daily_coach_check('Different evidence question')
                    self.assertEqual(generate.call_count,2)
                    with self.assertRaises(ValueError):ai_coach.run_daily_coach_check(operator_extra_attempt='2000-01-01',operator_reason='Wrong date approval')
                    today=datetime.now(timezone.utc).date().isoformat()
                    kwargs={'max_attempts_this_run':1,'operator_extra_attempt':today,'operator_reason':'User approved one additional attempt after the 503 outage'}
                    if extra_succeeds:
                        self.assertEqual(ai_coach.run_daily_coach_check(**kwargs)['status'],'complete')
                        self.assertEqual(ai_coach.run_daily_coach_check(**kwargs)['status'],'unchanged')
                    else:
                        with self.assertRaises(RuntimeError):ai_coach.run_daily_coach_check(**kwargs)
                        with self.assertRaises(ValueError):ai_coach.run_daily_coach_check(**kwargs)
                    self.assertEqual(generate.call_count,3)
                    with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                        self.assertEqual(conn.execute('SELECT COUNT(*) FROM coach_api_requests').fetchone()[0],3)
                        kind,snapshot=conn.execute('SELECT request_kind,input_snapshot_json FROM coach_api_requests ORDER BY id DESC LIMIT 1').fetchone()
                        self.assertEqual(kind,'operator_recovery')
                        self.assertEqual(json.loads(snapshot)['operator_approval']['date'],today)
                        self.assertEqual(conn.execute('SELECT MAX(attempts) FROM coach_review_jobs').fetchone()[0],3)

    def test_env_is_loaded_before_limit_selection(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'.env').write_text('COACH_MODELS=configured-model\nCOACH_MAX_OUTPUT_TOKENS=1024\nCOACH_MAX_ATTEMPTS=1\n',encoding='utf-8')
            with patch.object(ai_coach,'SCRIPT_DIR',root),patch.dict(os.environ,{},clear=True):
                self.assertEqual(ai_coach.limits(),(['configured-model'],1024,1))

    def test_operator_exception_rejects_other_errors_or_models_without_calls(self):
        today=datetime.now(timezone.utc).date().isoformat();prior=('pending',2,today)
        rows=[(1,'digest','gemini-2.5-flash','failed','429 quota exhausted','daily'),(2,'digest','gemini-2.5-flash','failed','503 high demand','daily')]
        with self.assertRaises(ValueError):ai_coach.operator_recovery_eligibility(rows,today,'Explicit user recovery approval','digest',prior)
        rows[0]=(1,'different-digest','gemini-2.5-flash','failed','503 high demand','daily')
        with self.assertRaises(ValueError):ai_coach.operator_recovery_eligibility(rows,today,'Explicit user recovery approval','digest',prior)

    def test_new_starter_prompt_cannot_spend_third_utc_request_after_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            paths = self.fixture(Path(d))
            paths['PLAN_FILE'].unlink()
            paths['CANONICAL_FILE'].unlink()
            truncated = types.SimpleNamespace(text='{"summary":"partial', usage_metadata=None, finish_reason='MAX_TOKENS')
            generate = Mock(side_effect=[Unavailable('503 high demand'), truncated])
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'gemini-2.5-flash', 'COACH_MAX_DAILY_REQUESTS': '2'}
            with patch.multiple(ai_coach, **paths), patch.object(ai_coach, 'SCRIPT_DIR', Path(d)), patch.dict(os.environ, env, clear=True), redirect_stdout(io.StringIO()):
                with patch.object(ai_coach, 'STARTER_PROMPT_VERSION', ai_coach.PROMPT_VERSION), patch.object(ai_coach, 'api_client', return_value=client):
                    previous = ai_coach.run_daily_coach_check(dry_run=True)['fingerprint']
                    with self.assertRaisesRegex(RuntimeError, '503'):
                        ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                    with self.assertRaisesRegex(RuntimeError, 'truncated'):
                        ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                current = ai_coach.run_daily_coach_check(dry_run=True)['fingerprint']
                self.assertNotEqual(previous, current)
                with patch.object(ai_coach, 'api_client') as api:
                    with self.assertRaisesRegex(RuntimeError, 'Daily coaching API request budget exhausted'):
                        ai_coach.run_daily_coach_check(max_attempts_this_run=1)
                    api.assert_not_called()
            self.assertEqual(generate.call_count, 2)
            self.assertFalse(paths['PLAN_FILE'].exists())
            self.assertFalse(paths['CANONICAL_FILE'].exists())
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual([(row[1],row[3]) for row in ai_coach.daily_coaching_requests(conn)],
                                 [(previous, 'failed'), (previous, 'invalid')])

    def test_first_plan_resulting_review_remains_cached_across_prompt_mode_change(self):
        with tempfile.TemporaryDirectory() as d:
            paths = self.fixture(Path(d))
            paths['PLAN_FILE'].unlink()
            paths['CANONICAL_FILE'].unlink()
            session = {'id': 'first-session', 'date': (date.today()+timedelta(days=1)).isoformat(),
                       'workout_type': 'Run', 'amount': '30 min', 'target': 'Conversational effort'}
            payload = {'summary': 'A short first block.', 'trajectory': 'insufficient_evidence', 'reviews': [],
                       'plan_changes': [{'op': 'add', 'session_id': session['id'], 'session': session, 'reason': 'First evidence-led session.'}]}
            generate = Mock(return_value=types.SimpleNamespace(text=json.dumps(payload), usage_metadata=None))
            client = types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generate))
            env = {'COACH_PROVIDER': 'gemini', 'COACH_MODELS': 'gemini-2.5-flash'}
            with patch.multiple(ai_coach, **paths), patch.object(ai_coach, 'SCRIPT_DIR', Path(d)), patch.dict(os.environ, env, clear=True), patch.object(ai_coach, 'api_client', return_value=client), redirect_stdout(io.StringIO()):
                self.assertEqual(ai_coach.run_daily_coach_check()['status'], 'complete')
                self.assertEqual(ai_coach.run_daily_coach_check()['status'], 'unchanged')
            self.assertEqual(generate.call_count, 1)
            with closing(sqlite3.connect(paths['DB_PATH'])) as conn:
                self.assertEqual(conn.execute('SELECT prompt_version FROM coach_api_requests').fetchall(), [(ai_coach.STARTER_PROMPT_VERSION,)])

if __name__=='__main__':unittest.main()
