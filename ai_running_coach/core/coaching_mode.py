"""Explicit, bounded planning budgets. No network, retries or training decisions."""
import copy
from datetime import date, timedelta
import os

PLANNING_MODES = ('economical', 'advanced')
OVERVIEW_UNSET = object()
OVERVIEW_FIELDS = {'start_date', 'end_date', 'focus', 'rationale', 'adaptation', 'review_trigger'}


def planning_settings(environment=None):
    env = os.environ if environment is None else environment
    mode = (env.get('COACH_PLANNING_MODE') or 'economical').strip().lower()
    if mode not in PLANNING_MODES:
        raise ValueError('COACH_PLANNING_MODE must be economical or advanced.')
    advanced = mode == 'advanced'
    prefix = 'COACH_ADVANCED_' if advanced else 'COACH_'
    def bounded(name, default, minimum, maximum):
        value = env.get(prefix + name)
        try:
            value = int(value) if value is not None and str(value).strip() else default
        except (ValueError, TypeError):
            raise ValueError(prefix + name + ' must be an integer.') from None
        return max(minimum, min(maximum, value))
    return {
        'mode': mode,
        'max_output_tokens': bounded('MAX_OUTPUT_TOKENS', 16384 if advanced else 4096, 256, 32768 if advanced else 8192),
        'max_input_chars': bounded('MAX_INPUT_CHARS', 120000 if advanced else 60000, 8000, 240000 if advanced else 120000),
        'planning_days': 28 if advanced else 7,
        'activity_review_days': 14 if advanced else 7,
        'summary_max_chars': 6000 if advanced else 2000,
        'feedback_max_chars': 3600 if advanced else 1800,
        'http_timeout_seconds': bounded('HTTP_TIMEOUT_SECONDS', 180 if advanced else 60, 5, 300 if advanced else 120),
        'run_timeout_seconds': 1200 if advanced else 900,
    }


def evidence_settings(evidence):
    # Limits come from trusted policy, not arbitrary values in imported records.
    # Snapshots retain the selected mode so offline replay uses its original contract.
    return planning_settings({'COACH_PLANNING_MODE': evidence.get('planning_mode', 'economical')})


def validate_overview(items, max_items=None):
    if not isinstance(items, list) or (max_items is not None and len(items) > max_items):
        raise ValueError('Planning overview must be a bounded list of dated blocks.')
    for item in items:
        if not isinstance(item, dict) or set(item) != OVERVIEW_FIELDS:
            raise ValueError('Planning overview needs dates, focus, rationale, adaptation and review_trigger.')
        from training_phases import phase_date, _text
        start, end = phase_date(item['start_date']), phase_date(item['end_date'])
        if start > end or (end - start).days > 13:
            raise ValueError('Planning overview blocks must span one to fourteen days.')
        for key, maximum in (('focus', 180), ('rationale', 1200), ('adaptation', 1200), ('review_trigger', 600)):
            if not _text(item[key], maximum):
                raise ValueError('Planning overview text must be bounded, nonempty and single-line.')
    previous = None
    for item in sorted(items, key=lambda row: row['start_date']):
        if previous is not None and item['start_date'] <= previous:
            raise ValueError('Planning overview blocks must not overlap.')
        previous = item['end_date']
    return items


def replace_future_overview(existing, replacement, today):
    boundary = date.fromisoformat(str(today))
    validate_overview(existing)
    validate_overview(replacement, max_items=8)
    for item in replacement:
        if item['start_date'] < str(today) or date.fromisoformat(item['end_date']) > boundary + timedelta(days=366):
            raise ValueError('Replacement planning overview must cover today or later within one year.')
    retained = []
    for item in existing:
        if item['start_date'] < str(today):
            past = copy.deepcopy(item)
            if past['end_date'] >= str(today):
                past['end_date'] = (boundary - timedelta(days=1)).isoformat()
            retained.append(past)
    result = sorted(retained + copy.deepcopy(replacement), key=lambda item: item['start_date'])
    validate_overview(result)
    return result
