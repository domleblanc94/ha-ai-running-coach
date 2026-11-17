"""Check and present coach-authored interval instructions without prescribing them."""
import re

from sports import session_sport
from workout_schema import validate_workout

QUALITY = re.compile(r'\b(?:intervals?|strides?|fartlek|repeats?|repetitions?|(?:hill|speed)\s+reps?)\b', re.I)
QUANTITY = r'\d+(?:\.\d+)?\s*(?:kilomet(?:er|re)s?|km|met(?:er|re)s?|miles?|mi|minutes?|mins?|seconds?|secs?|hours?|hrs?|[msh])\b'
REPETITIONS = re.compile(r'\b(\d{1,2})\s*(?:[x×]|(?:reps?|repeats?|repetitions?)\s*(?:of\s*)?)\s*(' + QUANTITY + ')', re.I)
EFFORT = re.compile(r'\b(?:easy|steady|controlled|hard|brisk|fast|relaxed|threshold|tempo|conversational|sprint|smooth|comfortable|comfortably|race[ -]pace|(?:\d+\s*k|half[ -]marathon|marathon)\s*pace|RPE\s*\d|Z[1-5]|zone\s*[1-5]|\d+\s*(?:bpm|watts?|W))\b|\d+:\d{2}\s*(?:/|per\s+)(?:km|mi|mile)', re.I)


def is_quality_session(session):
    if session_sport(session) not in {'running', 'cycling', 'swimming'}:
        return False
    return bool(QUALITY.search((session.get('workout_type', '') + ' ' + session.get('amount', '')).replace('_', ' ')))


def _clauses(text):
    # Preserve decimals and pace notation; semicolons are the preferred compact separator.
    return [part.strip() for part in re.split(r';|\n|\.(?:\s|$)', text) if part.strip()]


def _phase(clauses, label):
    return any(re.search(label, part, re.I) and re.search(QUANTITY, part, re.I) for part in clauses)


def _readable_complete(text):
    clauses = _clauses(text)
    repetition = REPETITIONS.search(text)
    work = next((part for part in clauses if REPETITIONS.search(part)), '')
    if work:
        work = work[REPETITIONS.search(work).start():]
        work = re.split(r'\b(?:with|recover(?:y|ies)?|rest|cool[ -]?down|warm[ -]?up)\b', work, maxsplit=1, flags=re.I)[0]
    return bool(repetition and int(repetition[1]) >= 1 and EFFORT.search(work)
                and _phase(clauses, r'\b(?:warm[ -]?up|WU)\b')
                and _phase(clauses, r'\b(?:cool[ -]?down|CD)\b')
                and _phase(clauses, r'\b(?:recover(?:y|ies)?|rest|between)\b'))


def _structured_complete(workout):
    steps = validate_workout(workout)['steps']
    intensities = [step['intensity'] for step in steps]
    if not steps or intensities[0] != 'warmup' or intensities[-1] != 'cooldown':
        return False
    active = [index for index, step in enumerate(steps) if step['intensity'] == 'active']
    if not active or any(steps[index]['duration']['type'] == 'open' for index in active):
        return False
    # Recovery must be explicit between efforts, not invented by the exporter.
    for first, second in zip(active, active[1:]):
        if not any(step['intensity'] in {'recovery', 'rest'}
                   for step in steps[first+1:second]):
            return False
    if len(active) == 1 and not any(step['intensity'] in {'recovery', 'rest'} for step in steps):
        return False
    for index in active:
        step = steps[index]
        if step['target']['type'] in {'open', 'stroke'} and not EFFORT.search(' '.join((step.get('name', ''), step.get('notes', '')))):
            return False
    return True


def _quantity_key(text):
    match=re.fullmatch(r'(\d+(?:\.\d+)?)\s*([a-z]+)',text.strip(),re.I)
    value=float(match[1]);unit=match[2].lower()
    if unit in {'s','sec','secs','second','seconds'}:return ('time',round(value,6))
    if unit in {'min','mins','minute','minutes'}:return ('time',round(value*60,6))
    if unit in {'h','hr','hrs','hour','hours'}:return ('time',round(value*3600,6))
    if unit in {'km','kilometre','kilometres','kilometer','kilometers'}:value*=1000
    elif unit in {'mi','mile','miles'}:value*=1609.344
    return ('distance',round(value,6))


