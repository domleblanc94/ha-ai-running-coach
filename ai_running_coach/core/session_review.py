"""Bounded plan/activity comparison. Suggestions never become athlete reports."""
import json
import math
import re
from datetime import date, timedelta
from sports import activity_sport, session_sport


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def intent(session):
    result = {key: session.get(key) for key in ('id', 'date', 'sport', 'workout_type', 'amount', 'target')}
    steps = (session.get('workout') or {}).get('steps', [])
    durations = [s.get('duration', {}) for s in steps]
    seconds = None
    if durations and all(d.get('type') == 'time' and number(d.get('seconds')) is not None for d in durations):
        seconds = sum(d['seconds'] for d in durations)
    if seconds is None:
        match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(min|minutes?|h|hours?)\s*', str(session.get('amount', '')), re.I)
        if match:
            seconds = float(match[1]) * (3600 if match[2].lower().startswith('h') else 60)
    result['planned_duration_s'] = seconds
    result['duration_basis'] = 'explicit timed steps or amount; distance plans have unknown duration'
    return result


def review(conn, plan, today, days=28, limit=50, compact=False):
    start = (date.fromisoformat(today) - timedelta(days=days-1)).isoformat()
    columns = {r[1] for r in conn.execute('PRAGMA table_info(activities)')}
    fields = [k for k in ('id', 'type', 'start_date', 'source', 'source_activity_id', 'moving_time_s', 'timer_duration_s', 'elapsed_time_s', 'distance_m', 'avg_heartrate', 'perceived_exertion') if k in columns]
    activities = [dict(zip(fields, r)) for r in conn.execute('SELECT '+','.join(fields)+' FROM activities WHERE date(start_date) BETWEEN ? AND ? ORDER BY start_date,id', (start, today))]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    saved, history = {}, {}
    sessions = [s for s in plan['sessions'] if start <= s['date'] <= today]
    if 'coach_session_links' in tables:
        for session in sessions[-limit:]:
            records = conn.execute('SELECT id,body_json,created_at FROM coach_session_links WHERE session_id=? ORDER BY id DESC LIMIT 6', (session['id'],)).fetchall()
            if records:
                saved[session['id']] = json.loads(records[0][1])
                history[session['id']] = [{'id':r[0], 'confirmation':json.loads(r[1]), 'created_at':r[2]} for r in records[:5]]
    reports = {}
    if 'athlete_checkins' in tables:
        from coach_checkin import validate_feedback_fields
        linked_ids = {r.get('activity_id') for r in saved.values() if r.get('activity_id') is not None}
        for aid in linked_ids:
            records = conn.execute('SELECT id,reported_on,source,feedback_json,comment FROM athlete_checkins WHERE activity_id=? AND reported_on<=? ORDER BY reported_on DESC,id DESC LIMIT 5', (aid,today)).fetchall()
            for cid, reported, source, raw, comment in records:
                try: fields = validate_feedback_fields(json.loads(raw))
                except (ValueError,TypeError): fields = {}
                reports.setdefault(aid, []).append({'id':cid, 'reported_on':reported, 'source':source,
                    'fields':fields, 'comment':str(comment or '')[:500], 'activity_id':aid,
                    'basis':'explicit dated athlete report; next-day dates remain distinct'})
    output = []
    for session in sessions[-limit:]:
        candidates = [a for a in activities if str(a['start_date'])[:10] == session['date'] and activity_sport(a.get('type')) == session_sport(session)]
        confirmation = saved.get(session['id'])
        linked = next((a for a in activities if confirmation and a['id'] == confirmation.get('activity_id')), None)
        actual = None
        if linked:
            actual = {k: (number(v) if k.endswith('_s') or k in {'distance_m','avg_heartrate','perceived_exertion'} else v) for k,v in linked.items()}
            planned = intent(session)['planned_duration_s']
            for basis in ('moving','timer','elapsed'):
                key = basis+'_time_s' if basis != 'timer' else 'timer_duration_s'
                actual[basis+'_difference_s'] = actual.get(key) - planned if planned is not None and actual.get(key) is not None else None
        output.append({'intent': intent(session), 'confirmation': confirmation, 'actual': actual,
            'status': confirmation['outcome'] if confirmation else 'unconfirmed',
            'match_source': 'athlete_confirmed' if confirmation else 'inferred_candidates_only',
            'confirmed_activity_outside_window_or_missing': bool(confirmation and confirmation.get('activity_id') is not None and linked is None),
            'match_confidence': 'confirmed' if confirmation else ('medium' if len(candidates)==1 else 'low' if candidates else 'none'),
            'candidate_activity_ids': [a['id'] for a in candidates[:10]], 'candidate_count': len(candidates),
            'confirmation_history': history.get(session['id'], []),
            'athlete_reports': reports.get(confirmation.get('activity_id'), []) if confirmation else [],
            'intent_changed_since_confirmation': bool(confirmation and confirmation.get('intent') != intent(session))})
    if compact:
        for item in output:
            item.pop('confirmation_history', None)
            item['athlete_reports'] = item['athlete_reports'][:2]
            if item['confirmation']:
                item['confirmation'] = {k:v for k,v in item['confirmation'].items() if k in {'outcome','activity_id','confirmed_on','source','comment'}}
    return {'from': start, 'through': today, 'sessions': output, 'total_sessions': len(sessions), 'truncated': len(sessions)>limit,
        'limitations': ['Date/sport candidates require athlete confirmation; absence of a record never means skipped.',
            'Shortened, swapped and skipped outcomes are explicit athlete statements, never inferred.',
            'Effort comparison uses planned target, measured HR and explicitly reported RPE; none establishes equivalent effort.',
            'Reports are optional; use dated linked check-ins for RPE, recovery, fueling and stomach tolerance.']}


