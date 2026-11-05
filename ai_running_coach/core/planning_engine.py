"""Offline coaching policy, evidence and canonical plan validation (stdlib only)."""
import copy
import hashlib
import json
import math
import re
from datetime import date, datetime, timedelta
from source_policy import normalize_sport

POLICY_VERSION = 3


def is_run(value):
    return normalize_sport(value)=='run'


def parse_profile(text):
    zones = {}
    for number, label, bounds in re.findall(r'\*\*Zone\s+(\d)\s*\(([^)]+)\):\*\*\s*([^\n]+)', text, re.I):
        values = [int(n) for n in re.findall(r'\d+', bounds)]
        if not values:
            continue
        zones[number] = {'label': label, 'min': None if '<' in bounds else values[0],
                         'max': None if '>=' in bounds or '\u2265' in bounds else values[-1], 'display': bounds.strip()}
    explicitly_unknown=bool(re.search(r'Heart Rate Zones[^\n]*(?:unknown|not configured|not supplied)',text,re.I))
    if (not zones or '2' not in zones) and not explicitly_unknown:
        raise ValueError('Profile must specify an explicit Zone 2 before migration/review.')
    for zone in zones.values():
        if zone['min'] is not None and zone['max'] is not None and zone['min'] > zone['max']:
            raise ValueError('Invalid profile zone boundaries.')
    injury = re.search(r'\*\*Active Injuries:\*\*\s*([^\n]+)', text, re.I)
    goals = []
    current = None
    for line in text.splitlines():
        race = re.search(r'\*\*([ABC]-Race):\*\*\s*(.+)', line, re.I)
        if race:
            current = {'priority': race[1], 'event': race[2].strip()}
            goals.append(current)
        elif current:
            field = re.search(r'\*\*(Date|Target Time):\*\*\s*(.+)', line)
            if field:
                current[field[1].lower().replace(' ', '_')] = field[2].strip()
    preferences = {}
    for label in ('Weekly Availability', 'Preferred Rest Days', 'Preferred Long Run Day'):
        match = re.search(r'\*\*' + label + r':\*\*\s*([^\n]+)', text)
        if match:
            preferences[label] = match[1].strip()
    facts = {}
    for label in ('Age','Weight','Resting Heart Rate','Max Heart Rate (MHR)','Lactate Threshold','Sporting Background','Last 12 Months','2 years ago','Coach Name','Athlete Name','Coaching Style'):
        match = re.search(r'\*\*' + re.escape(label) + r':\*\*\s*([^\n]+)',text)
        if match: facts[label] = match[1].strip()
    tone = text.split('**Tone Rules:**',1)[-1].strip() if '**Tone Rules:**' in text else ''
    return {'schema_version': 1, 'zones': zones, 'zone_method': 'not configured' if not zones else 'profile-configured; not independently validated',
            'goals': goals, 'active_injuries': injury[1].strip() if injury else 'unknown',
            'preferences': preferences, 'athlete_facts': facts, 'communication_preferences': tone[:2000], 'source_hash': hashlib.sha256(text.encode()).hexdigest(),
            'uncertainties': ['Zone boundaries are configured estimates, not medical validation.',
                              'Sporting background does not establish muscle-fiber composition.']}


def configured_zone_conflicts(text,profile):
    pattern=r'\b(?:Zone\s*|Z\s*)([1-5])\b(?:\s*(?:heart\s*rate|HR|bpm|target|range|is|of|approximately|about|[():=@])){0,8}\s*(\d{2,3})\s*(?:[-\u2013\u2014]|to)\s*(\d{2,3})'
    errors=[]
    for number,low,high in re.findall(pattern,text,re.I):
        configured=profile['zones'].get(number)
        # A coach may prescribe a narrower session target inside a configured
        # zone. This validates membership, not mandatory use of its full range.
        lower=int(low);upper=int(high)
        incompatible=(not configured or lower>upper or
                      (configured['min'] is not None and lower<configured['min']) or
                      (configured['max'] is not None and upper>configured['max']))
        if incompatible:
            errors.append('Zone '+number+' range differs from configured profile.')
    return errors


