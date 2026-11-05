"""Import explicitly mapped local CSV/JSON activity summaries without any API.

This is a documented summary schema, not a FIT/GPX or universal watch parser.
Use data you are entitled to process; changing a file format does not remove
restrictions imposed by the service from which data originated.
"""
import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import sys

from database_manager import ensure_schema, ingest_payload

ROOT=Path(__file__).resolve().parent
REQUIRED={'id','start_date','type'}
TEXT={'name','description'}
NUMERIC={'distance_m','moving_time_s','elapsed_time_s','elevation_gain_m',
         'avg_heartrate','max_heartrate','perceived_exertion'}
ALLOWED=REQUIRED|TEXT|NUMERIC


def validate_activity(row):
    if not isinstance(row,dict) or set(row)-ALLOWED or not REQUIRED.issubset(row):
        raise ValueError('Each row requires id, start_date, type and only documented optional fields')
    value=row['id']
    if isinstance(value,bool) or not str(value).isdigit() or not 0<int(value)<2**63:
        raise ValueError('id must be a positive integer smaller than 2^63')
    when=row['start_date']
    if not isinstance(when,str) or not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[T ])',when):
        raise ValueError('start_date must begin with YYYY-MM-DD, optionally followed by an ISO time')
    try:datetime.fromisoformat(when.replace('Z','+00:00'))
    except ValueError:raise ValueError('start_date must be an ISO date or timestamp') from None
    kind=row['type']
    if not isinstance(kind,str) or not kind.strip() or len(kind)>100:
        raise ValueError('type must be a short sport name, for example Run or TrailRun')
    clean={'id':int(value),'start_date':when,'type':kind.strip()}
    for key in TEXT:
        if key not in row:continue
        value=row[key]
        if value is not None and (not isinstance(value,str) or len(value)>10000):
            raise ValueError(key+' must be text or null')
        clean[key]=value
    for key in NUMERIC:
        if key not in row:continue
        value=row[key]
        if value is None or value=='':
            clean[key]=None
            continue
        if isinstance(value,bool):raise ValueError(key+' must be a numeric measurement')
        try:number=float(value)
        except (TypeError,ValueError):raise ValueError(key+' must be a numeric measurement') from None
        if not math.isfinite(number) or number<0:
            raise ValueError(key+' must be a finite nonnegative measurement')
        if key in {'avg_heartrate','max_heartrate'} and not 0<number<=300:
            raise ValueError(key+' must be positive bpm, or omitted when unknown')
        if key=='perceived_exertion' and not 1<=number<=10:
            raise ValueError('perceived_exertion must be self-reported RPE 1-10, or omitted')
        clean[key]=int(number) if number.is_integer() else number
    return clean


def read_activities(path):
    path=Path(path)
    if path.suffix.lower()=='.csv':
        with path.open(encoding='utf-8-sig',newline='') as handle:
            reader=csv.DictReader(handle)
            if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames):
                raise ValueError('CSV requires unique documented column headers')
            raw=list(reader)
    elif path.suffix.lower()=='.json':
        with path.open(encoding='utf-8-sig') as handle:raw=json.load(handle)
    else:raise ValueError('Input must be a .csv or .json file using the documented summary schema')
    if not isinstance(raw,list) or not raw:raise ValueError('Input must contain a nonempty list of activity rows')
    result=[];seen=set()
    for index,row in enumerate(raw,1):
        try:clean=validate_activity(row)
        except ValueError as exc:raise ValueError(f'Row {index}: {exc}') from None
        if clean['id'] in seen:raise ValueError(f'Row {index}: duplicate id in input batch')
        seen.add(clean['id']);result.append(clean)
    return result


def import_activities(input_path,db_path,dataset='watch',dry_run=False):
    if not isinstance(dataset,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,60}',dataset):
        raise ValueError('dataset must be 1-60 letters, numbers, underscores or hyphens')
    activities=read_activities(input_path)
    source='local:'+dataset
    result={'rows':len(activities),'source':source,'dry_run':bool(dry_run),'backup':None}
    if dry_run:return result
    db_path=Path(db_path)
    if not db_path.parent.is_dir():raise ValueError('Database parent directory does not exist')
    if db_path.exists() and db_path.stat().st_size:
        backup=db_path.with_name(db_path.stem+'.import-backup-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'.sqlite')
        with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)) as old:
            with closing(sqlite3.connect(backup)) as saved:
                old.backup(saved)
        result['backup']=str(backup)
    with closing(sqlite3.connect(db_path)) as conn,conn:
        conn.row_factory=sqlite3.Row
        conn.execute('BEGIN IMMEDIATE')
        ensure_schema(conn)
        for activity in activities:
            ingest_payload(conn,{'source':source,'metadata_only':True,'activity_metrics':activity})
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file',required=True,type=Path,help='CSV or JSON in the documented metre/second/bpm summary schema')
    parser.add_argument('--db',type=Path,default=ROOT/'ai_coach_master.sqlite')
    parser.add_argument('--dataset',default='watch',help='Stable source namespace; reuse for later updates from the same dataset')
    parser.add_argument('--dry-run',action='store_true',help='Validate the entire file without creating or changing database files')
    args=parser.parse_args(argv)
    try:result=import_activities(args.file,args.db,args.dataset,args.dry_run)
    except (OSError,ValueError,sqlite3.Error) as exc:
        print(str(exc),file=sys.stderr);return 1
    print(json.dumps(result))
    return 0


if __name__=='__main__':sys.exit(main())
