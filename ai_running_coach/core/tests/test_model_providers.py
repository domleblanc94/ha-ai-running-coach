import contextlib
from datetime import date,timedelta
import io,json,os
from pathlib import Path
import sqlite3,tempfile,types,unittest
import shutil,subprocess,sys
from unittest.mock import Mock,patch

import requests
import ai_coach
from model_provider import ProviderClient,ProviderError,configured_models,provider_identity,DEFAULT_MODELS
from planning_engine import fingerprint,parse_profile,parse_plan,render_plan


class ProviderAdapterTests(unittest.TestCase):
    def test_new_gemini_default_uses_current_rest_config_without_legacy_controls(self):
        env,session=self.client('gemini',{'candidates':[{'content':{'parts':[{'text':'{}'}]}}]})
        env.pop('COACH_MODELS')
        with patch.dict(os.environ,env,clear=True):
            self.assertEqual(configured_models(),['gemini-3.5-flash-lite'])
            client=ProviderClient(session=session)
            client.generate_content(configured_models()[0],'Return JSON.',{'temperature':0.2,'response_mime_type':'application/json','max_output_tokens':4096})
        payload=session.post.call_args.kwargs['json']
        self.assertEqual(payload['generationConfig'],{'maxOutputTokens':4096,'responseMimeType':'application/json','thinkingConfig':{'thinkingLevel':'MINIMAL'}})
        self.assertTrue(session.post.call_args.args[0].endswith('/models/gemini-3.5-flash-lite:generateContent'))
        self.assertEqual(session.post.call_count,1)

    def test_explicit_legacy_models_keep_their_original_budget_and_sampling(self):
        for model in ('gemini-2.5-flash','models/gemini-2.5-flash-lite'):
            env,session=self.client('gemini',{'candidates':[{'content':{'parts':[{'text':'{}'}]}}]},extra={'COACH_MODELS':model})
            with patch.dict(os.environ,env,clear=True):
                self.assertEqual(configured_models(),[model])
                ProviderClient(session=session).generate_content(model,'Return JSON.',{'temperature':0.2})
            generation=session.post.call_args.kwargs['json']['generationConfig']
            self.assertEqual(generation['temperature'],0.2)
            self.assertEqual(generation['thinkingConfig'],{'thinkingBudget':0})
            self.assertNotIn('thinkingLevel',generation['thinkingConfig'])

    def test_newer_explicit_gemini_model_does_not_inherit_lite_thinking(self):
        env,session=self.client('gemini',{'candidates':[{'content':{'parts':[{'text':'{}'}]}}]})
        with patch.dict(os.environ,env,clear=True):
            ProviderClient(session=session).generate_content('gemini-3.8-flash','Return JSON.',{'temperature':0.2})
        generation=session.post.call_args.kwargs['json']['generationConfig']
        self.assertNotIn('temperature',generation);self.assertNotIn('thinkingConfig',generation)

    def test_openai_cold_env_file_with_blank_base_url(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            (root/'.env').write_text('COACH_PROVIDER=openai\nCOACH_MODELS=account-selected-model\nOPENAI_API_KEY=test-key-not-real\nOPENAI_BASE_URL=\n',encoding='utf-8')
            with patch.object(ai_coach,'SCRIPT_DIR',root),patch.dict(os.environ,{},clear=True):
                self.assertEqual(ai_coach.limits()[0],['account-selected-model'])
                client=ai_coach.api_client()
                self.assertEqual(client.base_url,'https://api.openai.com/v1')
                client.session.close()

    def test_blank_optional_provider_settings_use_official_defaults(self):
        for provider in ('gemini','openai','anthropic'):
            with self.subTest(provider=provider):
                env,session=self.client(provider,{},extra={provider.upper()+'_BASE_URL':'  ','OPENAI_TOKEN_PARAMETER':'','ANTHROPIC_VERSION':''})
                with patch.dict(os.environ,env,clear=True):
                    client=ProviderClient(session=session)
                    client.generate_content('configured-model','Return JSON.',{'response_mime_type':'application/json'})
                self.assertTrue(session.post.call_args.args[0].startswith('https://'))
                if provider=='openai':self.assertEqual(session.post.call_args.kwargs['json']['max_completion_tokens'],4096)
                if provider=='anthropic':self.assertEqual(session.post.call_args.kwargs['headers']['anthropic-version'],'2026-10-21')

    def client(self,provider,data,extra=None,status=200):
        env={'COACH_PROVIDER':provider,'COACH_MODELS':'selected-model',provider.upper()+'_API_KEY':'test-key-not-real'}
        env.update(extra or {})
        session=Mock();session.post.return_value=types.SimpleNamespace(status_code=status,json=lambda:data)
        return env,session

    def test_three_provider_protocols_usage_timeouts_and_one_http_attempt(self):
        cases=[('gemini',{'candidates':[{'content':{'parts':[{'text':'{"ok":true}'}]},'finishReason':'STOP'}],'usageMetadata':{'promptTokenCount':12,'candidatesTokenCount':4,'totalTokenCount':16}},'/models/selected-model:generateContent'),
               ('openai',{'choices':[{'message':{'content':'{"ok":true}'},'finish_reason':'stop'}],'usage':{'prompt_tokens':12,'completion_tokens':4,'total_tokens':16}},'/chat/completions'),
               ('anthropic',{'content':[{'type':'text','text':'{"ok":true}'}],'stop_reason':'end_turn','usage':{'input_tokens':12,'output_tokens':4}},'/messages')]
        for provider,data,suffix in cases:
            with self.subTest(provider=provider):
                env,session=self.client(provider,data)
                with patch.dict(os.environ,env,clear=True):
                    client=ProviderClient(session=session)
                    response=client.models.generate_content('selected-model','Return JSON.',{'response_mime_type':'application/json','max_output_tokens':1024})
                self.assertEqual(json.loads(response.text),{'ok':True})
                self.assertEqual(response.usage_metadata.model_dump()['total_token_count'],16)
                self.assertEqual(session.post.call_count,1)
                args,kwargs=session.post.call_args
                self.assertTrue(args[0].endswith(suffix));self.assertNotIn('test-key',args[0])
                self.assertEqual(kwargs['timeout'],(10,60));self.assertFalse(kwargs['allow_redirects'])
                if provider=='gemini':
                    self.assertEqual(kwargs['headers']['x-goog-api-key'],'test-key-not-real')
                    self.assertEqual(kwargs['json']['generationConfig']['responseMimeType'],'application/json')
                elif provider=='openai':
                    self.assertEqual(kwargs['json']['response_format'],{'type':'json_object'})
                    self.assertEqual(kwargs['json']['max_completion_tokens'],1024)
                else:self.assertEqual(kwargs['headers']['anthropic-version'],'2026-10-21')

    def test_keyless_ollama_endpoint_is_explicit_and_model_never_guessed(self):
        env,session=self.client('openai',{'choices':[{'message':{'content':'{}'}}]}, {'OPENAI_BASE_URL':'http://localhost:11434/v1','OPENAI_API_KEY':''})
        with patch.dict(os.environ,env,clear=True):
            with self.assertRaises(ValueError):ProviderClient(session=session)
            os.environ['COACH_ALLOW_KEYLESS']='true'
            client=ProviderClient(session=session);client.generate_content('locally-installed-model','JSON.',{'response_mime_type':'application/json','max_output_tokens':512})
            self.assertNotIn('Authorization',session.post.call_args.kwargs['headers'])
            self.assertEqual(session.post.call_args.kwargs['json']['max_tokens'],512)
            os.environ.pop('COACH_MODELS')
            with self.assertRaises(ValueError):configured_models()
            os.environ['OPENAI_BASE_URL']='https://api.openai.com/v1'
            with self.assertRaises(ValueError):ProviderClient(session=session)

    def test_failure_diagnostics_never_echo_keys_urls_or_server_bodies(self):
        env,session=self.client('gemini',{'error':{'message':'SECRET_KEY echoed'}},status=503)
        with patch.dict(os.environ,env,clear=True):
            client=ProviderClient(session=session)
            with self.assertRaises(ProviderError) as result:client.generate_content('model','JSON.',{})
            self.assertEqual(result.exception.code,503)
            self.assertNotIn('SECRET',str(result.exception));self.assertNotIn('test-key',str(result.exception))
            session.post.side_effect=requests.Timeout('https://host?key=SECRET_KEY')
            with self.assertRaises(ProviderError) as timed:client.generate_content('model','JSON.',{})
            self.assertEqual(timed.exception.code,504);self.assertNotIn('SECRET',str(timed.exception))

    def test_profile_conversation_works_without_google_sdk_for_every_provider(self):
        profile='**Athlete Name:** Example Runner\n**Heart Rate Zones:** Not configured\n**Active Injuries:** Unknown\n'
        for provider in ('gemini','openai','anthropic'):
            with self.subTest(provider=provider),tempfile.TemporaryDirectory() as d:
                if provider=='gemini':data={'candidates':[{'content':{'parts':[{'text':'```markdown\n'+profile+'```'}]}}]}
                elif provider=='openai':data={'choices':[{'message':{'content':'```markdown\n'+profile+'```'}}]}
                else:data={'content':[{'type':'text','text':'```markdown\n'+profile+'```'}]}
                env,session=self.client(provider,data)
                path=Path(d)/'athlete.md';path.write_text('**Athlete Name:** Example Runner\n',encoding='utf-8')
                with patch.dict(os.environ,env,clear=True):
                    client=ProviderClient(session=session)
                    with patch.object(ai_coach,'api_client',return_value=client),patch.object(ai_coach,'ATHLETE_FILE',path),patch('builtins.input',return_value='save'),contextlib.redirect_stdout(io.StringIO()):ai_coach.interactive('profile')
                self.assertEqual(parse_profile(path.read_text())['zones'],{})
                self.assertEqual(len(list((Path(d)/'.profile_versions').glob('*.md'))),1)

    def test_provider_model_and_endpoint_changes_invalidate_fingerprint(self):
        evidence={'today':date.today().isoformat(),'recent_7d':[]}
        env={'COACH_PROVIDER':'gemini','COACH_MODELS':'one'}
        with patch.dict(os.environ,env,clear=True):
            first=fingerprint({}, {'sessions':[]}, evidence,'review',provider_identity())
            os.environ['COACH_MODELS']='two'
            self.assertNotEqual(first,fingerprint({}, {'sessions':[]},evidence,'review',provider_identity()))
            os.environ.update(COACH_PROVIDER='openai',OPENAI_BASE_URL='http://localhost:11434/v1')
            second=fingerprint({}, {'sessions':[]},evidence,'review',provider_identity())
            self.assertNotEqual(first,second)
            os.environ['OPENAI_BASE_URL']='http://localhost:11435/v1'
            self.assertNotEqual(second,fingerprint({}, {'sessions':[]},evidence,'review',provider_identity()))


class NewPlanTests(unittest.TestCase):
    def test_actual_new_plan_cli_is_noninteractive_and_offline(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);source=Path(ai_coach.__file__).parent
            for name in ('ai_coach.py','planning_engine.py','training_calendar.py','model_provider.py','migrate_plan.py','coach_checkin.py','source_policy.py','workout_schema.py'):
                shutil.copy2(source/name,root/name)
            (root/'athlete.md').write_text('**Heart Rate Zones:** Unknown\n',encoding='utf-8')
            env=dict(os.environ,COACH_PROVIDER='gemini',COACH_MODELS='gemini-2.5-flash')
            result=subprocess.run([sys.executable,str(root/'ai_coach.py'),'--new-plan','--dry-run','Build a fresh block for general fitness'],capture_output=True,text=True,encoding='utf-8',env=env,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['status'],'dry_run')
            self.assertFalse((root/'training_plan.json').exists());self.assertFalse((root/'ai_coach_master.sqlite').exists())

    def test_new_plan_invalid_response_preserves_old_then_valid_replaces_future_only(self):
        today=date.today();past=(today-timedelta(days=1)).isoformat();future=(today+timedelta(days=1)).isoformat()
        text='**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n'
        profile=parse_profile(text)
        plan=parse_plan('| Day | '+past+' | Run | 3 km | Past session |\n| Day | '+future+' | Run | 5 km | Old future session |',profile)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);paths={'ATHLETE_FILE':root/'athlete.md','PLAN_FILE':root/'training_plan.md','CANONICAL_FILE':root/'training_plan.json','DB_PATH':root/'data.sqlite'}
            paths['ATHLETE_FILE'].write_text(text,encoding='utf-8');paths['PLAN_FILE'].write_text(render_plan(plan),encoding='utf-8');paths['CANONICAL_FILE'].write_text(json.dumps(plan),encoding='utf-8')
            conn=sqlite3.connect(paths['DB_PATH']);conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,type TEXT,start_date TEXT,distance_m REAL,moving_time_s REAL)');conn.commit();conn.close()
            replacement={'id':'fresh-block-session','date':future,'workout_type':'Run','amount':'6 km','target':'AI-selected effort.',
                         'workout':{'name':'Synthetic running workout','sport':'running','steps':[
                             {'intensity':'warmup','duration':{'type':'distance','metres':1000},'target':{'type':'open'}},
                             {'intensity':'active','duration':{'type':'distance','metres':4000},'target':{'type':'open'},'notes':'Coach-selected comfortable effort.'},
                             {'intensity':'cooldown','duration':{'type':'distance','metres':1000},'target':{'type':'open'}}]}}
            payload={'summary':'Start a fresh block for the stated request.','trajectory':'insufficient_evidence','reviews':[],'plan_changes':[{'op':'add','session_id':replacement['id'],'session':replacement,'reason':'New athlete-requested block.'}]}
            generator=Mock(side_effect=[types.SimpleNamespace(text='invalid JSON',usage_metadata=None),types.SimpleNamespace(text=json.dumps(payload),usage_metadata=None)])
            client=types.SimpleNamespace(models=types.SimpleNamespace(generate_content=generator))
            with patch.multiple(ai_coach,**paths),patch.object(ai_coach,'api_client',return_value=client),patch.dict(os.environ,{'COACH_PROVIDER':'gemini','COACH_MODELS':'gemini-2.5-flash','COACH_MAX_DAILY_REQUESTS':'2'}),contextlib.redirect_stdout(io.StringIO()):
                before={p:p.read_bytes() for p in (paths['PLAN_FILE'],paths['CANONICAL_FILE'])}
                with self.assertRaises(RuntimeError):ai_coach.run_daily_coach_check('New block please',new_plan=True)
                self.assertEqual({p:p.read_bytes() for p in before},before)
                self.assertEqual(ai_coach.run_daily_coach_check('New block please',new_plan=True)['applied_revision'],2)
            current=json.loads(paths['CANONICAL_FILE'].read_text())
            self.assertEqual(current['sessions'][0],plan['sessions'][0])
            self.assertEqual(current['sessions'][1]['id'],'fresh-block-session')
            self.assertEqual(current['sessions'][1]['workout'],replacement['workout'])
            changed=json.loads(json.dumps(current));changed['sessions'][1]['workout']['steps'][1]['duration']['metres']=4001
            evidence={'today':today.isoformat(),'recent_7d':[]}
            self.assertNotEqual(fingerprint(profile,current,evidence,'review'),fingerprint(profile,changed,evidence,'review'))
            self.assertIn(plan['sessions'][1]['id'],current['retired_session_ids'])
            self.assertEqual(current['superseded_blocks'][0]['sessions'],[plan['sessions'][1]])
            self.assertTrue(list((root/'.plan_versions').glob('*.json')))

    def test_new_plan_dry_run_handles_fresh_empty_install_without_writes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);profile=root/'athlete.md';profile.write_text('**Heart Rate Zones:** Unknown\n',encoding='utf-8')
            with patch.multiple(ai_coach,ATHLETE_FILE=profile,PLAN_FILE=root/'training_plan.md',CANONICAL_FILE=root/'training_plan.json',DB_PATH=root/'missing.sqlite'),patch.object(ai_coach,'api_client',side_effect=AssertionError('No API')),contextlib.redirect_stdout(io.StringIO()):
                result=ai_coach.run_daily_coach_check(new_plan=True,dry_run=True)
            self.assertEqual(result['status'],'dry_run');self.assertIn('NEW plan',result['prompt'])
            today=date.fromisoformat(result['evidence']['today'])
            self.assertIn(f'from {today} through {today+timedelta(days=6)}, inclusive',result['prompt'])
            self.assertIn('at most seven calendar entries',result['prompt'])
            self.assertIn('at most one optional structured workout with at most six steps',result['prompt'])
            self.assertEqual(list(root.iterdir()),[profile])

if __name__=='__main__':unittest.main()