def plan_conflicts(markdown, profile):
    conflicts = []
    if configured_zone_conflicts(markdown,profile):
        conflicts.append('Plan zone definitions differ from configured profile.')
    if re.search(r'all.*power walks|non-negotiable', markdown, re.I):
        conflicts.append('Plan contains blanket walking restrictions or punitive policy.')
    if profile['active_injuries'].lower() == 'none' and re.search(r'active injur(?:y|ies)\s*[:=]\s*(?!none\b|unknown\b)\w+', markdown, re.I):
        conflicts.append('Explicit active-injury assertion conflicts with profile.')
    return sorted(set(conflicts))


def parse_plan(markdown, profile, today=None):
    """Structural migration only: the coach resolves superseded legacy targets."""
    sessions = []
    source_rows = []
    seen = set()
    for line in markdown.splitlines():
        if not line.lstrip().startswith('|'):
            continue
        cells = [c.strip() for c in line.strip().strip('|').split('|')]
        if len(cells) != 5 or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', cells[1]):
            continue
        day = date.fromisoformat(cells[1])
        source_rows.append({'date':cells[1], 'workout_type':cells[2], 'amount':cells[3], 'target':cells[4]})
        if cells[1] in seen:
            raise ValueError('Duplicate scheduled date; migrate manually before continuing.')
        seen.add(cells[1])
        kind = cells[2].replace('**', '')
        target = cells[4]
        if plan_conflicts(target,profile):
            target = 'Legacy target superseded by current profile; awaiting AI coach reconciliation.'
        sessions.append({'id': 'session-' + cells[1], 'date': cells[1], 'workout_type': kind,
                         'amount': cells[3], 'target': target})
    plan = {'source_schedule': source_rows, 'goals': profile['goals'], 'schema_version': 1, 'revision': 1, 'profile_hash': profile['source_hash'],
            'requires_coach_reconciliation': bool(plan_conflicts(markdown,profile)),
            'migration_notes': ['Structural import of legacy schedule; Python has prescribed no replacement workouts.',
                                'Conflicting legacy restrictions are superseded. The AI coach will reconcile upcoming sessions.'],
            'sessions': sessions}
    validate_plan(plan, profile)
    return plan


def validate_plan(plan, profile):
    if plan.get('schema_version') != 1 or not isinstance(plan.get('revision'), int):
        raise ValueError('Unsupported plan version.')
    if plan.get('profile_hash') != profile['source_hash']:
        raise ValueError('Profile changed: reconcile canonical plan before review.')
    if plan.get('goals') != profile['goals']:
        raise ValueError('Canonical goals differ from configured profile.')
    sessions = plan.get('sessions')
    if not isinstance(sessions, list) or (not sessions and not plan.get('requires_coach_reconciliation')):
        raise ValueError('No validated sessions.')
    ids = set()
    for session in sessions:
        if not {'id', 'date', 'workout_type', 'amount', 'target'}.issubset(session) or set(session)-{'id', 'date', 'workout_type', 'amount', 'target','goal_priority','workout'}:
            raise ValueError('Invalid session fields.')
        date.fromisoformat(session['date'])
        if session['id'] in ids:
            raise ValueError('Duplicate session ID.')
        ids.add(session['id'])
        text_fields=set(session)-{'workout'}
        if not all(isinstance(session[k], str) and 0 < len(session[k]) < 2000 for k in text_fields):
            raise ValueError('Invalid session text.')
        if any(v in session[k] for k in text_fields for v in ('\n','\r','|')):
            raise ValueError('Session text cannot break the rendered table.')
        if re.search(r'(?<![\w\d])[-\u2212]\s*\d+(?:\.\d+)?\s*(?:km|miles?|mi|m|min|minutes?|s|seconds?|hours?|h)\b',session['amount'],re.I):
            raise ValueError('Negative session quantity.')
        if 'workout' in session:
            from workout_schema import validate_workout
            validate_workout(session['workout'])
    return plan


