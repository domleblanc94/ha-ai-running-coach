"""Pure calendar constraints shared by the coach and its presentation apps."""

from datetime import date, datetime
import re


WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')


def calendar_profile(text):
    """Read only the profile's goals and calendar preferences, without I/O."""
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
    for label in ('Weekly Availability', 'Preferred Rest Days', 'Preferred Long Run Day', 'Preferred Long Run Days'):
        match = re.search(r'\*\*' + label + r':\*\*[ \t]*([^\n]*)', text, re.I)
        if match:
            preferences[label] = match[1].strip()
    return {'goals': goals, 'preferences': preferences}


def _weekday_list(value, alternatives=False):
    if not isinstance(value, str) or not value.strip():
        return None
    if alternatives:
        value = re.sub(r'\bor\b', ',', value, flags=re.I)
    parts = re.split(r'\s*(?:,|;|\band\b|&)\s*', value.strip(), flags=re.I)
    names = {day.lower(): day for day in WEEKDAYS}
    if not parts or any(part.lower() not in names for part in parts):
        # Legacy entries such as "3 days" do not select particular weekdays.
        return None
    selected = {names[part.lower()] for part in parts}
    return [day for day in WEEKDAYS if day in selected]


def training_days(profile):
    """Return an explicit weekday list; missing or free-text availability is unknown."""
    return _weekday_list(profile.get('preferences', {}).get('Weekly Availability', ''))


def preferred_long_run_days(profile):
    """Optional alternative slots, not a requirement for multiple long runs."""
    preferences = profile.get('preferences', {})
    return _weekday_list(preferences.get('Preferred Long Run Days', preferences.get('Preferred Long Run Day', '')), alternatives=True)


def planned_running_session(session):
    """Classify planned workout labels, separately from recorded activity types."""
    workout = session.get('workout')
    if isinstance(workout, dict) and workout.get('sport') == 'running':
        return True
    label = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(session.get('workout_type', '')))
    kind = re.sub(r'[^a-z]+', ' ', label.lower()).strip()
    nonrunning = bool(re.search(r'\b(?:strength|swim|swimming|cycling|cycle|bike|biking|ride|yoga|walk|walking|hike|hiking|rest|off|mobility|row|rowing|cross train|cross training)\b', kind))
    # "Post-run mobility" describes a separate mobility session. A combined
    # "Run + strength" still contains a run and occupies the day's running slot.
    qualifier = re.search(r'\b(?:(?:pre|post) ?run(?:ning)?|(?:before|after) (?:a )?run(?:ning)?|for (?:runners?|running))\b', kind)
    if nonrunning and qualifier:
        return False
    compact_aliases = {'easyrun', 'longrun', 'recoveryrun', 'trailrun', 'trailrunning',
                       'trackrun', 'trackrunning', 'treadmillrun', 'treadmillrunning', 'runwalk'}
    if kind in compact_aliases or re.search(r'\b(?:run|running|runs|jog|jogging)\b', kind):
        return True
    if nonrunning:
        return False
    return bool(re.search(r'\b(?:easy|long|recovery|progression|aerobic|tempo|threshold|intervals?|speed|fartlek|hills?|trail|race|strides?)\b', kind))


def running_session_conflicts(plan, today):
    """Report future running collisions; choosing the replacement remains the AI's job."""
    by_day = {}
    for session in plan.get('sessions', []):
        if session['date'] >= str(today) and planned_running_session(session):
            by_day.setdefault(session['date'], []).append(session['id'])
    return [{'date': day, 'session_ids': identifiers} for day, identifiers in sorted(by_day.items())
            if len(identifiers) > 1]


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


def training_day_conflicts(plan, profile, today):
    """Validate selected days without moving or prescribing the coach's sessions."""
    allowed = training_days(profile)
    if allowed is None:
        return []
    races = [goal for goal in profile.get('goals', [])
             if not goal.get('event', '').lower().startswith('training milestone:')]
    conflicts = []
    for session in plan.get('sessions', []):
        if session['date'] < str(today):
            continue
        weekday = WEEKDAYS[date.fromisoformat(session['date']).weekday()]
        if weekday in allowed:
            continue
        kind = session['workout_type']
        rest = re.sub(r'[^a-z]+', ' ', kind.lower()).strip()
        if rest in {'rest', 'rest day', 'full rest', 'full rest day', 'complete rest',
                    'complete rest day', 'off', 'off day'} and 'workout' not in session:
            continue
        event = bool(re.search(r'\brace\b', kind, re.I)) and not re.search(
            r'race[ -]*(?:pace|effort|simulation|specific|prep)', kind, re.I)
        if event and not race_calendar_conflicts({'sessions': [session]}, {'goals': races}, today):
            # A configured event date can fall outside normal training days.
            continue
        conflicts.append({'session_id': session['id'], 'date': session['date'], 'weekday': weekday})
    return conflicts
