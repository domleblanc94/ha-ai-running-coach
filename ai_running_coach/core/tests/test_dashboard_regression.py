"""Restored UI keeps presentation and the canonical/read-only/security guarantees."""
from contextlib import closing
from datetime import date
from html.parser import HTMLParser
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

import generate_dashboard as dashboard
from planning_engine import parse_profile, parse_plan


PROFILE='''**Zone 1 (Recovery):** < 158 bpm
**Zone 2 (Endurance):** 158 - 166 bpm
**Zone 3 (Tempo):** 167 - 175 bpm
**Zone 4 (Hard):** 176 - 184 bpm
**Zone 5 (Max):** >= 185 bpm
**Active Injuries:** None
**A-Race:** Example Trail 50km
**Date:** November 21, 2026
**Target Time:** Sub 7 hours
'''


class Markup(HTMLParser):
    def __init__(self):
        super().__init__();self.ids=set();self.tags=[];self.event_attributes=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append(tag)
        for key,value in attrs:
            if key=='id': self.ids.add(value)
            if key.lower().startswith('on'): self.event_attributes.append((key,value))


class RestoredDashboardTests(unittest.TestCase):
    def test_dynamic_checkbox_uses_listener_instead_of_inline_javascript(self):
        # URI encoding preserves apostrophes: interpolating generated IDs inside
        # inline event JavaScript would make crafted workout names executable.
        source=(Path(dashboard.__file__).parent/'dashboard_template.py').read_text(encoding='utf-8')
        self.assertNotIn('onchange=',source.lower())
        self.assertIn("addEventListener('change'",source)

    def fixture(self,root,attack=''):
        profile=root/'athlete.md';profile.write_text(PROFILE,encoding='utf-8')
        md=root/'training_plan.md'
        canonical=parse_plan('| Saturday | 2026-10-31 | Trail Run | 7 km | Coach-selected steady effort. |',parse_profile(PROFILE))
        canonical['revision']=3
        canonical['sessions'][0]['target']='Coach-selected effort. '+attack
        (root/'training_plan.json').write_text(json.dumps(canonical),encoding='utf-8')
        # Deliberately stale Markdown must never override the current AI canonical revision.
        md.write_text('| Saturday | 2026-10-31 | STALE WALK ONLY | 1 km | Outdated November 26 schedule |',encoding='utf-8')
        db=root/'data.sqlite'
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('CREATE TABLE activities(id INTEGER PRIMARY KEY,name TEXT,type TEXT,start_date TEXT,distance_m REAL,ai_feedback TEXT,moving_time_s INTEGER,suffer_score REAL,avg_heartrate REAL)')
            conn.executemany('INSERT INTO activities VALUES(?,?,?,?,?,?,?,?,?)',[
                (1,'Morning trail '+attack,'Run','2026-10-31T08:00:00',5000,'Feedback '+attack,1800,40,150),
                (2,'Evening companion run','Run','2026-10-31T18:00:00',2000,'Second distinct feedback',900,10,140)])
            conn.execute('CREATE TABLE daily_coach_reviews(id INTEGER PRIMARY KEY,created_at TEXT,summary_text TEXT)')
            conn.execute('INSERT INTO daily_coach_reviews VALUES(1,?,?)',('2026-10-31 12:00:00','Current coach review '+attack))
            conn.execute('CREATE TABLE coach_review_jobs(fingerprint TEXT PRIMARY KEY,status TEXT,updated_at TEXT,prompt_version TEXT,response_json TEXT)')
            response={'summary':'Current coach review '+attack,'trajectory':'on_track','reviews':[{'activity_id':1,'feedback':'Feedback '+attack},{'activity_id':2,'feedback':'Second distinct feedback'}],'plan_changes':[],'applied_revision':3}
            conn.execute('INSERT INTO coach_review_jobs VALUES(?,?,?,?,?)',('fixture','complete','2026-10-31T12:00:00','autonomous-coach-v2',json.dumps(response)))
            conn.commit()
        return db,md

    def test_starter_review_renders_without_regular_review_alias(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db, md = self.fixture(root)
            with closing(sqlite3.connect(db)) as conn:
                response = json.loads(conn.execute('SELECT response_json FROM coach_review_jobs').fetchone()[0])
                response['weekly_priorities'] = [{'priority': 'Starter weekly priority', 'reason': 'Synthetic reason', 'evidence': ['Activity 1']}]
                response['plan_changes'] = [{'op': 'add', 'session_id': 'starter-session', 'reason': 'Starter session rationale'}]
                conn.execute('UPDATE coach_review_jobs SET prompt_version=?, response_json=?',
                             ('autonomous-coach-v4-starter-week', json.dumps(response)))
                conn.commit()
            page = dashboard.build_dashboard(db, md, root/'dashboard.html', today=date(2026, 10, 31))
            html = page.read_text(encoding='utf-8')
            self.assertIn('Starter weekly priority', html)
            self.assertIn('Starter session rationale', html)

    def metric_fixture(self,root):
        db,md=self.fixture(root)
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('ALTER TABLE activities ADD COLUMN elevation_gain_m REAL')
            conn.executemany('INSERT INTO activities(id,name,type,start_date,distance_m,elevation_gain_m) VALUES(?,?,?,?,?,?)',[
                (10,'Outside both windows','Run','2026-09-05',100000,10000),
                (11,'First day of eight weeks','Run','2026-09-06',1000,100),
                (12,'Before four week window','Run','2026-10-03',2000,200),
                (13,'First day of four weeks','Run','2026-10-04',3000,300),
                (14,'Cycling is not running','Ride','2026-10-31',100000,10000),
                (15,'Future not completed','Run','2026-11-01',100000,10000),
                (16,'Unknown measurements','Run','2026-10-31',None,None),
                (17,'Known zero measurements','Run','2026-10-31',0,0)])
            conn.commit()
        path=root/'training_plan.json'
        plan=json.loads(path.read_text(encoding='utf-8'))
        plan['sessions'] += [
            {'id':'prior-run','date':'2026-10-30','workout_type':'Run','amount':'5 km','target':'Prior scheduled run.'},
            {'id':'timed-run','date':'2026-11-01','workout_type':'Run','amount':'45 min','target':'Time-based workout.'},
            {'id':'old-race-date','date':'2026-11-26','workout_type':'Race','amount':'50 km','target':'Legacy race date does not determine the profile countdown.'}]
        path.write_text(json.dumps(plan),encoding='utf-8')
        return db,md

    def test_restored_calendar_chart_controls_and_canonical_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db,md=self.fixture(root)
            inputs=[db,md,root/'athlete.md',root/'training_plan.json']
            before={p:p.read_bytes() for p in inputs}
            output=dashboard.build_dashboard(db,md,root/'dashboard.html',today=date(2026,10,31))
            html=output.read_text(encoding='utf-8');markup=Markup();markup.feed(html)
            for element in ('calendarView','tableView','weekTabsContainer','calendarGridContainer','volumeChart','btnKm','btnLoad','toggleViewBtn'):
                self.assertIn(element,markup.ids)
            self.assertIn('Morning trail',html);self.assertIn('Evening companion run',html)
            self.assertIn('Second distinct feedback',html)
            self.assertIn('November 21, 2026',html)
            self.assertNotIn('STALE WALK ONLY',html)
            for p in inputs: self.assertEqual(p.read_bytes(),before[p],str(p))

    def test_untrusted_text_cannot_create_scripts_images_or_event_handlers(self):
        attack='</script><script>window.dashboardPwned=1</script><img src=x onerror="window.dashboardPwned=2">'
        with tempfile.TemporaryDirectory() as a,tempfile.TemporaryDirectory() as b:
            safe=Path(a);bad=Path(b)
            safe_db,safe_md=self.fixture(safe)
            bad_db,bad_md=self.fixture(bad,attack)
            safe_html=dashboard.build_dashboard(safe_db,safe_md,safe/'page.html',today=date(2026,10,31)).read_text(encoding='utf-8')
            bad_html=dashboard.build_dashboard(bad_db,bad_md,bad/'page.html',today=date(2026,10,31)).read_text(encoding='utf-8')
            clean=Markup();clean.feed(safe_html)
            injected=Markup();injected.feed(bad_html)
            self.assertEqual(injected.tags.count('script'),clean.tags.count('script'))
            self.assertEqual(injected.tags.count('img'),clean.tags.count('img'))
            self.assertEqual(injected.event_attributes,clean.event_attributes)
            self.assertNotIn('<script>window.dashboardPwned',bad_html)

    def test_running_overview_inclusive_boundaries_coverage_and_true_race_date(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);db,md=self.metric_fixture(root)
            plan=json.loads((root/'training_plan.json').read_text(encoding='utf-8'))
            sources=[db,md,root/'athlete.md',root/'training_plan.json']
            before={p:p.read_bytes() for p in sources}
            with closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro',uri=True)) as conn:
                result=dashboard.build_overview(conn,plan,date(2026,10,31),parse_profile(PROFILE))
            four,eight=result['windows']['28'],result['windows']['56']
            self.assertEqual((four['start'],four['end']),('2026-10-04','2026-10-31'))
            self.assertEqual((eight['start'],eight['end']),('2026-09-06','2026-10-31'))
            self.assertEqual((four['distance_km'],four['ascent_m'],four['longest_km']),(10,300,5))
            self.assertEqual((eight['distance_km'],eight['ascent_m'],eight['longest_km']),(13,600,5))
            self.assertEqual((four['runs'],four['distance_coverage'],four['ascent_coverage']),(5,4,2))
            self.assertEqual((eight['runs'],eight['distance_coverage'],eight['ascent_coverage']),(7,6,4))
            self.assertEqual(result['countdown']['date'],'2026-11-21')
            self.assertEqual(result['countdown']['days'],21)
            self.assertEqual(result['planned_weeks']['2026-10-26'],{'known_km':12,'known_sessions':2,'unknown_sessions':1})
            for p in sources:self.assertEqual(p.read_bytes(),before[p],str(p))

    def test_missing_measurements_stay_unknown_and_recovery_is_a_session(self):
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('CREATE TABLE activities(start_date TEXT,type TEXT,distance_m REAL,elevation_gain_m REAL)')
            conn.execute("INSERT INTO activities VALUES('2026-10-31','Run',NULL,NULL)")
            plan={'sessions':[
                {'id':'rest','date':'2026-10-31','workout_type':'Rest','amount':'-','target':'Rest'},
                {'id':'recovery','date':'2026-10-31','workout_type':'Recovery Run','amount':'3 km','target':'Coach-selected recovery effort'},
                {'id':'tomorrow','date':'2026-11-01','workout_type':'Run','amount':'5 km','target':'Coach target'}]}
            result=dashboard.build_overview(conn,plan,date(2026,10,31))
            self.assertIsNone(result['windows']['28']['distance_km'])
            self.assertIsNone(result['windows']['28']['ascent_m'])
            self.assertIsNone(result['windows']['28']['longest_km'])
            self.assertEqual(result['windows']['28']['distance_coverage'],0)
            self.assertEqual(result['next_session']['id'],'recovery')
        finally:conn.close()

    def test_planned_km_does_not_estimate_time_ranges_or_repetitions(self):
        conn=sqlite3.connect(':memory:')
        try:
            conn.execute('CREATE TABLE activities(start_date TEXT,type TEXT)')
            plan={'sessions':[{'id':str(i),'date':'2026-10-31','workout_type':kind,'amount':amount,'target':'Coach target'} for i,(kind,amount) in enumerate([
                ('Run','5 km'),('Run','45 min'),('Run','5-8 km'),('Run','4x 1 km'),('Run','5 km or 45 min'),('Tempo','8 km')])]}
            result=dashboard.build_overview(conn,plan,date(2026,10,31))
            self.assertEqual(result['planned_weeks']['2026-10-26'],{'known_km':13,'known_sessions':2,'unknown_sessions':4})
        finally:conn.close()


if __name__=='__main__': unittest.main()