def render_plan(plan):
    banner = 'Legacy schedule awaiting AI coach reconciliation; imported amounts are historical planning data.' if plan.get('requires_coach_reconciliation') else 'Plan authored and adapted by the AI coach.'
    lines = ['# Training plan', '', '> ' + banner, '']
    for goal in plan.get('goals', []):
        lines += [f"- {goal['priority']}: {goal['event']} | {goal.get('date','date unknown')} | {goal.get('target_time','target unknown')}"]
    lines += ['']
    week = None
    for s in sorted(plan['sessions'], key=lambda x: x['date']):
        d = date.fromisoformat(s['date']); monday = d - timedelta(days=d.weekday())
        if monday != week:
            week = monday
            lines += [f'### Week (w/c {week.isoformat()})', '', '| Day | Date | Workout Type | Distance / Duration | Target |', '|---|---|---|---|---|']
        lines.append('| ' + ' | '.join([d.strftime('%A'), s['date'], s['workout_type'], s['amount'], s['target']]) + ' |')
    return '\n'.join(lines) + '\n'


def parse_structured_notes(text):
    """Read explicitly labelled athlete reports, never infer measurements from prose."""
    from coach_checkin import validate_feedback_fields
    results=[]
    blocks=re.findall(r'\[coach:\s*(.*?)\]|\[coach\](.*?)\[/coach\]',str(text or ''),re.I|re.S)
    for alternatives in blocks:
        block=next((part for part in alternatives if part),'')
        fields,warnings={},[]
        for piece in re.split(r';|\n',block):
            if not piece.strip():continue
            if '=' not in piece:
                warnings.append('Ignored field without key=value.');continue
            key,value=piece.split('=',1);key=key.strip().lower().replace('-','_');value=value.strip()
            try:fields.update(validate_feedback_fields({key:value}))
            except ValueError as exc:warnings.append(str(exc)[:150])
        results.append({'fields':fields,'warnings':warnings})
    return results


def collect_feedback(conn, rows, today):
    """Dated history with explicit provenance, recency and topic supersession."""
    records=[]
    def add(reported,activity_id,source,comment,fields=None,warnings=None):
        try:reported_date=date.fromisoformat(str(reported)[:10])
        except (ValueError,TypeError):return
        age=(today-reported_date).days
        if not 0<=age<=55:return
        record={'reported_on':reported_date.isoformat(),'activity_id':activity_id,'source':str(source),'fields':fields or {},'comment':str(comment or '')[:500], 'age_days':age,
                'current_report':age<=6,'interpretation':'Dated athlete observation; not a permanent injury or nutrition assessment.'}
        expiry=record['fields'].get('expires')
        if expiry:
            try:record['current_report']=record['current_report'] and today<=date.fromisoformat(expiry)
            except ValueError:record['current_report']=False
        if warnings:record['parse_warnings']=warnings
        records.append(record)
    tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'activity_feedback_history' in tables:
        for activity_id,source,reported,text in conn.execute('SELECT activity_id,source,activity_date,text FROM activity_feedback_history WHERE date(activity_date) BETWEEN ? AND ? ORDER BY recorded_at,id',((today-timedelta(days=55)).isoformat(),today.isoformat())):
            blocks=parse_structured_notes(text)
            if blocks:
                for block in blocks:add(reported,activity_id,str(source)+':description_history',text,block['fields'],block['warnings'])
            else:add(reported,activity_id,str(source)+':description_history',text)
    for row in rows:
        for location,text in row.get('_note_sources',[]):
            if not str(text or '').strip():continue
            source=str(row.get('metrics_source') or row.get('source') or 'legacy_unknown')+':'+location
            blocks=parse_structured_notes(text)
            if blocks:
                for block in blocks:add(row['start_date'],row['activity_id'],source,text,block['fields'],block['warnings'])
            else:add(row['start_date'],row['activity_id'],source,text)
    if 'athlete_checkins' in tables:
        for reported,activity_id,source,feedback_json,comment in conn.execute('SELECT reported_on,activity_id,source,feedback_json,comment FROM athlete_checkins WHERE reported_on BETWEEN ? AND ? ORDER BY reported_on,created_at,id',((today-timedelta(days=55)).isoformat(),today.isoformat())):
            try:fields=json.loads(feedback_json)
            except (TypeError,ValueError):fields={}
            add(reported,activity_id,source,comment,fields if isinstance(fields,dict) else {})
    records.sort(key=lambda r:r['reported_on'])
    latest={}
    for index,record in enumerate(records):
        topic=record['fields'].get('topic')
        if topic:
            topic=str(topic).casefold()
            if topic in latest:
                previous=records[latest[topic]];previous['current_report']=False
                previous['superseded_by']={'reported_on':record['reported_on'],'source':record['source'],'status':record['fields'].get('status','unspecified')}
            latest[topic]=index
    # Deduplicate repeated refresh descriptions without dropping distinct local reports.
    unique={}
    for record in records:
        identity=json.dumps([record['reported_on'],record['activity_id'],record['fields'],record['comment']],sort_keys=True)
        if identity in unique:
            record['sources']=sorted(set(unique[identity].get('sources',[unique[identity]['source']])+[record['source']]))
        unique[identity]=record
    records=list(unique.values())[-24:]
    return records