def weekly_summary(conn, today, weeks=8):
    end = date.fromisoformat(today)
    current_monday = end-timedelta(days=end.weekday())
    start = (current_monday-timedelta(weeks=weeks-1)).isoformat()
    columns = {r[1] for r in conn.execute('PRAGMA table_info(activities)')}
    fields = [k for k in ('id','type','start_date','source','distance_m','moving_time_s','timer_duration_s','elapsed_time_s','elevation_gain_m') if k in columns]
    rows = [dict(zip(fields,r)) for r in conn.execute('SELECT '+','.join(fields)+' FROM activities WHERE date(start_date) BETWEEN ? AND ? ORDER BY start_date,id',(start,today))]
    result=[]
    def aggregate(items):
        out={'activity_count':len(items), 'sources':sorted({a['source'] for a in items if a.get('source')})}
        for metric in ('distance_m','moving_time_s','timer_duration_s','elapsed_time_s','elevation_gain_m'):
            values=[number(a.get(metric)) for a in items if number(a.get(metric)) is not None]
            out[metric]=round(sum(values),2) if values else None
            out[metric+'_coverage']=len(values)
            if metric != 'elevation_gain_m':out['longest_'+metric]=max(values) if values else None
        if any(activity_sport(a.get('type')) == 'running' for a in items):
            out['longest_run_distance_m'] = out['longest_distance_m']
            out['longest_run_moving_time_s'] = out['longest_moving_time_s']
            out['longest_run_timer_duration_s'] = out['longest_timer_duration_s']
        return out
    for offset in range(weeks-1,-1,-1):
        begin=current_monday-timedelta(weeks=offset);finish=min(begin+timedelta(days=6),end)
        block=[a for a in rows if begin.isoformat()<=str(a['start_date'])[:10]<=finish.isoformat()]
        sports=sorted({activity_sport(a.get('type')) for a in block})
        result.append({'from':begin.isoformat(),'through':finish.isoformat(), 'calendar_week_ending':(begin+timedelta(days=6)).isoformat(), 'partial_week':finish<begin+timedelta(days=6), 'as_of':today, 'by_sport':{sport:aggregate([a for a in block if activity_sport(a.get('type'))==sport]) for sport in sports}})
    return {'weeks': result, 'units':{'distance_m':'metres','elevation_gain_m':'metres','moving_time_s':'seconds','timer_duration_s':'seconds; may include rests','elapsed_time_s':'seconds'},
        'limitations':['Totals contain only recorded measures; coverage below activity_count means incomplete totals.',
            'Null means unknown, including longest run when no distance or duration was recorded.',
            'No recorded activity does not establish a missed or skipped planned session.',
            'Sources label measured activity provenance; detailed streams remain available only on demand.']}
