"""One eligible scheduled run per local calendar day, including DST changes."""

from datetime import time
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_schedule(enabled, time_text, timezone):
    if type(enabled) is not bool:
        raise ValueError('Schedule enabled must be a boolean.')
    if not isinstance(time_text, str) or not re.fullmatch(r'[0-9]{2}:[0-9]{2}', time_text):
        raise ValueError('Schedule time must be HH:MM.')
    try:
        time.fromisoformat(time_text)
    except ValueError:
        raise ValueError('Schedule time must be between 00:00 and 23:59.') from None
    if not isinstance(timezone, str) or not timezone or len(timezone) > 100:
        raise ValueError('Use an IANA timezone such as Europe/London or UTC.')
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError('Use an IANA timezone such as Europe/London or UTC.') from None


def due_date(schedule, now):
    if not schedule['enabled']:
        return None
    local = now.astimezone(ZoneInfo(schedule['timezone']))
    today = local.date().isoformat()
    if today <= (schedule['last_date'] or ''):
        return None
    if local.strftime('%H:%M') < schedule['time']:
        return None
    return today