def build_evidence(conn, today):
    from coach_checkin import FEEDBACK_FIELD_UNITS
    today = date.fromisoformat(today) if isinstance(today, str) else today
    cols = {row[1] for row in conn.execute('PRAGMA table_info(activities)')}
    wanted = ['id','type','start_date','distance_m','moving_time_s','timer_duration_s','elapsed_time_s','avg_heartrate','elevation_gain_m','perceived_exertion','description','notes','source','metrics_source','cardiac_drift_pct','drift_validity','training_load','provider_metrics_json','hr_zone_seconds_json','hr_zones_json']
    fields = [c for c in wanted if c in cols]
    start = (today - timedelta(days=55)).isoformat()
    cursor = conn.execute('SELECT ' + ','.join(fields) + ' FROM activities WHERE date(start_date)>=? AND date(start_date)<=? ORDER BY start_date,id', (start, today.isoformat()))
    rows = [dict(zip(fields, row)) for row in cursor]
    for row in rows:
        for k, v in list(row.items()):
            if isinstance(v, float) and not math.isfinite(v): row[k] = None
        row['activity_id'] = row.pop('id')
        for stored,shown in (('provider_metrics_json','provider_metrics'),('hr_zone_seconds_json','reported_zone_seconds'),('hr_zones_json','recorded_zone_configuration')):
            raw=row.pop(stored,None)
            if raw:
                try:row[shown]=json.loads(raw)
                except (ValueError,TypeError):row[shown]=None
        row['is_running'] = is_run(row.get('type'))
        duration, distance = row.get('moving_time_s'), row.get('distance_m')
        row['pace_min_km'] = round(duration / 60 / (distance/1000), 2) if row['is_running'] and duration and distance and distance > 0 else None
        row['_note_sources']=[('description',row.pop('description','')),('local_notes',row.get('notes'))]
        row['notes'] = '\n'.join(dict.fromkeys(str(v).strip() for _,v in row['_note_sources'] if v and str(v).strip()))[:1000]
        if row['notes']:row['reported_feedback']=parse_structured_notes(row['notes'])
        if row.get('drift_validity') != 'valid':
            row['cardiac_drift_pct'] = None
        row['drift_interpretation'] = 'Descriptive only; no fitness or sensor-error cause established.'
    running = [r for r in rows if r['is_running'] and str(r['start_date'])[:10] >= (today-timedelta(days=27)).isoformat()]
    km = sum((r.get('distance_m') or 0) / 1000 for r in running)
    recent = [r for r in rows if str(r['start_date'])[:10] >= (today-timedelta(days=6)).isoformat()]
    weekly = []
    for offset in range(7,-1,-1):
        end = today-timedelta(days=offset*7); begin=end-timedelta(days=6)
        block=[r for r in rows if r['is_running'] and begin.isoformat() <= str(r['start_date'])[:10] <= end.isoformat()]
        ascents=[r['elevation_gain_m'] for r in block if isinstance(r.get('elevation_gain_m'),(int,float)) and r['elevation_gain_m']>=0]
        durations=[r['moving_time_s'] for r in block if isinstance(r.get('moving_time_s'),(int,float)) and r['moving_time_s']>=0]
        timers=[r['timer_duration_s'] for r in block if isinstance(r.get('timer_duration_s'),(int,float)) and r['timer_duration_s']>=0]
        weekly.append({'ending':end.isoformat(),'run_count':len(block),'km':round(sum((r.get('distance_m') or 0)/1000 for r in block),2),'minutes':round(sum(durations)/60,1) if durations else None,'longest_km':round(max([(r.get('distance_m') or 0)/1000 for r in block] or [0]),2),'ascent_m':round(sum(ascents),1) if ascents else None,'ascent_coverage':len(ascents),'duration_coverage':len(durations),'longest_duration_min':round(max(durations)/60,1) if durations else None})
        weekly[-1].update(timer_minutes=round(sum(timers)/60,1) if timers else None,timer_duration_coverage=len(timers),longest_timer_duration_min=round(max(timers)/60,1) if timers else None)
    feedback=collect_feedback(conn,rows,today)
    for row in rows:row.pop('_note_sources',None)
    return {'today': today.isoformat(), 'window_start': start, 'feedback_units':FEEDBACK_FIELD_UNITS, 'feedback_history':feedback,'current_athlete_reports':[r for r in feedback if r['current_report']], 'running_weeks_8':weekly, 'running_28d': {'count': len(running), 'total_km': round(km,2), 'avg_weekly_km': round(km/4,2), 'longest_km': round(max([(r.get('distance_m') or 0)/1000 for r in running] or [0]),2)},
            'recent_7d': recent, 'limitations': ['Missing subjective effort is unknown, not low fatigue.', 'Average HR does not measure time in zones.', 'Race readiness requires more evidence than plan compliance.']}


