"""Record explicit dated athlete feedback without calling the AI or a source API."""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import sys

DEFAULT_DB = Path(__file__).resolve().parent / 'ai_coach_master.sqlite'
FEEDBACK_FIELD_UNITS = {
    'rpe':'self-reported exertion, scale 1-10',
    'fatigue':'self-reported category or scale 0-10',
    'pain':'self-reported category or scale 0-10',
    'sleep_hours':'hours slept',
    'carbs_g':'reported period/session total grams; not grams per hour',
    'fluid_ml':'reported period/session total millilitres; not millilitres per hour',
    'gels':'reported period/session total gel count; not gels per hour',
}

NUMERIC_FIELDS = {'rpe':(1,10),'sleep_hours':(0,24),'carbs_g':(0,None),
                  'fluid_ml':(0,None),'gels':(0,None)}
TEXT_FIELDS = {'recovery','fueling','fueling_tolerance','topic'}
KNOWN_FIELDS = set(NUMERIC_FIELDS) | TEXT_FIELDS | {'fatigue','pain','sleep_quality','status','expires'}


def validate_feedback_fields(fields):
    """Keep missing, explicitly unknown, none and numeric zero distinct.

    This validates self-reports; it does not infer clinical state or prescribe
    recovery, food quantities, or treatment from prose.
    """
    if not isinstance(fields,dict) or set(fields)-KNOWN_FIELDS:
        raise ValueError('Unknown structured feedback field')
    clean = {}
    for key,value in fields.items():
        if value is None or (isinstance(value,str) and value.strip().lower()=='unknown'):
            clean[key]='unknown'
            continue
        if key in NUMERIC_FIELDS or key in {'fatigue','pain'}:
            if key in {'fatigue','pain'} and isinstance(value,str) and value.strip().lower() in {'none','low','moderate','high'}:
                clean[key]=value.strip().lower()
                continue
            if isinstance(value,bool):
                raise ValueError(key+' must be a number or explicit category')
            try:
                number=float(value)
            except (TypeError,ValueError):
                raise ValueError(key+' must be a number or explicit category') from None
            low,high = NUMERIC_FIELDS.get(key,(0,10))
            if not math.isfinite(number) or number<low or (high is not None and number>high):
                raise ValueError(key+' is outside its supported range')
            if key=='gels' and not number.is_integer():
                raise ValueError('gels must be a whole count')
            clean[key]=int(number) if number.is_integer() else number
        elif key=='expires':
            if not isinstance(value,str):
                raise ValueError('expires must be an ISO date')
            clean[key]=date.fromisoformat(value).isoformat()
        elif key in {'sleep_quality','status'}:
            choices={'good','fair','poor'} if key=='sleep_quality' else {'active','resolved'}
            if not isinstance(value,str) or value.strip().lower() not in choices:
                raise ValueError('Invalid '+key)
            clean[key]=value.strip().lower()
        else:
            if not isinstance(value,str) or not value.strip() or len(value)>500:
                raise ValueError(key+' must be a short self-report')
            clean[key]=value.strip()
    return clean


def record_checkin(db_path, reported_on, fields, comment='', activity_id=None, source='local_cli'):
    """Append a report; source imports cannot overwrite this table."""
    if not isinstance(reported_on,str):
        raise ValueError('reported_on must be an ISO date')
    reported_on=date.fromisoformat(reported_on).isoformat()
    clean=validate_feedback_fields(fields)
    if not isinstance(comment,str) or len(comment)>10000:
        raise ValueError('comment must be text of at most 10000 characters')
    comment=comment.strip()
    if not clean and not comment:
        raise ValueError('Supply at least one field or a comment')
    if activity_id is not None and type(activity_id) is not int:
        raise ValueError('activity_id must be an integer')
    if source not in {'local_cli','user_chat'}:
        raise ValueError('Unknown check-in source')
    path=Path(db_path)
    if not path.is_file():
        raise ValueError('Activity database does not exist; check the --db path')
    with closing(sqlite3.connect(path)) as conn,conn:
        if activity_id is not None:
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='activities'").fetchone():
                raise ValueError('No activity table for linked check-in')
            if not conn.execute('SELECT 1 FROM activities WHERE id=?',(activity_id,)).fetchone():
                raise ValueError('Unknown linked activity ID')
        conn.execute('''CREATE TABLE IF NOT EXISTS athlete_checkins (
            id INTEGER PRIMARY KEY AUTOINCREMENT, reported_on TEXT NOT NULL,
            activity_id INTEGER, source TEXT NOT NULL, feedback_json TEXT NOT NULL,
            comment TEXT NOT NULL, created_at TEXT NOT NULL)''')
        cursor=conn.execute('''INSERT INTO athlete_checkins
            (reported_on,activity_id,source,feedback_json,comment,created_at) VALUES (?,?,?,?,?,?)''',
            (reported_on,activity_id,source,json.dumps(clean,allow_nan=False),comment,
             datetime.now(timezone.utc).isoformat()))
        return cursor.lastrowid


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db',type=Path,default=DEFAULT_DB)
    parser.add_argument('--date',required=True,help='Date experienced, YYYY-MM-DD')
    for flag in sorted(KNOWN_FIELDS):
        parser.add_argument('--'+flag.replace('_','-'),dest=flag,help=FEEDBACK_FIELD_UNITS.get(flag))
    parser.add_argument('--comment',default='')
    parser.add_argument('--activity-id',type=int)
    parser.add_argument('--source',choices=('local_cli','user_chat'),default='local_cli')
    args=parser.parse_args(argv)
    fields={key:getattr(args,key) for key in KNOWN_FIELDS if getattr(args,key) is not None}
    try:
        checkin_id=record_checkin(args.db,args.date,fields,args.comment,args.activity_id,args.source)
    except (ValueError,sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps({'id':checkin_id,'reported_on':args.date,'source':args.source}))
    return 0


if __name__=='__main__':
    sys.exit(main())
