"""Coach-authored phase metadata and date lookup; never infer training periods."""
import copy
from datetime import date, timedelta
import re


PHASES_UNSET = object()
PHASE_FIELDS = {'start_date', 'end_date', 'name', 'purpose'}


def phase_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('Training phase dates must use YYYY-MM-DD.')
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError('Training phase dates must be valid calendar dates.') from None


def _text(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return False
    if any(ord(char) < 32 or ord(char) == 127 or char in '\u2028\u2029' for char in value):
        return False
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        return False
    return True


def validate_phases(phases, *, max_items=None):
    """Validate metadata without normalizing/mutating retained historical records."""
    if not isinstance(phases, list) or (max_items is not None and len(phases) > max_items):
        raise ValueError('Training phases must be a bounded list of date ranges.')
    for phase in phases:
        if not isinstance(phase, dict) or set(phase) != PHASE_FIELDS:
            raise ValueError('Training phases need start_date, end_date, name and purpose.')
        start, end = phase_date(phase['start_date']), phase_date(phase['end_date'])
        if start > end:
            raise ValueError('Training phase end date must not precede its start date.')
        if not _text(phase['name'], 64) or not _text(phase['purpose'], 240):
            raise ValueError('Training phase name/purpose must be bounded, nonempty, single-line text.')
    previous = None
    for phase in sorted(phases, key=lambda item: item['start_date']):
        if previous is not None and phase['start_date'] <= previous:
            raise ValueError('Training phase date ranges must not overlap.')
        previous = phase['end_date']
    return phases


def phases_overlapping(plan, start, end):
    if phase_date(start) > phase_date(end):
        raise ValueError('Phase lookup end must not precede start.')
    phases = validate_phases(plan.get('phases', []))
    return sorted((phase for phase in phases if phase['start_date'] <= end and phase['end_date'] >= start),
                  key=lambda phase: phase['start_date'])


def phase_for_date(plan, day):
    phases = phases_overlapping(plan, day, day)
    return phases[0] if phases else None


def phase_spans(plan, start, end):
    """Display a requested interval, explicitly retaining gaps as unknown."""
    phases = phases_overlapping(plan, start, end)
    cursor = phase_date(start)
    final = phase_date(end)
    spans = []
    for phase in phases:
        left = max(phase_date(phase['start_date']), cursor)
        right = min(phase_date(phase['end_date']), final)
        if cursor < left:
            spans.append({'start_date': cursor.isoformat(), 'end_date': (left - timedelta(days=1)).isoformat(), 'phase': None})
        spans.append({'start_date': left.isoformat(), 'end_date': right.isoformat(), 'phase': phase})
        if right == final:
            return spans
        cursor = right + timedelta(days=1)
    if cursor <= final:
        spans.append({'start_date': cursor.isoformat(), 'end_date': final.isoformat(), 'phase': None})
    return spans


def replace_future_phases(existing, replacement, today):
    """Replace only coach-authored future metadata; preserve meaning on past dates."""
    boundary = phase_date(str(today))
    validate_phases(existing)
    validate_phases(replacement, max_items=24)
    for phase in replacement:
        if phase_date(phase['start_date']) < boundary:
            raise ValueError('Replacement training phases must start today or later.')
        if (phase_date(phase['end_date']) - boundary).days > 366:
            raise ValueError('Replacement training phase outside supported future window.')
    retained = []
    for phase in existing:
        if phase_date(phase['start_date']) < boundary:
            past = copy.deepcopy(phase)
            if phase_date(past['end_date']) >= boundary:
                past['end_date'] = (boundary - timedelta(days=1)).isoformat()
            retained.append(past)
    combined = sorted(retained + copy.deepcopy(replacement), key=lambda phase: phase['start_date'])
    # Adjacent segments with exactly identical meaning are one range. This
    # prevents daily restatements accumulating arbitrary historical fragments.
    result = []
    for phase in combined:
        if (result and result[-1]['name'] == phase['name'] and result[-1]['purpose'] == phase['purpose']
                and (phase_date(phase['start_date']) - phase_date(result[-1]['end_date'])).days == 1):
            result[-1]['end_date'] = phase['end_date']
        else:
            result.append(phase)
    validate_phases(result)
    return result