def fingerprint(profile, plan, evidence, question, generation_identity=None):
    stable_evidence = copy.deepcopy({k:v for k,v in evidence.items() if k not in {'today','window_start'}})
    for week in stable_evidence.get('running_weeks_8',[]): week.pop('ending',None)
    for key in ('feedback_history','current_athlete_reports'):
        for report in stable_evidence.get(key,[]):report.pop('age_days',None)
    today=date.fromisoformat(evidence['today'])
    stable_evidence['weekly_review_due']=(today-timedelta(days=today.weekday())).isoformat()
    stable_evidence['elapsed_scheduled_sessions']=[s['id'] for s in plan['sessions'] if s['date']<today.isoformat() and 'rest' not in s['workout_type'].lower()]
    parts=[POLICY_VERSION,profile,plan,stable_evidence,question]
    if generation_identity is not None:parts.append(generation_identity)
    return hashlib.sha256(json.dumps(parts, sort_keys=True, allow_nan=False).encode()).hexdigest()


def race_calendar_conflicts(plan,profile,today):
    """Check event dates against athlete configuration, without choosing training."""
    goals=[]
    for goal in profile.get('goals',[]):
        for fmt in ('%Y-%m-%d','%B %d, %Y'):
            try:day=datetime.strptime(goal.get('date',''),fmt).date().isoformat();break
            except ValueError:day=None
        if day:
            words=set(re.findall(r'[a-z]{3,}',goal.get('event','').lower()))-{'the','ascent','miles','mile','race','trail','trails'}
            goals.append((goal,day,words))
    errors=[]
    for session in plan.get('sessions',[]):
        if session['date']<str(today):continue
        kind=session['workout_type']
        explicit=bool(session.get('goal_priority'))
        event=bool(re.search(r'\brace\b',kind,re.I)) and not re.search(r'race[ -]*(?:pace|effort|simulation|specific|prep)',kind,re.I)
        if not explicit and not event:continue
        text=' '.join(str(session.get(k,'')) for k in ('workout_type','amount','target')).lower()
        matches=[(goal,day) for goal,day,words in goals if (explicit and goal.get('priority')==session['goal_priority']) or (not explicit and words and all(word in text for word in words))]
        if not matches and not explicit:
            matches=[(goal,day) for goal,day,_ in goals if day==session['date']]
        if len(matches)!=1:
            errors.append(session['id']+': race event needs an unambiguous configured goal_priority.')
        elif session['date']!=matches[0][1]:
            errors.append(session['id']+': event date must match '+matches[0][0]['priority']+' '+matches[0][1]+'.')
    return errors


