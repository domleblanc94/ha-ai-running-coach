"""Activity ingestion preserving provider identities and conservative evidence."""
import hashlib
import json
import math
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from statistics import mean, pstdev
from source_policy import normalize_sport

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(SCRIPT_DIR, 'ai_coach_master.sqlite')
DATA_DIR = os.path.join(SCRIPT_DIR, 'ai_coach_data')


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _validated_zones(zones):
    result = []
    raw = zones.get('heart_rate', {}).get('zones', []) if isinstance(zones,dict) else []
    if not isinstance(raw,list) or not 1 <= len(raw) <= 5:
        return []
    for i, zone in enumerate(raw):
        if not isinstance(zone,dict):
            return []
        low, high = _number(zone.get('min')), _number(zone.get('max'))
        if i == len(raw)-1 and high == -1:
            high = math.inf
        if low is None or high is None or low < 0 or high <= low:
            return []
        if result and low != result[-1][1]:
            return []
        result.append((low,high))
    return result


def calculate_stream_features(streams, zones, activity_type=None):
    """Zone sample fractions are not elapsed time; drift needs a steady flat run.

    A valid drift value remains observational, never a fitness diagnosis.
    """
    result = {'cardiac_drift_pct':None,'drift_validity':'insufficient_data',
              'hr_zone_basis':'unknown'}
    streams = streams or {}
    hr = [_number(v) for v in streams.get('heartrate',[])]
    valid_hr = [v for v in hr if v is not None and 30 <= v <= 240]
    bounds = _validated_zones(zones)
    if valid_hr and bounds:
        result['hr_zone_basis'] = 'valid_hr_sample_fraction_not_time'
        for i,(low,high) in enumerate(bounds,1):
            result[f'hr_zone_{i}_pct'] = round(100*sum(low<=h<high for h in valid_hr)/len(valid_hr),1)
    if normalize_sport(activity_type) != 'run':
        result['drift_validity'] = 'unsupported_sport'
        return result
    times = [_number(v) for v in streams.get('time',[])]
    speed = [_number(v) for v in streams.get('velocity_smooth',[])]
    if len(hr)<2 or len(times)!=len(hr) or len(speed)!=len(hr):
        return result
    if any(t is None for t in times) or any(b<=a for a,b in zip(times,times[1:])):
        result['drift_validity'] = 'invalid_timestamps'
        return result
    if times[-1]-times[0]<1800:
        result['drift_validity'] = 'too_short'
        return result
    if max(b-a for a,b in zip(times,times[1:]))>10:
        result['drift_validity'] = 'sampling_gaps'
        return result
    moving = streams.get('moving')
    if moving is not None and len(moving)!=len(hr):
        return result
    rows, possible = [], 0
    for i in range(len(times)-1):
        if times[i]<times[0]+600:
            continue
        duration = times[i+1]-times[i]
        possible += duration
        if (hr[i] is None or not 30<=hr[i]<=240 or speed[i] is None
                or not 1<=speed[i]<=8 or (moving is not None and moving[i]!=1)):
            continue
        rows.append((times[i],hr[i],speed[i],duration))
    if not possible or sum(r[3] for r in rows)/possible<.95:
        result['drift_validity'] = 'pauses_or_sensor_gaps'
        return result
    grades = streams.get('grade_smooth')
    if grades is None or len(grades)!=len(hr):
        result['drift_validity'] = 'terrain_unknown'
        return result
    valid_grades = [_number(g) for t,g in zip(times,grades) if t>=times[0]+600]
    speeds = [r[2] for r in rows]
    if any(g is None or abs(g)>2 for g in valid_grades) or pstdev(speeds)/mean(speeds)>.1:
        result['drift_validity'] = 'variable_effort_or_terrain'
        return result
    midpoint = (times[0]+600+times[-1])/2
    halves = [[r for r in rows if (r[0]<midpoint)==first] for first in (True,False)]
    if not all(halves):
        return result
    efficiencies = [sum(r[2]*r[3] for r in h)/sum(r[1]*r[3] for r in h) for h in halves]
    result.update(cardiac_drift_pct=round(100*(1-efficiencies[1]/efficiencies[0]),2),drift_validity='valid')
    return result


