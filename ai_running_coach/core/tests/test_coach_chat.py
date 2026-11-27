"""Offline round trips for the bounded external-chat boundary."""
from contextlib import closing
import copy
import json
import importlib.util
import sys
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import ai_coach
from coach_chat_service import CoachChatService, ChatError, REMOTE_ACTIONS
from coach_mcp import HomeAssistantRelay
from database_manager import ensure_schema
from planning_engine import parse_profile, render_plan

TODAY = '2026-11-26'
PROFILE = '**Heart Rate Zones:** Not configured\n**Active Injuries:** None\n**Weekly Availability:** Thursday, Friday, Saturday\n'


class ChatServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = CoachChatService(self.root, today=TODAY)
        self.db = self.root/'ai_coach_master.sqlite'
        with closing(sqlite3.connect(self.db)) as conn:
            ensure_schema(conn)
            conn.execute("INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,source) VALUES (1,'Run','2026-11-25T12:00:00Z',5000,1800,'local:fit')")
            conn.commit()
        self.profile = parse_profile(PROFILE)
        self.plan = {'schema_version': 1, 'revision': 3, 'profile_hash': self.profile['source_hash'],
                     'goals': self.profile['goals'], 'sessions': [
                         {'id': 'easy-run', 'date': '2026-11-27', 'workout_type': 'Run', 'amount': '5 km', 'target': 'Easy conversational effort', 'sport': 'running'}]}
        (self.root/'athlete.md').write_text(PROFILE, encoding='utf-8')
        self.write_plan(self.plan)

    def write_plan(self, plan):
        (self.root/'training_plan.json').write_text(json.dumps(plan), encoding='utf-8')
        (self.root/'training_plan.md').write_text(render_plan(plan), encoding='utf-8')

    def proposal(self, request_id='proposal-001'):
        current = self.service.dispatch('plan')
        session = dict(self.plan['sessions'][0], amount='4 km')
        return {'request_id': request_id, 'base_revision': current['revision'],
                'base_plan_hash': current['base_plan_hash'], 'base_profile_hash': current['base_profile_hash'],
                'summary': 'Shorten the easy run after the reported tiredness.',
                'changes': [{'op': 'update', 'session_id': session['id'], 'session': session, 'reason': 'Athlete requested a shorter easy session.'}]}

    def assert_code(self, code, action, payload=None):
        with self.assertRaises(ChatError) as error:
            self.service.dispatch(action, payload)
        self.assertEqual(error.exception.code, code)

    def test_context_is_bounded_and_excludes_files_streams_and_credentials(self):
        (self.root/'.env').write_text('SECRET=never-return-this', encoding='utf-8')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('ALTER TABLE activities ADD COLUMN gps_json TEXT')
            conn.execute("UPDATE activities SET gps_json='GPS-SECRET'")
            conn.commit()
        result = self.service.dispatch('context')
        self.assertEqual(result['recent_activities'][0]['id'], 1)
        text = json.dumps(result)
        self.assertNotIn('GPS-SECRET', text)
        self.assertNotIn('never-return-this', text)
        for action, payload in [('read_file', {'path': '.env'}), ('plan', {'path': '.env'}), ('activities', {'limit': 51})]:
            self.assert_code('invalid_input', action, payload)

    def test_unapproved_sources_fail_closed_even_for_feedback(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE activities SET source='legacy'")
            conn.commit()
        for action, payload in [('context', {}), ('save_feedback', {'request_id':'feedback-001','reported_on':TODAY,'fields':{'fatigue':5}})]:
            self.assert_code('source_policy', action, payload)

    def test_feedback_dated_idempotent_and_reaches_context_without_model_calls(self):
        payload = {'request_id':'feedback-001','reported_on':TODAY,'fields':{'fatigue':0,'pain':'none','carbs_g':30},'activity_id':1}
        with patch.object(ai_coach, 'api_client', side_effect=AssertionError('No paid model call')):
            first = self.service.dispatch('save_feedback', payload)
            self.assertEqual(first, self.service.dispatch('save_feedback', payload))
            reports = self.service.dispatch('context')['athlete_reports']
        self.assertTrue(any(r['fields'].get('fatigue') == 0 and r['source'] == 'user_chat' for r in reports))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM athlete_checkins').fetchone()[0], 1)
        self.assert_code('conflict', 'save_feedback', dict(payload, comment='Different report'))
        self.assert_code('invalid_input', 'save_feedback', dict(payload, request_id='feedback-002', reported_on='2026-11-27'))
        self.assert_code('invalid_input', 'save_feedback', dict(payload, request_id='feedback-003', activity_id=999))

    def test_proposal_does_not_mutate_plan_and_requires_exact_review_hash(self):
        before = (self.root/'training_plan.json').read_bytes()
        proposal = self.service.dispatch('propose_adjustment', self.proposal())
        self.assertEqual(proposal['status'], 'pending')
        self.assertEqual(before, (self.root/'training_plan.json').read_bytes())
        self.assert_code('stale_proposal', 'apply_proposal', {'proposal_id':proposal['id'],'proposal_hash':'bad'})
        self.assertEqual(proposal, self.service.dispatch('propose_adjustment', self.proposal()))

    def test_apply_uses_journal_commit_and_is_idempotent_without_provider(self):
        proposal = self.service.dispatch('propose_adjustment', self.proposal())
        payload = {'proposal_id':proposal['id'],'proposal_hash':proposal['proposal_hash']}
        with patch.object(ai_coach, 'api_client', side_effect=AssertionError('No paid model call')), patch('generate_dashboard.build_dashboard'):
            result = self.service.dispatch('apply_proposal', payload)
            self.assertEqual(result, self.service.dispatch('apply_proposal', payload))
        self.assertEqual(result['revision'], 4)
        self.assertEqual(json.loads((self.root/'training_plan.json').read_text())['sessions'][0]['amount'], '4 km')
        self.assertFalse((self.root/'.plan-commit.json').exists())
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM daily_coach_reviews').fetchone()[0], 1)
            self.assertEqual(conn.execute('SELECT model,status FROM coach_review_jobs').fetchone(), ('external-chat','complete'))

    def test_plan_profile_and_date_changes_reject_pending_proposals(self):
        for mutation in ('plan', 'profile', 'date'):
            with self.subTest(mutation=mutation):
                proposal = self.service.dispatch('propose_adjustment', self.proposal('proposal-'+mutation))
                if mutation == 'plan':
                    altered = copy.deepcopy(self.plan); altered['sessions'][0]['amount'] = '6 km'; self.write_plan(altered)
                elif mutation == 'profile':
                    (self.root/'athlete.md').write_text(PROFILE+'\n**Athlete Name:** Changed\n', encoding='utf-8')
                else:
                    self.service.today = '2026-11-27'
                self.assert_code('stale_proposal','apply_proposal', {'proposal_id':proposal['id'],'proposal_hash':proposal['proposal_hash']})
                self.service.today = TODAY
                self.write_plan(self.plan)
                (self.root/'athlete.md').write_text(PROFILE, encoding='utf-8')

    def test_rejection_prevents_apply_and_historical_or_invalid_sessions_rejected(self):
        proposal = self.service.dispatch('propose_adjustment', self.proposal())
        payload = {'proposal_id':proposal['id'],'proposal_hash':proposal['proposal_hash']}
        self.service.dispatch('reject_proposal',payload)
        self.assert_code('conflict','apply_proposal',payload)
        invalid = self.proposal('proposal-002'); invalid['changes'][0]['session']['date'] = '2026-11-25'
        self.assert_code('invalid_input','propose_adjustment',invalid)

    def test_crash_journal_recovered_before_reading_and_applied_status_recovered(self):
        proposal = self.service.dispatch('propose_adjustment', self.proposal())
        payload = {'proposal_id':proposal['id'],'proposal_hash':proposal['proposal_hash']}
        with patch('generate_dashboard.build_dashboard'):
            self.service.dispatch('apply_proposal',payload)
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE coach_chat_proposals SET status='pending'")
            conn.commit()
        # Atomic commit completed; service recovers status instead of applying twice.
        self.assertEqual(self.service.dispatch('get_proposal', {'proposal_id':proposal['id']})['status'], 'applied')
        content = json.dumps(self.plan)
        (self.root/'.plan-commit.json').write_text(json.dumps({'fingerprint':'interrupted','old':{'training_plan.json':content},'new':{'training_plan.json':'bad'}}),encoding='utf-8')
        self.service.dispatch('plan')
        self.assertEqual(json.loads((self.root/'training_plan.json').read_text())['revision'],3)
        self.assertFalse((self.root/'.plan-commit.json').exists())

    def test_advanced_overview_is_visible_reviewed_and_retained_when_omitted(self):
        overview = {'start_date': TODAY, 'end_date':'2026-11-28', 'focus':'Easy recovery',
                    'rationale':'Reported fatigue', 'adaptation':'Shorten if needed', 'review_trigger':'After next run'}
        self.plan['planning_overview'] = [overview]
        self.write_plan(self.plan)
        self.assertEqual(self.service.dispatch('plan')['planning_overview'], [overview])
        proposal = self.service.dispatch('propose_adjustment',self.proposal())
        self.assertEqual(proposal['planning_overview_after'], [overview])
        payload = self.proposal('proposal-overview')
        payload['planning_overview'] = [dict(overview,focus='Recovery with reassessment')]
        proposal = self.service.dispatch('propose_adjustment',payload)
        self.assertEqual(proposal['planning_overview_after'][0]['focus'], 'Recovery with reassessment')
        with patch('generate_dashboard.build_dashboard'):
            self.service.dispatch('apply_proposal',{'proposal_id':proposal['id'],'proposal_hash':proposal['proposal_hash']})
        self.assertEqual(json.loads((self.root/'training_plan.json').read_text())['planning_overview'],proposal['planning_overview_after'])

    def test_race_context_filters_nested_geometry(self):
        stored = {'summary':'Saved strategy','gps':'TOP-SECRET-GPS','context':{'activity_count':1,'coordinates':['TOP-SECRET-GPS']},
                  'fitness':{'confidence':'low','evidence':['Observed long run'],'coordinates':['TOP-SECRET-GPS']},
                  'strategies':[{'id':'conservative','title':'Easy start','sections':[{'section_id':1,'guidance':'Walk steep sections','coordinates':['TOP-SECRET-GPS']}]}]}
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('CREATE TABLE race_plans(result_json TEXT,updated_at TEXT)')
            conn.execute('INSERT INTO race_plans VALUES (?,?)',(json.dumps(stored),TODAY));conn.commit()
        result = self.service.dispatch('race_context')
        self.assertEqual(result['race_plans'][0]['summary'],'Saved strategy')
        self.assertNotIn('TOP-SECRET-GPS',json.dumps(result))


class RelayTests(unittest.TestCase):
    def relay(self):
        return HomeAssistantRelay('http://192.168.1.5:8100','t'*43,allow_private_http=True)

    def test_private_http_requires_explicit_literal_lan_and_token(self):
        for url, allow in [('http://192.168.1.5:8100',False),('http://localhost:8100',True),('http://8.8.8.8',True),('https://example.com/path',False),('https://user:pass@example.com',False)]:
            with self.subTest(url=url), self.assertRaises(ChatError):
                HomeAssistantRelay(url,'t'*43,allow)
        with self.assertRaises(ChatError):HomeAssistantRelay('https://example.com','short')

    def test_remote_apply_forbidden_and_redirects_disabled(self):
        relay=self.relay()
        response=Mock(status_code=200)
        response.iter_content.return_value=[b'{"ok":true,"value":{"revision":3}}']
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        relay.session.post=Mock(return_value=response)
        self.assertFalse(relay.session.trust_env)
        self.assertEqual(relay.dispatch('plan', {'days':1}), {'revision':3})
        args=relay.session.post.call_args.kwargs
        self.assertFalse(args['allow_redirects']);self.assertEqual(args['timeout'],(5,30))
        for action in ('apply_proposal','reject_proposal','list_proposals'):
            with self.assertRaises(ChatError):relay.dispatch(action,{})
        self.assertEqual(relay.session.post.call_count,1)

    def test_oversize_redirect_and_private_error_bodies_are_not_forwarded(self):
        relay=self.relay()
        for status, body in [(302,b'secret-token'),(200,b'{"ok":false,"code":"unknown","message":"secret-token"}'),(200,b'x'*540000)]:
            response=Mock(status_code=status)
            response.iter_content.return_value=[body]
            response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            relay.session.post=Mock(return_value=response)
            with self.assertRaises(ChatError) as error:relay.dispatch('context')
            self.assertNotIn('secret-token',str(error.exception))
        for payload in ([],{'value':float('nan')},{'comment':'x'*140000}):
            with self.assertRaises(ChatError):relay.dispatch('save_feedback',payload)


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'Optional MCP SDK is not installed')
class OfficialSDKTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_discovers_ten_bounded_tools_and_calls_backend(self):
        from mcp import Client
        from coach_mcp import build_server
        backend = Mock()
        backend.dispatch.return_value = {'revision':3}
        async with Client(build_server(backend)) as client:
            tools = (await client.list_tools()).tools
            self.assertEqual({tool.name for tool in tools}, {'get_coach_context','get_training_plan','get_recent_activities','get_activity_details','get_race_context','get_weekly_summary','get_session_review','save_session_link','save_athlete_feedback','propose_plan_adjustment'})
            result = await client.call_tool('get_training_plan',{'days':2})
            self.assertEqual(json.loads(result.content[0].text),{'ok':True,'value':{'revision':3}})
            backend.dispatch.assert_called_once_with('plan',{'days':2})

    async def test_real_sdk_activity_details_dispatches_bounded_options(self):
        from mcp import Client
        from coach_mcp import build_server
        backend = Mock()
        backend.dispatch.return_value = {'activity': {'id': 123}, 'splits': {'status': 'not_stored'}}
        async with Client(build_server(backend)) as client:
            result = await client.call_tool('get_activity_details', {'activity_id':123, 'include_streams':True, 'offset':2, 'limit':3})
            envelope = json.loads(result.content[0].text)
            self.assertEqual(envelope['value']['activity']['id'],123)
            backend.dispatch.assert_called_once_with('activity_details', {'activity_id':123, 'include_streams':True, 'offset':2, 'limit':3})

    async def test_real_sdk_provider_id_and_session_tools(self):
        from mcp import Client
        from coach_mcp import build_server
        backend = Mock()
        backend.dispatch.return_value = {'status':'available'}
        async with Client(build_server(backend)) as client:
            await client.call_tool('get_activity_details', {'source':'garmin_personal','source_activity_id':'12345678901'})
            backend.dispatch.assert_called_with('activity_details', {'source':'garmin_personal','source_activity_id':'12345678901','include_streams':False,'offset':0,'limit':100})
            await client.call_tool('get_weekly_summary', {'weeks':12})
            backend.dispatch.assert_called_with('weekly_summary', {'weeks':12})
            await client.call_tool('get_session_review', {'days':28,'limit':20})
            backend.dispatch.assert_called_with('session_review', {'days':28,'limit':20})
            await client.call_tool('save_session_link', {'request_id':'confirm-001','base_plan_hash':'hash','session_id':'run','outcome':'skipped','athlete_confirmed':True})
            backend.dispatch.assert_called_with('save_session_link', {'request_id':'confirm-001','base_plan_hash':'hash','session_id':'run','outcome':'skipped','athlete_confirmed':True,'activity_id':None,'comment':''})

    async def test_real_stdio_process_discovery_and_sanitized_not_ready(self):
        from mcp import Client
        from mcp.client.stdio import StdioServerParameters
        with tempfile.TemporaryDirectory() as directory:
            transport = StdioServerParameters(command=sys.executable,
                args=['-B',str(Path(__file__).resolve().parents[1]/'coach_mcp.py'),'--workspace',directory])
            async with Client(transport, read_timeout_seconds=15) as client:
                tools = (await client.list_tools()).tools
                self.assertEqual(len(tools),10)
                result = await client.call_tool('get_coach_context',{})
                envelope = json.loads(result.content[0].text)
                self.assertEqual(envelope['code'],'not_ready')
                self.assertNotIn(directory,result.content[0].text)


if __name__ == '__main__':
    unittest.main()
