"""Observational baseline running feedback for the Home Assistant app."""

from contextlib import closing
from datetime import timedelta
import json
import math
import re
import sqlite3

from .storage import checked
from .profile_sports import training_mode


LEARNING_MARKER = re.compile(r'^\*\*HR Zone Learning:\*\*[ \t]+Enabled[ \t]*$', re.MULTILINE)
TALK_TESTS = {'comfortable', 'broken', 'unable', 'unknown'}
SENSORS = {'wrist', 'chest_strap', 'unknown'}
COMMENT_PREFIX = 'HA HR baseline observation.'
LEARNING_QUESTION = (
    'I am learning how my heart rate relates to running effort. Use my goals, availability, recovery, '
    'and current ability to plan or adapt a few easy baseline runs where appropriate. Mark these as '
    'baseline runs and use comfortable conversational effort, with open heart-rate targets. Use '
    'running or run/walk as appropriate to my ability, without requiring a maximal or all-out test. '
    'Review explicitly labelled HA HR baseline check-ins, including RPE, talk test, sensor, and '
    'recorded whole-activity average heart rate when available. In the summary, say what the '
    'observations support, what is uncertain, and what to record next. Missing data are unknown. '
    'Treat terrain, conditions and sensor differences as potential context. Do not infer five zones, '
    'threshold heart rate or maximum heart rate from these summaries or age. Do not change existing '
    'configured zones or prescribe numerical cutoffs from these observations. Keep the training '
    'plan useful for my goals while collecting this evidence.'
)
OBSERVATION_PREFIX = re.compile(
    r'^HA HR baseline observation\. Talk test: (comfortable|broken|unable|unknown)\. '
    r'HR sensor: (wrist|chest_strap|unknown)\.'
)


def learning_enabled(profile):
    return training_mode(profile) in {'running', 'triathlon'} and bool(LEARNING_MARKER.search(profile))


def validate_feedback(payload):
    if not isinstance(payload, dict) or set(payload) != {'activity_id', 'rpe', 'talk_test', 'sensor'}:
        raise ValueError('Choose an activity and provide its baseline observations.')
    if type(payload['activity_id']) is not int:
        raise ValueError('Choose a valid activity.')
    rpe = payload['rpe']
    if type(rpe) is not int or not 1 <= rpe <= 10:
        raise ValueError('Perceived effort must be a whole number from 1 to 10.')
    for name, choices in (('talk_test', TALK_TESTS), ('sensor', SENSORS)):
        if not isinstance(payload[name], str) or payload[name] not in choices:
            raise ValueError('Choose a listed talk-test result and sensor type.')
    return payload


def positive_number(value, divisor=1):
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        return None
    return round(value / divisor, 1)


def candidates(database, today):
    """Read recent official running observations and their latest app feedback."""
    database = checked(database)
    if not database.is_file():
        return []
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        fields = {row[1] for row in conn.execute('PRAGMA table_info(activities)')}
        needed = {'id', 'start_date', 'source', 'normalized_type', 'avg_heartrate', 'moving_time_s'}
        if not needed <= fields:
            return []
        rows = conn.execute('''
            SELECT id, date(start_date), avg_heartrate, moving_time_s
            FROM activities
            WHERE source = 'garmin_personal' AND normalized_type = 'run'
              AND date(start_date) BETWEEN ? AND ?
            ORDER BY start_date DESC, id DESC LIMIT 12
        ''', ((today - timedelta(days=20)).isoformat(), today.isoformat())).fetchall()
        feedback = latest_feedback(conn, [row[0] for row in rows])
    return [
        {'activity_id': activity_id, 'date': day, 'average_hr_bpm': positive_number(average),
         'moving_minutes': positive_number(moving, 60), 'feedback': feedback.get(activity_id)}
        for activity_id, day, average, moving in rows
    ]


def latest_feedback(conn, activity_ids):
    if not activity_ids or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='athlete_checkins'"
    ).fetchone():
        return {}
    placeholders = ','.join('?' for _ in activity_ids)
    rows = conn.execute(f'''
        SELECT activity_id, feedback_json, comment FROM athlete_checkins
        WHERE source = 'local_cli' AND comment LIKE ? AND activity_id IN ({placeholders})
        ORDER BY id DESC
    ''', (COMMENT_PREFIX + '%', *activity_ids))
    feedback = {}
    for activity_id, fields_json, comment in rows:
        if activity_id in feedback:
            continue
        match = OBSERVATION_PREFIX.match(comment)
        try:
            fields = json.loads(fields_json)
        except (ValueError, TypeError):
            continue
        if not match or not isinstance(fields, dict) or fields.get('topic') != f'HR baseline activity {activity_id}':
            continue
        rpe = fields.get('rpe')
        if type(rpe) is int and 1 <= rpe <= 10:
            feedback[activity_id] = {'rpe': rpe, 'talk_test': match[1], 'sensor': match[2]}
    return feedback


def status(enabled, database, today):
    review = None
    try:
        rows = candidates(database, today) if enabled else []
    except sqlite3.Error:
        rows = []
    if enabled:
        try:
            review = latest_review(database)
        except sqlite3.Error:
            pass
    count = sum(row['feedback'] is not None for row in rows)
    if not enabled:
        state = 'off'
    elif not rows:
        state = 'awaiting_runs'
    elif not count:
        state = 'awaiting_feedback'
    else:
        state = 'observations_ready'
    return {'enabled': enabled, 'state': state, 'feedback_count': count, 'candidates': rows, 'review': review}


def latest_review(database):
    database = checked(database)
    if not database.is_file():
        return None
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        row = conn.execute('''
            SELECT request.completed_at, job.response_json
            FROM coach_api_requests AS request
            JOIN coach_review_jobs AS job ON job.fingerprint = request.job_fingerprint
            WHERE request.status IN ('complete', 'accepted_after_revalidation') AND job.status = 'complete'
              AND CASE WHEN json_valid(request.input_snapshot_json)
                       THEN json_extract(request.input_snapshot_json, '$.question') END = ?
            ORDER BY request.id DESC LIMIT 1
        ''', (LEARNING_QUESTION,)).fetchone()
    if not row or not isinstance(row[0], str):
        return None
    try:
        response = json.loads(row[1])
    except (ValueError, TypeError):
        return None
    if not isinstance(response, dict) or not isinstance(response.get('summary'), str):
        return None
    return {'completed_at': row[0], 'summary': response['summary'][:2000]}


def checkin(payload, activity, today):
    average = activity['average_hr_bpm']
    moving = activity['moving_minutes']
    comment = (
        f'{COMMENT_PREFIX} Talk test: {payload["talk_test"]}. HR sensor: {payload["sensor"]}. '
        f'Activity date: {activity["date"]}. Whole-activity average HR: '
        f'{"unavailable" if average is None else f"{average:g} bpm"}. '
        f'Moving duration: {"unavailable" if moving is None else f"{moving:g} minutes"}.'
    )
    return {
        'reported_on': today.isoformat(), 'activity_id': payload['activity_id'], 'comment': comment,
        'fields': {'rpe': payload['rpe'], 'topic': f'HR baseline activity {payload["activity_id"]}'},
    }