def apply_plan_changes(plan, changes, profile, today):
    if not isinstance(changes,list) or len(changes)>60:
        raise ValueError('Invalid plan change count.')
    result=copy.deepcopy(plan)
    sessions={s['id']:s for s in result['sessions']}
    retired=set(result.get('retired_session_ids',[]))
    seen=set()
    for change in changes:
        if not isinstance(change,dict) or set(change)!={'op','session_id','session','reason'}:
            raise ValueError('Invalid change fields.')
        op,sid=change['op'],change['session_id']
        if op not in {'add','update','delete'} or not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',sid) or sid in seen:
            raise ValueError('Invalid or duplicate operation/ID.')
        seen.add(sid)
        if not isinstance(change['reason'],str) or not 1<=len(change['reason'])<=500:
            raise ValueError('Each change needs a concise coaching reason.')
        if op in {'update','delete'}:
            if sid not in sessions or sessions[sid]['date']<str(today):
                raise ValueError('Cannot change unknown or historical sessions.')
        elif sid in sessions or sid in retired:
            raise ValueError('Added session ID already exists or was retired.')
        if op=='delete':
            if change['session'] is not None: raise ValueError('Delete must have null session.')
            del sessions[sid]; retired.add(sid)
        else:
            session=change['session']
            if not isinstance(session,dict) or not {'id','date','workout_type','amount','target'}.issubset(session) or set(session)-{'id','date','workout_type','amount','target','goal_priority','workout'} or session['id']!=sid:
                raise ValueError('Change needs full session with matching stable ID.')
            d=date.fromisoformat(session['date'])
            if not str(today)<=d.isoformat() <= (date.fromisoformat(str(today))+timedelta(days=366)).isoformat():
                raise ValueError('Changed session outside supported future window.')
            # Enforce configured zone definitions, without deciding the training intensity.
            if configured_zone_conflicts(session['target'],profile):
                raise ValueError('Coach may choose zones but cannot redefine profile zone boundaries.')
            sessions[sid]=copy.deepcopy(session)
    result['sessions']=sorted(sessions.values(),key=lambda s:(s['date'],s['id']))
    if retired or 'retired_session_ids' in result:
        result['retired_session_ids']=sorted(retired)
    unresolved=[s['id'] for s in result['sessions'] if s['date']>=str(today) and 'Legacy target superseded' in s['target']]
    if plan.get('requires_coach_reconciliation') and unresolved:
        raise ValueError('Coach must replace or remove superseded upcoming targets: '+','.join(unresolved))
    if changes or plan.get('requires_coach_reconciliation'):
        result['revision']+=1
        result['requires_coach_reconciliation']=False
        result['authored_by']='AI coach'
    if not any(s['date']>=str(today) for s in result['sessions']):
        raise ValueError('Coach must provide an upcoming schedule when the current plan expires.')
    validate_plan(result,profile)
    calendar_errors=race_calendar_conflicts(result,profile,today)
    if calendar_errors:raise ValueError('Race calendar conflict: '+'; '.join(calendar_errors))
    return result