def _written_repetitions_agree(session,steps):
    actual=[(step['duration']['type'],round(step['duration'].get('seconds',step['duration'].get('metres',0)),6))
            for step in steps if step['intensity']=='active']
    for field in ('target','amount','workout_type'):
        repetitions=list(REPETITIONS.finditer(session.get(field,'')))
        if not repetitions:continue
        expected=[]
        for repetition in repetitions:
            count=int(repetition[1])
            if count<1:return False
            expected.extend([_quantity_key(repetition[2])]*count)
        # A separate steady/easy block may also be active. Compare the explicitly
        # described quantities, in order, rather than assuming every active step
        # is a member of the first repetition block.
        quantities=set(expected)
        if [item for item in actual if item in quantities]!=expected:return False
    return True


def quality_details_issue(session):
    """Return a reason for incomplete quality work; untouched legacy plans may retain it."""
    if not is_quality_session(session):
        return None
    workout = session.get('workout')
    if workout is not None:
        try:
            if not _structured_complete(workout):
                return 'Structured intervals need warm-up, explicit work efforts, recovery, effort cues and cooldown.'
            if not _written_repetitions_agree(session,workout['steps']):
                return 'Written repetition counts and work quantities must match the structured workout steps.'
        except (ValueError, TypeError, KeyError):
            return 'The saved structured workout needs complete valid instructions.'
        return None
    if not _readable_complete(session.get('target', '')):
        return 'Intervals or strides need warm-up, repetitions, work duration or distance and effort, recovery, and cooldown.'
    return None


def validate_quality_session(session):
    issue = quality_details_issue(session)
    if issue:
        raise ValueError('Incomplete workout instructions: ' + issue)


def incomplete_session_details(plan, today):
    return [{'id': session['id'], 'reason': issue}
            for session in plan.get('sessions', []) if session['date'] >= str(today)
            if (issue := quality_details_issue(session))]


def _number(value):
    return format(value, '.8g')


def _duration(value):
    if value['type'] == 'open':
        return 'until lap press'
    if value['type'] == 'distance':
        metres = value['metres']
        return _number(metres / 1000) + ' km' if metres >= 1000 else _number(metres) + ' m'
    seconds = value['seconds']
    return _number(seconds / 60) + ' min' if seconds % 60 == 0 else _number(seconds) + ' s'


def _pace(speed):
    seconds=round(1000/speed)
    return str(seconds//60)+':'+str(seconds%60).zfill(2)


def workout_instruction_lines(session):
    """Human-readable steps use exactly the same quantities as the saved FIT workout."""
    if not session.get('workout'):
        return []
    workout = validate_workout(session['workout'])
    lines = []
    for index, step in enumerate(workout['steps'], 1):
        labels = {'warmup':'Warm-up', 'active':'Work', 'recovery':'Recovery', 'cooldown':'Cooldown', 'rest':'Rest'}
        line = f"{index}. {labels[step['intensity']]}: {_duration(step['duration'])}"
        target = step['target']; kind = target['type']
        if kind == 'heart_rate': line += f" at {target['lowBpm']}-{target['highBpm']} bpm"
        elif kind == 'speed':
            if workout['sport']=='running':line += f" at {_pace(target['highMps'])}-{_pace(target['lowMps'])} min/km"
            else:line += f" at {_number(target['lowMps']*3.6)}-{_number(target['highMps']*3.6)} km/h"
        elif kind == 'power': line += f" at {target['lowWatts']}-{target['highWatts']} W"
        elif kind == 'cadence': line += f" at {target['lowRpm']}-{target['highRpm']} rpm"
        elif kind == 'stroke': line += ' · ' + target['stroke'].replace('_', ' ')
        for field in ('name', 'notes'):
            if step.get(field): line += ' · ' + step[field]
        lines.append(line)
    return lines