BASE_COLUMNS = {'name':'TEXT','description':'TEXT','type':'TEXT','start_date':'TEXT',
    'distance_m':'REAL','moving_time_s':'INTEGER','elapsed_time_s':'INTEGER','elevation_gain_m':'REAL','calories':'REAL',
    'suffer_score':'REAL','perceived_exertion':'REAL','avg_heartrate':'REAL','max_heartrate':'REAL',
    'avg_watts':'REAL','weighted_avg_watts':'REAL','cardiac_drift_pct':'REAL',
    **{f'hr_zone_{n}_pct':'REAL' for n in range(1,6)},'raw_splits_json':'TEXT',
    'downsampled_streams_json':'TEXT','ai_feedback':'TEXT','notes':'TEXT','source':'TEXT',
    'source_activity_id':'TEXT','normalized_type':'TEXT','source_start_date_utc':'TEXT',
    'hr_zones_json':'TEXT','hr_zone_basis':'TEXT','drift_validity':'TEXT','imported_at':'TEXT','metrics_source':'TEXT','stream_source':'TEXT'}
BASE_COLUMNS.update({'training_load':'REAL','provider_metrics_json':'TEXT','hr_zone_seconds_json':'TEXT','elapsed_time_s':'REAL','timer_duration_s':'REAL'})


def ensure_schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS activities (id INTEGER PRIMARY KEY)')
    existing = {r[1] for r in conn.execute('PRAGMA table_info(activities)')}
    for name, sql_type in BASE_COLUMNS.items():
        if name not in existing:
            conn.execute(f'ALTER TABLE activities ADD COLUMN "{name}" {sql_type}')
    conn.execute('''CREATE TABLE IF NOT EXISTS activity_sources (
        source TEXT NOT NULL, source_activity_id TEXT NOT NULL,
        activity_id INTEGER NOT NULL REFERENCES activities(id),
        PRIMARY KEY(source,source_activity_id))''')

    conn.execute('''CREATE TABLE IF NOT EXISTS activity_feedback_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, activity_id INTEGER NOT NULL,
        source TEXT NOT NULL, source_activity_id TEXT NOT NULL,
        activity_date TEXT, recorded_at TEXT NOT NULL, text TEXT NOT NULL)''')
    conn.execute('CREATE INDEX IF NOT EXISTS feedback_history_activity ON activity_feedback_history(activity_id,source,source_activity_id,id)')


def _record_description(conn, activity_id, source, source_id, activity_date, text):
    if text is None:
        return
    if not isinstance(text,str):
        raise ValueError('Activity description must be text or unknown')
    previous = conn.execute('''SELECT text FROM activity_feedback_history
        WHERE activity_id=? AND source=? AND source_activity_id=? ORDER BY id DESC LIMIT 1''',
        (activity_id,source,str(source_id))).fetchone()
    if previous is None or previous[0] != text:
        conn.execute('''INSERT INTO activity_feedback_history
            (activity_id,source,source_activity_id,activity_date,recorded_at,text)
            VALUES (?,?,?,?,?,?)''',(activity_id,source,str(source_id),str(activity_date or '')[:10],
                                    datetime.now().astimezone().isoformat(),text))


def get_existing_source_ids(db_path, source):
    if not os.path.exists(db_path):
        return set()
    with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='activities'").fetchone():
            return set()
        cols = {r[1] for r in conn.execute('PRAGMA table_info(activities)')}
        if 'source' not in cols:
            return set()
        ids = {int(r[0]) for r in conn.execute('SELECT COALESCE(source_activity_id,id) FROM activities WHERE source=?',(source,))}
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='activity_sources'").fetchone():
            ids.update(int(r[0]) for r in conn.execute('SELECT source_activity_id FROM activity_sources WHERE source=?',(source,)))
        return ids


def _internal_id(conn, source, source_id, metrics):
    alias = conn.execute('SELECT activity_id FROM activity_sources WHERE source=? AND source_activity_id=?',(source,source_id)).fetchone()
    if alias:
        return alias[0]
    candidate = conn.execute('SELECT * FROM activities WHERE id=?',(int(source_id),)).fetchone()
    if candidate and candidate['source']==source and str(candidate['source_activity_id'] or candidate['id'])==source_id:
        return candidate['id']
    # Provider identities are independent. Numerical similarity is never proof
    # that legacy data is an official Personal API observation.
    if not candidate:
        return int(source_id)
    internal = -int.from_bytes(hashlib.sha256(f'{source}:{source_id}'.encode()).digest()[:7],'big')
    while conn.execute('SELECT 1 FROM activities WHERE id=?',(internal,)).fetchone():
        internal -= 1
    return internal


