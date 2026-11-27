"""Ongoing coaching evidence stays explicit, auditable and bounded."""
from contextlib import closing
import json
import sqlite3
import unittest
from unittest.mock import patch
from tests import test_coach_chat as fixture
TODAY = fixture.TODAY
from coach_chat_service import ChatError


class OngoingTests(unittest.TestCase):
    setUp = fixture.ChatServiceTests.setUp
    write_plan = fixture.ChatServiceTests.write_plan
    assert_code = fixture.ChatServiceTests.assert_code

    def past_plan(self):
        self.plan['sessions'][0]['date']='2026-11-25'
        self.plan['sessions'][0]['amount']='60 min'
        self.write_plan(self.plan)

    def link(self, **changes):
        value={'request_id':'session-link-001', 'base_plan_hash':self.service.dispatch('session_review')['base_plan_hash'],
               'session_id':'easy-run','outcome':'shortened','athlete_confirmed':True,'activity_id':1,'comment':'Athlete chose a shorter run.'}
        return dict(value,**changes)

    def test_candidate_is_never_automatic_confirmation_or_skipped(self):
        self.past_plan()
        result=self.service.dispatch('session_review')['sessions'][0]
        self.assertEqual(result['status'],'unconfirmed')
        self.assertEqual(result['candidate_activity_ids'],[1])
        self.assertEqual(result['match_confidence'],'medium')
        self.assertIsNone(result['actual'])
        self.plan['sessions'][0]['date']='2026-11-24';self.write_plan(self.plan)
        result=self.service.dispatch('session_review')['sessions'][0]
        self.assertEqual(result['status'],'unconfirmed')
        self.assertEqual(result['candidate_activity_ids'],[])

    def test_explicit_link_retry_correction_audit_and_stale_validation(self):
        self.past_plan()
        payload=self.link()
        with patch('socket.socket.connect',side_effect=AssertionError('No network')):
            first=self.service.dispatch('save_session_link',payload)
            self.assertEqual(first,self.service.dispatch('save_session_link',payload))
            result=self.service.dispatch('session_review')['sessions'][0]
        self.assertEqual(result['status'],'shortened')
        self.assertEqual(result['actual']['moving_time_s'],1800)
        self.assertIsNone(result['actual']['timer_difference_s'])
        self.assertEqual(result['intent']['planned_duration_s'],3600)
        self.assert_code('conflict','save_session_link',dict(payload,comment='changed'))
        self.assert_code('stale_proposal','save_session_link',self.link(request_id='session-link-002',base_plan_hash='bad'))
        self.assert_code('invalid_input','save_session_link',self.link(request_id='session-link-003',athlete_confirmed=False))
        self.service.dispatch('save_session_link',self.link(request_id='session-link-004',outcome='unlinked',activity_id=None))
        result=self.service.dispatch('session_review')['sessions'][0]
        self.assertEqual(result['status'],'unlinked')
        self.assertIsNone(result['actual'])
        self.assertEqual(len(result['confirmation_history']),2)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM coach_session_links').fetchone()[0],2)

    def test_skipped_requires_explicit_report_and_no_activity(self):
        self.past_plan()
        self.assert_code('invalid_input','save_session_link',self.link(outcome='skipped'))
        self.service.dispatch('save_session_link',self.link(outcome='skipped',activity_id=None))
        self.assertEqual(self.service.dispatch('session_review')['sessions'][0]['status'],'skipped')

    def test_plan_change_flags_original_confirmed_intent(self):
        self.past_plan();self.service.dispatch('save_session_link',self.link())
        self.plan['sessions'][0]['amount']='30 min';self.write_plan(self.plan)
        result=self.service.dispatch('session_review')['sessions'][0]
        self.assertTrue(result['intent_changed_since_confirmation'])
        self.assertEqual(result['confirmation']['intent']['planned_duration_s'],3600)

    def test_weekly_summary_missing_coverage_sources_and_units(self):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO activities(id,type,start_date,source,elevation_gain_m,timer_duration_s) VALUES (2,'Run','2026-11-25','local:fit',0,0)")
            conn.commit()
        result=self.service.dispatch('weekly_summary',{'weeks':12})
        self.assertEqual(len(result['weeks']),12)
        run=result['weeks'][-1]['by_sport']['running']
        self.assertEqual(run['activity_count'],2)
        self.assertEqual(run['distance_m_coverage'],1)
        self.assertEqual(run['moving_time_s_coverage'],1)
        self.assertEqual(run['elevation_gain_m'],0)
        self.assertEqual(run['timer_duration_s'],0)
        self.assertIsNone(run['elapsed_time_s'])
        self.assertEqual(run['longest_distance_m'],5000)
        self.assertEqual(run['sources'],['local:fit'])
        self.assert_code('invalid_input','weekly_summary',{'weeks':13})
        self.assert_code('invalid_input','weekly_summary',{'weeks':True})

    def test_optional_reports_reuse_existing_validation_no_inferences(self):
        payload={'request_id':'checkin-option-001','reported_on':TODAY,'activity_id':1,
                 'fields':{'rpe':7,'felt_effort':'Harder than expected','limiting_factor':'Hills', 'recovery':'Legs felt tired next morning','fueling_tolerance':'No stomach issues'}}
        self.service.dispatch('save_feedback',payload)
        reports=self.service.dispatch('context')['athlete_reports']
        self.assertEqual(reports[-1]['fields'],payload['fields'])
        self.assert_code('invalid_input','save_feedback',dict(payload,request_id='checkin-option-002',fields={'rpe':11}))
        self.assert_code('invalid_input','save_feedback',dict(payload,request_id='checkin-option-003',fields={'felt_effort':True}))

    def test_new_actions_fail_closed_on_source_policy(self):
        self.past_plan();payload=self.link()
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("UPDATE activities SET source='legacy'");conn.commit()
        for action,value in [('weekly_summary',{}),('session_review',{}),('save_session_link',payload)]:
            self.assert_code('source_policy',action,value)

    def test_calendar_weeks_and_validated_timed_workout(self):
        self.past_plan()
        from workout_schema import validate_workout
        self.plan['sessions'][0]['workout'] = validate_workout({'name':'Steady','sport':'running','steps':[
            {'intensity':'warmup','duration':{'type':'time','seconds':600},'target':{'type':'open'}},
            {'intensity':'active','duration':{'type':'time','seconds':1200},'target':{'type':'heart_rate','lowBpm':140,'highBpm':155}}]})
        self.write_plan(self.plan)
        result=self.service.dispatch('session_review')['sessions'][0]
        self.assertEqual(result['intent']['planned_duration_s'],1800)
        weeks=self.service.dispatch('weekly_summary')['weeks']
        self.assertEqual(weeks[-1]['from'],'2026-11-23')
        self.assertEqual(weeks[-1]['through'],TODAY)
        self.assertEqual(weeks[-1]['calendar_week_ending'],'2026-11-29')
        self.assertTrue(weeks[-1]['partial_week'])
        self.assertFalse(weeks[-2]['partial_week'])
        self.service.dispatch('save_session_link',self.link())
        self.service.dispatch('save_feedback',{'request_id':'linked-checkin-001','reported_on':'2026-11-26','activity_id':1,'fields':{'rpe':8,'recovery':'Sore legs next morning'}})
        result=self.service.dispatch('session_review')['sessions'][0]
        self.assertEqual(result['athlete_reports'][0]['reported_on'],'2026-11-26')
        self.assertEqual(result['athlete_reports'][0]['fields']['rpe'],8)
        from planning_engine import build_evidence
        with closing(sqlite3.connect(self.db)) as conn:
            evidence=build_evidence(conn,TODAY,self.plan)
        self.assertEqual(evidence['session_review']['sessions'][0]['status'],'shortened')
        self.assertNotIn('confirmation_history',evidence['session_review']['sessions'][0])

    def test_provider_resolution_is_unambiguous_and_detail_allowlist_is_private(self):
        with closing(sqlite3.connect(self.db)) as conn:
            for name in ('raw_laps_json','activity_hr_zones_json','stream_metadata_json'):
                if name not in {r[1] for r in conn.execute('PRAGMA table_info(activities)')}:conn.execute('ALTER TABLE activities ADD COLUMN '+name+' TEXT')
            conn.execute("UPDATE activities SET source='garmin_personal', source_activity_id='12345678901', raw_laps_json=?, activity_hr_zones_json=?,stream_metadata_json=?",(
                json.dumps([{'lap_index':1,'distance_m':1000,'latitude':'GPS-SECRET','token':'SECRET'}]),
                json.dumps({'source':'garmin_activity_fit','status':'recorded','boundaries':[{'zone':1,'min_bpm':100,'max_bpm':140,'latitude':'GPS-SECRET'}],'source_index_times_s':[0,120,240],'seconds_basis':'fit_array_index_unmapped','secret':'SECRET'}),
                json.dumps({'source':'garmin_activity_fit','bucket_seconds':30,'time_basis':'elapsed_bucket_start','secret':'SECRET'})))
            conn.commit()
        payload={'source':'garmin_personal','source_activity_id':'12345678901'}
        result=self.service.dispatch('activity_details',payload)
        self.assertEqual(result['activity']['id'],1)
        self.assertEqual(result['laps']['items'][0]['lap_index'],1)
        self.assertEqual(result['activity_hr_zone_configuration']['boundaries'][0]['max_bpm'],140)
        self.assertEqual(result['activity_hr_zone_configuration']['source_index_times_s'],[0,120,240])
        self.assertEqual(result['activity_hr_zone_configuration']['seconds_basis'],'fit_array_index_unmapped')
        self.assertIn('not established as historical',result['hr_zone_configuration']['scope'])
        self.assertNotIn('SECRET',json.dumps(result))
        self.assert_code('invalid_input','activity_details',dict(payload,activity_id=1))
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("INSERT INTO activities(id,type,start_date,source,source_activity_id) VALUES (3,'Run','2026-11-25','garmin_personal','12345678901')");conn.commit()
        self.assert_code('conflict','activity_details',payload)


if __name__=='__main__':unittest.main()
