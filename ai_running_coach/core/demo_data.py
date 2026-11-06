"""Authored fictional fixtures shared by the local and public dashboard demos."""
from contextlib import closing
from datetime import date,timedelta
import json
from pathlib import Path
import sqlite3

from database_manager import ensure_schema
from planning_engine import parse_profile,render_plan,validate_plan

SAMPLE_DATE=date(2027,5,5)


def step(intensity,seconds=None,metres=None):
    duration={'type':'time','seconds':seconds} if seconds is not None else {'type':'distance','metres':metres}
    return {'intensity':intensity,'duration':duration,'target':{'type':'open'}}


def populate_demo(root,today):
    """Create fresh synthetic data only; never read an existing athlete or config."""
    root=Path(root)
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError('Demo output must be an empty/new directory')
    root.mkdir(parents=True,exist_ok=True)
    monday=today-timedelta(days=today.weekday())
    profile_text='''# SYNTHETIC DEMO — fictional athlete, not training advice
**Athlete Name:** Alex Example (fictional)
**Training Mode:** triathlon
**Heart Rate Zones:** Not configured
**Active Injuries:** Unknown — fictional demonstration
**Weekly Availability:** Monday, Tuesday, Wednesday, Thursday, Friday, Saturday
**Swimming Type:** lap_swimming
**Pool Length:** 25 m
**Coaching Style:** Fictional example used to demonstrate the interface.
**A-Race:** Fictional community triathlon
**Sport:** multisport
'''+f'**Date:** {(monday+timedelta(days=62)).isoformat()}\n**Target Time:** Just finish\n'
    (root/'athlete.md').write_text(profile_text,encoding='utf-8')
    profile=parse_profile(profile_text)
    def session(offset,sid,sport,kind,amount,**extra):
        return {'id':'demo-'+sid,'date':(monday+timedelta(days=offset)).isoformat(),'sport':sport,
                'workout_type':kind,'amount':amount,'target':'Fictional example only; not a prescription for you.',**extra}
    sessions=[
        session(0,'easy-run','running','Easy run','5 km',workout={'name':'DEMO easy run','sport':'running','steps':[step('warmup',metres=1000),step('active',metres=3000),step('cooldown',metres=1000)]}),
        session(1,'bike','cycling','Steady bike','45 min',workout={'name':'DEMO steady bike','sport':'cycling','steps':[step('warmup',seconds=600),step('active',seconds=1500),step('cooldown',seconds=600)]}),
        session(2,'pool','swimming','Pool technique','1200 m',sub_sport='lap_swimming',workout={'name':'DEMO pool swim','sport':'swimming','subSport':'lap_swimming','poolLengthMetres':25,'poolLengthUnit':'m','steps':[step('warmup',metres=200),step('active',metres=800),step('cooldown',metres=200)]}),
        session(3,'mobility','mobility','Mobility','20 min'),
        session(4,'run','running','Easy run with strides','30 min'),
        session(5,'brick-bike','cycling','Brick: bike','50 min',brick={'id':'demo-weekend-brick','order':1},workout={'name':'DEMO brick bike','sport':'cycling','steps':[step('active',seconds=3000)]}),
        session(5,'brick-run','running','Brick: run','15 min',brick={'id':'demo-weekend-brick','order':2},workout={'name':'DEMO brick run','sport':'running','steps':[step('active',seconds=900)]}),
        session(6,'rest','rest','Rest','—'),
    ]
    plan={'schema_version':1,'revision':1,'profile_hash':profile['source_hash'],'goals':profile['goals'],'sessions':sessions}
    validate_plan(plan,profile)
    (root/'training_plan.json').write_text(json.dumps(plan,indent=2)+'\n',encoding='utf-8')
    (root/'training_plan.md').write_text(render_plan(plan),encoding='utf-8')
    written_at=today.isoformat()+'T12:00:00+00:00'
    review={'summary':'DEMO: this authored example shows how one review can coordinate running, cycling and swimming. No AI generated these fixtures.',
            'trajectory':'insufficient_evidence','reviews':[],'plan_changes':[],
            'weekly_priorities':[{'priority':'Explore the sport filters','reason':'Distances remain separate; the shared chart uses recorded timer duration.','evidence':['Fictional activity history']},
                                 {'priority':'Inspect a structured workout','reason':'The pool and brick examples show local FIT previews and separate leg downloads.','evidence':['Authored sample sessions']}],
            'assessment':{'reason':'This is a product demonstration, not an assessment of an athlete.',
                          'evidence':['All profile, activity and review content is fictional.'],
                          'uncertainties':['No real athlete or live model is connected.']},
            'next_review_trigger':'In your own installation, fresh activities or a check-in inform the next review.'}
    with closing(sqlite3.connect(root/'ai_coach_master.sqlite')) as conn,conn:
        ensure_schema(conn)
        identifier=0
        for weeks_back in reversed(range(8)):
            week=monday-timedelta(weeks=weeks_back)
            for offset,kind,name,metres,seconds in ((0,'running','Fictional easy run',5000,1800),(1,'cycling','Fictional bike ride',18000,2700),(2,'lap_swimming','Fictional pool swim',1000,1500)):
                day=week+timedelta(days=offset)
                if day>today:continue
                identifier+=1
                conn.execute('INSERT INTO activities(id,name,type,start_date,distance_m,moving_time_s,timer_duration_s,elapsed_time_s,elevation_gain_m,source,metrics_source,ai_feedback) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                             (identifier,name,kind,day.isoformat()+'T08:00:00',metres,seconds if kind!='lap_swimming' else None,seconds,seconds+120,35 if kind=='running' else None,'local:synthetic-demo','local:synthetic-demo','Fictional recorded activity; no real athlete data.'))
        conn.execute('CREATE TABLE coach_review_jobs(status TEXT,response_json TEXT,prompt_version TEXT,updated_at TEXT)')
        conn.execute('INSERT INTO coach_review_jobs VALUES(?,?,?,?)',('complete',json.dumps(review),'autonomous-coach-v8-multisport',written_at))
        conn.execute('CREATE TABLE daily_coach_reviews(id INTEGER PRIMARY KEY,summary_text TEXT,created_at TEXT)')
        conn.execute('INSERT INTO daily_coach_reviews(summary_text,created_at) VALUES(?,?)',(review['summary'],written_at))
    return root