def _downsample(streams):
    buckets = {}
    for i,t in enumerate(streams.get('time',[])):
        t = _number(t)
        if t is None:
            continue
        bucket = buckets.setdefault(int(t//30),{})
        for key,values in streams.items():
            value = _number(values[i]) if i<len(values) else None
            if value is not None:
                bucket.setdefault(key,[]).append(value)
    return [{key:round(mean(values),1) for key,values in bucket.items()} for _,bucket in sorted(buckets.items())]


def ingest_payload(conn, payload):
    metrics = payload.get('activity_metrics',{})
    source, source_id = payload.get('source','legacy_unknown'),str(metrics.get('id',''))
    if not source_id.isdigit() or int(source_id)<=0:
        raise ValueError('Missing positive provider activity ID')
    internal = _internal_id(conn,source,source_id,metrics)
    old = conn.execute('SELECT * FROM activities WHERE id=?',(internal,)).fetchone()
    if old and old['description'] is not None and not conn.execute(
            'SELECT 1 FROM activity_feedback_history WHERE activity_id=? AND text=? LIMIT 1',
            (internal,old['description'])).fetchone():
        _record_description(conn,internal,old['metrics_source'] or old['source'] or 'legacy_unknown',
                            old['source_activity_id'] or old['id'],old['start_date'],old['description'])
    _record_description(conn,internal,source,source_id,metrics.get('start_date') or (old['start_date'] if old else None),
                        metrics.get('description'))
    values = {k:v for k,v in metrics.items() if k in BASE_COLUMNS and k not in
              {'ai_feedback','notes','source','source_activity_id','drift_validity'}}
    values['normalized_type'] = normalize_sport(metrics.get('type'))
    values.update(source=old['source'] if old and old['source'] else source,
                  source_activity_id=old['source_activity_id'] if old and old['source_activity_id'] else source_id)
    values['metrics_source'] = source
    values['imported_at'] = datetime.now().astimezone().isoformat()
    streams,zones = payload.get('time_series_streams',{}),payload.get('athlete_zones',{})
    usable_streams = (bool(streams.get('time')) and len(streams.get('heartrate',[]))==len(streams['time'])
                      and any(_number(h) is not None and 30<=float(h)<=240 for h in streams.get('heartrate',[])))
    if not payload.get('metadata_only') and (not old or usable_streams):
        values['stream_source'] = source
        values.update(calculate_stream_features(streams,zones,metrics.get('type')))
        for n in range(1,6):
            values.setdefault(f'hr_zone_{n}_pct',None)
        values['hr_zones_json'] = json.dumps(zones,allow_nan=False)
        values['downsampled_streams_json'] = json.dumps(_downsample(streams),allow_nan=False)
    if 'splits_metric' in metrics and (metrics['splits_metric'] or not payload.get('metadata_only')):
        values['raw_splits_json'] = json.dumps(metrics['splits_metric'],allow_nan=False)
    if old:
        values = {k:v for k,v in values.items() if v is not None or k.startswith('hr_zone_') or k=='cardiac_drift_pct'}
        conn.execute('UPDATE activities SET '+','.join(f'"{k}"=?' for k in values)+' WHERE id=?',[*values.values(),internal])
    else:
        conn.execute('INSERT INTO activities (id,'+','.join(f'"{k}"' for k in values)+') VALUES ('+','.join('?' for _ in range(len(values)+1))+')',[internal,*values.values()])
    conn.execute('INSERT OR IGNORE INTO activity_sources VALUES (?,?,?)',(source,source_id,internal))
    return internal


def build_master_database(db_path=None, data_dir=None):
    db_path,data_dir = db_path or DB_PATH,data_dir or DATA_DIR
    from source_policy import approved_activity_source,assert_coaching_sources
    if Path(db_path).exists():
        with closing(sqlite3.connect(Path(db_path).resolve().as_uri()+'?mode=ro',uri=True)) as existing:
            assert_coaching_sources(existing)
    os.makedirs(data_dir,exist_ok=True)
    processed = []
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        ensure_schema(conn)
        for path in sorted(Path(data_dir).glob('*_ai_payload.json')):
            with path.open(encoding='utf-8') as handle:
                payload = json.load(handle)
            if not approved_activity_source(payload.get('source')):
                raise ValueError('Legacy/unidentified pending payloads must be archived outside the fresh workspace; they were not imported or relabeled.')
            ingest_payload(conn,payload)
            processed.append(path)
    # Never discard inputs until the entire transaction has committed.
    for path in processed:
        path.unlink()
    print(f'Ingested/refreshed {len(processed)} activity payload(s).')


if __name__ == '__main__':
    build_master_database()
