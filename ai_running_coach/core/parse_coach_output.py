"""Strict ID-based feedback parsing; legacy date tags are intentionally rejected."""
import json
import os
import sqlite3
from contextlib import closing

DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ai_coach_master.sqlite')


def validate_activity_reviews(reviews, allowed_ids):
    if not isinstance(reviews, list):
        raise ValueError('reviews must be a list')
    allowed_ids, seen, clean = set(allowed_ids), set(), []
    for review in reviews:
        if not isinstance(review, dict) or set(review) != {'activity_id', 'feedback'}:
            raise ValueError('Each review requires activity_id and feedback only')
        activity_id, feedback = review['activity_id'], review['feedback']
        if type(activity_id) is not int or activity_id not in allowed_ids or activity_id in seen:
            raise ValueError('Unknown, duplicated, or invalid activity ID')
        if not isinstance(feedback, str) or not feedback.strip() or len(feedback)>4000:
            raise ValueError('feedback must be nonempty text of at most 4000 characters')
        seen.add(activity_id)
        clean.append({'activity_id': activity_id, 'feedback': feedback.strip()})
    return clean


def process_activity_reviews(reviews, db_path=DEFAULT_DB_PATH):
    """Validate every review before committing any activity update."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ids = [r[0] for r in conn.execute('SELECT id FROM activities')]
        clean = validate_activity_reviews(reviews, ids)
        columns = {r[1] for r in conn.execute('PRAGMA table_info(activities)')}
        if 'ai_feedback' not in columns:
            conn.execute('ALTER TABLE activities ADD COLUMN ai_feedback TEXT')
        conn.executemany('UPDATE activities SET ai_feedback=? WHERE id=?',
                         [(r['feedback'],r['activity_id']) for r in clean])
    return len(clean)


def process_ai_coach_review(review_text, db_path=DEFAULT_DB_PATH):
    """Compatibility entry point accepting a strict JSON feedback envelope.

    Free text/date regex output is rejected rather than attributed to all
    sessions on a date. Callers with richer envelopes validate those upstream.
    """
    payload = json.loads(review_text) if isinstance(review_text,str) else review_text
    if not isinstance(payload,dict) or set(payload) != {'reviews','coaching_feedback'}:
        raise ValueError('Expected JSON reviews and coaching_feedback')
    summary = payload['coaching_feedback']
    if not isinstance(summary,str) or not summary.strip() or len(summary)>20000:
        raise ValueError('coaching_feedback must be nonempty text')
    with closing(sqlite3.connect(db_path)) as conn, conn:
        ids = [r[0] for r in conn.execute('SELECT id FROM activities')]
        clean = validate_activity_reviews(payload['reviews'], ids)
        columns = {r[1] for r in conn.execute('PRAGMA table_info(activities)')}
        if 'ai_feedback' not in columns:
            conn.execute('ALTER TABLE activities ADD COLUMN ai_feedback TEXT')
        conn.execute('''CREATE TABLE IF NOT EXISTS daily_coach_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP, summary_text TEXT)''')
        conn.executemany('UPDATE activities SET ai_feedback=? WHERE id=?',
                         [(r['feedback'],r['activity_id']) for r in clean])
        conn.execute('INSERT INTO daily_coach_reviews(summary_text) VALUES (?)',(summary.strip(),))
    return len(clean)
