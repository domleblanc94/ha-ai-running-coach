"""Feedback round trips through actual local persistence, imports and AI evidence."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

import ai_coach
from coach_checkin import record_checkin, validate_feedback_fields, FEEDBACK_FIELD_UNITS
from database_manager import ensure_schema, ingest_payload
from planning_engine import build_evidence, parse_structured_notes


class FeedbackWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'coach.sqlite'
        self.conn=sqlite3.connect(self.db)
        self.addCleanup(self.conn.close)
        self.conn.row_factory=sqlite3.Row
        ensure_schema(self.conn)
        self.conn.commit()

    def payload(self, source='strava', activity_id=1, text='First athlete report', when='2026-10-29T12:00:00Z'):
        return {'source':source,'activity_metrics':{'id':activity_id,'type':'Run',
                'start_date':when,'distance_m':5000,'moving_time_s':1800,'description':text},
                'metadata_only':True}

    def insert(self, **kwargs):
        result=ingest_payload(self.conn,self.payload(**kwargs))
        self.conn.commit()
        return result

    def evidence(self):
        return build_evidence(self.conn,'2026-10-31')

    def test_missing_unknown_and_explicit_zero_none_remain_distinct(self):
        self.assertEqual(validate_feedback_fields({}),{})
        fields=validate_feedback_fields({'rpe':'unknown','fatigue':0,'pain':'none','recovery':'unknown'})
        self.assertEqual(fields,{'rpe':'unknown','fatigue':0,'pain':'none','recovery':'unknown'})
        for fields in ({'rpe':0},{'rpe':11},{'rpe':float('nan')},{'pain':True},
                       {'invented':1},{'gels':1.5},{'carbs_g':-1}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                validate_feedback_fields(fields)

    def test_nutrition_quantities_are_totals_and_tolerance_is_not_intake(self):
        fields=validate_feedback_fields({'carbs_g':30,'fluid_ml':500,'gels':1})
        self.assertEqual(fields,{'carbs_g':30,'fluid_ml':500,'gels':1})
        self.assertIn('not grams per hour',FEEDBACK_FIELD_UNITS['carbs_g'])
        tolerated=validate_feedback_fields({'fueling_tolerance':'Gels tolerated'})
        self.assertNotIn('carbs_g',tolerated)
        self.assertNotIn('gels',tolerated)

    def test_real_cli_roundtrip_and_unknown_option_does_not_add_report(self):
        self.insert()
        script=Path(__file__).resolve().parents[1]/'coach_checkin.py'
        command=[sys.executable,str(script),'--db',str(self.db),'--date','2026-10-31',
                 '--activity-id','1','--rpe','7','--fatigue','0','--pain','none',
                 '--fueling','Gels tolerated; intake unknown','--comment','Felt fine']
        result=subprocess.run(command,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['reported_on'],'2026-10-31')
        row=self.conn.execute('SELECT * FROM athlete_checkins').fetchone()
        fields=json.loads(row['feedback_json'])
        self.assertEqual(fields['rpe'],7)
        self.assertEqual(fields['fatigue'],0)
        self.assertNotIn('carbs_g',fields)
        failed=subprocess.run(command+['--imaginary-measurement','10'],capture_output=True,text=True)
        self.assertNotEqual(failed.returncode,0)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM athlete_checkins').fetchone()[0],1)

    def test_unknown_activity_rejected_without_creating_checkin_table(self):
        with self.assertRaises(ValueError):
            record_checkin(self.db,'2026-10-31',{'pain':'none'},activity_id=999)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='athlete_checkins'").fetchone())

    def test_checkin_survives_provider_refresh_and_reaches_ai_prompt(self):
        self.insert(text='Source note')
        record_checkin(self.db,'2026-10-31',{'fueling_tolerance':'Gels tolerated'},
                       comment='Physically fine. Intake not recorded.',source='user_chat')
        self.insert(source='garmin',activity_id=99,text='Provider changed note')
        self.insert(text='Refreshed Strava note')
        evidence=self.evidence()
        reports=[r for r in evidence['feedback_history'] if r['source']=='user_chat']
        self.assertEqual(len(reports),1)
        self.assertEqual(reports[0]['fields'],{'fueling_tolerance':'Gels tolerated'})
        self.assertNotIn('rpe',reports[0]['fields'])
        self.assertNotIn('recovery',reports[0]['fields'])
        prompt=ai_coach.make_prompt({'zones':{},'goals':[]},{'sessions':[]},evidence,'Review training')
        self.assertIn('Physically fine. Intake not recorded.',prompt)
        self.assertIn('Gels tolerated',prompt)

    def test_source_note_revisions_preserve_exact_text_and_provider_ids(self):
        first='First report\n[coach: rpe=7; fatigue=high]'
        second='Different provider report\n[coach: pain=none]'
        self.insert(text=first)
        self.insert(source='garmin',activity_id=99,text=second)
        self.insert(text='Corrected source note')
        rows=self.conn.execute('SELECT source,source_activity_id,text FROM activity_feedback_history ORDER BY id').fetchall()
        self.assertEqual([tuple(r) for r in rows],[('strava','1',first),('garmin','99',second),('strava','1','Corrected source note')])
        for _ in range(3):self.insert(text='Corrected source note')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM activity_feedback_history').fetchone()[0],3)
        text=json.dumps(self.evidence()['feedback_history'])
        self.assertIn('First report',text)
        self.assertIn('Different provider report',text)

    def test_structured_descriptions_and_local_notes_reach_ai_without_guessing_prose(self):
        self.insert(text='[coach: rpe=4; fatigue=unknown; pain=none; recovery=normal]')
        self.conn.execute("UPDATE activities SET notes='Local athlete detail: enjoyed the route'")
        self.conn.commit()
        evidence=self.evidence()
        activity=evidence['recent_7d'][0]
        self.assertEqual(activity['reported_feedback'][0]['fields']['rpe'],4)
        self.assertEqual(activity['reported_feedback'][0]['fields']['pain'],'none')
        self.assertIn('Local athlete detail',activity['notes'])
        self.assertIsNone(activity.get('perceived_exertion'))
        prose=parse_structured_notes('Felt amazing and had a gel')
        self.assertEqual(prose,[])
        malformed=parse_structured_notes('[coach: rpe=banana; fatigue=0; unknownfield=4]')
        self.assertEqual(malformed[0]['fields'],{'fatigue':0})
        self.assertGreaterEqual(len(malformed[0]['warnings']),2)

    def test_fifty_six_day_feedback_does_not_become_permanent_injury(self):
        self.insert(text='Historical hamstring observation',when='2026-09-06T12:00:00Z')
        self.insert(activity_id=2,text='Outside evidence window',when='2026-09-05T12:00:00Z')
        record_checkin(self.db,'2026-10-28',{'pain':'high','topic':'hamstring','status':'active'})
        record_checkin(self.db,'2026-10-31',{'pain':'none','topic':'hamstring','status':'resolved'})
        evidence=self.evidence()
        history=evidence['feedback_history']
        old=[r for r in history if 'Historical hamstring' in r['comment']]
        self.assertTrue(old)
        self.assertFalse(old[0]['current_report'])
        self.assertNotIn('Outside evidence window',json.dumps(history))
        active=[r for r in history if r['fields'].get('status')=='active']
        self.assertFalse(active[0]['current_report'])
        current=[r for r in evidence['current_athlete_reports'] if r['fields'].get('topic')=='hamstring']
        self.assertEqual(len(current),1)
        self.assertEqual(current[0]['fields']['pain'],'none')

    def test_expired_and_unknown_reports_do_not_invent_recovery(self):
        record_checkin(self.db,'2026-10-30',{'pain':'high','topic':'ankle','expires':'2026-10-30'})
        record_checkin(self.db,'2026-10-31',{'pain':'unknown','recovery':'unknown','topic':'hamstring'})
        history=self.evidence()['feedback_history']
        self.assertFalse(next(r for r in history if r['fields']['topic']=='ankle')['current_report'])
        unknown=next(r for r in history if r['fields']['topic']=='hamstring')
        self.assertEqual(unknown['fields']['recovery'],'unknown')
        self.assertNotIn('recovered',unknown['fields'].values())

    def test_weekly_ascent_duration_coverage_and_longest_duration_are_running_only(self):
        self.insert()
        self.conn.execute('UPDATE activities SET elevation_gain_m=120 WHERE id=1')
        self.conn.execute("INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,elevation_gain_m) VALUES(2,'TrailRun','2026-10-30',4000,3600,NULL)")
        self.conn.execute("INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,elevation_gain_m) VALUES(3,'Run','2026-10-31',1000,NULL,0)")
        self.conn.execute("INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,elevation_gain_m) VALUES(4,'Ride','2026-10-31',50000,7200,500)")
        self.conn.commit()
        week=self.evidence()['running_weeks_8'][-1]
        self.assertEqual(week['run_count'],3)
        self.assertEqual(week['ascent_m'],120)
        self.assertEqual(week['ascent_coverage'],2)
        self.assertEqual(week['duration_coverage'],2)
        self.assertEqual(week['longest_duration_min'],60)

    def test_evidence_read_only_does_not_create_checkin_tables(self):
        self.insert()
        before=self.db.read_bytes()
        with closing(sqlite3.connect(self.db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
            build_evidence(conn,'2026-10-31')
        self.assertEqual(self.db.read_bytes(),before)
        self.assertIsNone(self.conn.execute("SELECT name FROM sqlite_master WHERE name='athlete_checkins'").fetchone())


if __name__=='__main__':unittest.main()
