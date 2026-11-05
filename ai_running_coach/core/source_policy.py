"""Prevent legacy or unidentified activity data silently reaching a new coach run."""
import os
import re


def normalize_sport(value):
    """Shared provider-kind classification; the original activity type stays intact."""
    key=re.sub(r'[^a-z]','',str(value or '').lower())
    aliases={
        'run':'run','running':'run','trailrun':'run','trailrunning':'run',
        'virtualrun':'run','treadmillrunning':'run','trackrun':'run','trackrunning':'run',
        'ultrarun':'run','ultrarunning':'run','walk':'walk','walking':'walk',
        'hike':'hike','hiking':'hike','swim':'swim','swimming':'swim',
        'openwaterswimming':'swim','lapswimming':'swim','ride':'ride','cycling':'ride',
        'virtualride':'ride','mountainbikeride':'ride','weighttraining':'strength',
        'strengthtraining':'strength'}
    return aliases.get(key,key or 'unknown')


def activity_source_mode():
    value=os.getenv('COACH_ACTIVITY_SOURCE','offline').strip().lower() or 'offline'
    if value not in {'garmin','offline'}:
        raise ValueError('COACH_ACTIVITY_SOURCE must be garmin (official Personal API) or offline. Legacy connectors were removed; initialize a fresh workspace.')
    return value


def approved_activity_source(value):
    return isinstance(value,str) and (value=='garmin_personal' or value.startswith('local:'))


def assert_coaching_sources(conn):
    """Fail closed, retaining the original database and provenance for archiving."""
    tables={row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    problem=False
    if 'activities' in tables:
        columns={row[1] for row in conn.execute('PRAGMA table_info(activities)')}
        count=conn.execute('SELECT COUNT(*) FROM activities').fetchone()[0]
        if count and 'source' not in columns:
            problem=True
        elif count:
            fields=['source']+[name for name in ('metrics_source','stream_source') if name in columns]
            for row in conn.execute('SELECT '+','.join(fields)+' FROM activities'):
                if not approved_activity_source(row[0]) or any(value is not None and not approved_activity_source(value) for value in row[1:]):problem=True;break
    for table in ('activity_sources','activity_feedback_history'):
        if table in tables:
            for row in conn.execute('SELECT DISTINCT source FROM '+table):
                if not approved_activity_source(row[0]):problem=True;break
    if 'athlete_checkins' in tables:
        for source,activity_id in conn.execute('SELECT source,activity_id FROM athlete_checkins'):
            if source not in {'local_cli','user_chat'}:problem=True;break
            if activity_id is not None and ('activities' not in tables or not conn.execute('SELECT 1 FROM activities WHERE id=?',(activity_id,)).fetchone()):problem=True;break
    if problem:
        raise ValueError('This database contains legacy or unidentified activity provenance and cannot be sent to the coach. Preserve it as an archive, then use setup_coach.py --fresh-garmin-workspace DEST for an isolated official Garmin dataset; do not relabel old records.')