def apply_new_plan(plan,changes,profile,today):
    """Replace only future scheduling after a valid AI-authored block exists."""
    if not isinstance(changes,list) or not changes or any(change.get('op')!='add' for change in changes if isinstance(change,dict)):
        raise ValueError('A new plan requires added sessions with new unique IDs.')
    prepared=copy.deepcopy(plan)
    future=[session for session in prepared['sessions'] if session['date']>=str(today)]
    prepared['sessions']=[session for session in prepared['sessions'] if session['date']<str(today)]
    prepared['retired_session_ids']=sorted(set(prepared.get('retired_session_ids',[]))|{session['id'] for session in future})
    if future:
        prepared.setdefault('superseded_blocks',[]).append({'revision':plan['revision'],'superseded_from':str(today),'sessions':future})
    prepared['requires_coach_reconciliation']=True
    prepared.pop('new_plan_requested',None)
    result=apply_plan_changes(prepared,changes,profile,today)
    result['active_block_started_on']=str(today)
    return result


def apply_coach_changes(plan,changes,profile,today):
    if plan.get('new_plan_requested'):
        return apply_new_plan(plan,changes,profile,today)
    return apply_plan_changes(plan,changes,profile,today)


def validate_review(payload, evidence, plan, today, profile=None):
    required={'summary','trajectory','reviews','plan_changes'}
    optional={'weekly_priorities','assessment','next_review_trigger'}
    if not isinstance(payload,dict) or not required.issubset(payload) or set(payload)-required-optional:
        raise ValueError('Invalid review envelope.')
    if payload['trajectory'] not in {'insufficient_evidence','on_track','ahead','behind'}:
        raise ValueError('Invalid trajectory.')
    if not isinstance(payload['summary'],str) or not 1<=len(payload['summary'])<=2000 or '\n' in payload['summary']:
        raise ValueError('Summary must be one short paragraph.')
    activities={r['activity_id'] for r in evidence['recent_7d']}
    seen=set()
    if not isinstance(payload['reviews'],list) or len(payload['reviews'])>len(activities):
        raise ValueError('Invalid review list.')
    for review in payload['reviews']:
        if not isinstance(review,dict) or set(review)!={'activity_id','feedback'} or type(review['activity_id']) is not int or review['activity_id'] not in activities or review['activity_id'] in seen:
            raise ValueError('Unknown/duplicate activity ID.')
        if not isinstance(review['feedback'],str) or not 1<=len(review['feedback'])<=500:
            raise ValueError('Invalid activity feedback.')
        seen.add(review['activity_id'])
    def text(value,maximum):
        return isinstance(value,str) and 0<len(value)<=maximum
    def texts(value,maximum_items=6):
        return isinstance(value,list) and len(value)<=maximum_items and all(text(item,400) for item in value)
    if 'weekly_priorities' in payload:
        priorities=payload['weekly_priorities']
        if not isinstance(priorities,list) or not 2<=len(priorities)<=3:
            raise ValueError('Provide two or three concise coaching priorities.')
        for item in priorities:
            if not isinstance(item,dict) or set(item)!={'priority','reason','evidence'} or not text(item['priority'],180) or not text(item['reason'],500) or not texts(item['evidence']) or not item['evidence']:
                raise ValueError('Each priority needs a reason and evidence references.')
    if 'assessment' in payload:
        assessment=payload['assessment']
        if not isinstance(assessment,dict) or set(assessment)!={'reason','evidence','uncertainties'} or not text(assessment['reason'],1000) or not texts(assessment['evidence'],8) or not texts(assessment['uncertainties'],8):
            raise ValueError('Invalid trajectory assessment explanation.')
    if 'next_review_trigger' in payload and not text(payload['next_review_trigger'],500):
        raise ValueError('Invalid next review trigger.')
    if profile is not None:
        apply_coach_changes(plan,payload['plan_changes'],profile,today)
    elif payload['plan_changes']:
        raise ValueError('Profile required to validate plan changes.')
    return payload
