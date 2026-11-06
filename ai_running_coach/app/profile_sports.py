"""Optional sport settings for the profile form; unknown values stay unknown."""

import math
import re


MODES = {'running', 'cycling', 'swimming', 'triathlon'}
GOAL_SPORTS = {'running', 'cycling', 'swimming', 'multisport'}
WEEKDAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
ZONE_NAMES = ('Recovery', 'Endurance', 'Tempo', 'Hard', 'Max')
LABELS = {
    'running': {'available_days': 'Running Availability'},
    'cycling': {'available_days': 'Cycling Availability', 'preferred_long_days': 'Preferred Long Ride Days', 'ftp_watts': 'Cycling FTP'},
    'swimming': {'available_days': 'Swimming Availability', 'css_seconds_per_100m': 'Swimming CSS',
                 'pool_length_metres': 'Pool Length', 'pool_length_unit': 'Pool Length', 'sub_sport': 'Swimming Type'},
}


def label_value(markdown, label):
    match = re.search(r'^\*\*' + re.escape(label) + r':\*\*[ \t]*(.*)$', markdown, re.M | re.I)
    return match[1].strip() if match else ''


def training_mode(markdown):
    return label_value(markdown, 'Training Mode').lower() or 'running'


def days(value):
    return [day for day in WEEKDAYS if re.search(r'\b' + day + r'\b', value, re.I)] or None


def read_settings(markdown):
    result = {sport: {'available_days': days(label_value(markdown, labels['available_days']))}
              for sport, labels in LABELS.items()}
    for sport in ('cycling', 'swimming'):
        result[sport]['zones'] = {}
        for match in re.finditer(r'^\*\*' + sport.title() + r' Zone ([1-5]) \([^\n]+\):\*\*\s*(\d+)\s*-\s*(\d+) bpm$', markdown, re.M | re.I):
            result[sport]['zones'][match[1]] = [int(match[2]), int(match[3])]
    result['cycling'].update(preferred_long_days=days(label_value(markdown, 'Preferred Long Ride Days')), ftp_watts=None)
    ftp = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(?:W|watts?)?', label_value(markdown, 'Cycling FTP'), re.I)
    if ftp:
        result['cycling']['ftp_watts'] = float(ftp[1])
    result['swimming'].update(css_seconds_per_100m=None, pool_length_metres=None, pool_length_unit=None, sub_sport=None)
    css = re.fullmatch(r'(\d+):([0-5]\d)\s*(?:/\s*100\s*m)?', label_value(markdown, 'Swimming CSS'), re.I)
    if css:
        result['swimming']['css_seconds_per_100m'] = int(css[1]) * 60 + int(css[2])
    pool = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(m|yd)', label_value(markdown, 'Pool Length'), re.I)
    if pool:
        unit = pool[2].lower()
        result['swimming'].update(pool_length_metres=float(pool[1]) * (0.9144 if unit == 'yd' else 1), pool_length_unit=unit)
    subtype = label_value(markdown, 'Swimming Type').lower()
    if subtype in ('lap_swimming', 'open_water'):
        result['swimming']['sub_sport'] = subtype
    return result


def positive(value, maximum, title):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
        raise ValueError(f'{title} must be positive and no more than {maximum:g}, or left blank.')
    return value


def validated_days(value, title):
    if value is None:
        return None
    if not isinstance(value, list) or any(day not in WEEKDAYS for day in value):
        raise ValueError(f'Choose listed weekdays for {title}, or leave them blank.')
    return [day for day in WEEKDAYS if day in value] or None


def replace_label(markdown, label, value):
    pattern = r'^\*\*' + re.escape(label) + r':\*\*[^\n]*(?:\n|$)'
    markdown = re.sub(pattern, '', markdown, flags=re.M | re.I)
    return markdown.rstrip() + ('\n**' + label + ':** ' + value if value else '') + '\n'


def apply_settings(markdown, updates, global_days, mode=None):
    """Patch only explicitly edited fields; retain hidden and imported settings."""
    if mode is not None:
        if not isinstance(mode, str) or mode not in MODES:
            raise ValueError('Choose Running, Cycling, Swimming or Triathlon.')
        markdown = replace_label(markdown, 'Training Mode', mode)
    if updates is None:
        updates = {}
    if not isinstance(updates, dict) or set(updates) - set(LABELS):
        raise ValueError('Choose supported sport settings.')
    saved = read_settings(markdown)
    for sport, changes in updates.items():
        allowed = set(LABELS[sport]) | ({'zones'} if sport != 'running' else set())
        if not isinstance(changes, dict) or set(changes) - allowed:
            raise ValueError('Choose supported settings for ' + sport + '.')
        current = dict(saved[sport])
        for key, value in changes.items():
            if key in ('available_days', 'preferred_long_days'):
                current[key] = validated_days(value, sport)
            elif key == 'ftp_watts':
                current[key] = positive(value, 2000, 'Cycling FTP in watts')
            elif key == 'css_seconds_per_100m':
                current[key] = positive(value, 3600, 'Swimming CSS in seconds per 100 m')
                if value is not None and value != int(value):
                    raise ValueError('Enter swimming CSS in whole seconds, such as 2:00 per 100 m.')
            elif key == 'pool_length_metres':
                current[key] = positive(value, 655.34, 'Pool length in metres')
            elif key == 'pool_length_unit':
                if value not in ('m', 'yd', None):
                    raise ValueError('Choose metres or yards for the pool length.')
                current[key] = value
            elif key == 'sub_sport':
                if value not in ('lap_swimming', 'open_water', None):
                    raise ValueError('Choose pool, open water or unknown for swimming.')
                current[key] = value
            elif key == 'zones':
                if not isinstance(value, dict) or set(value) - set('12345'):
                    raise ValueError('Use numbered heart-rate zones from 1 to 5.')
                previous = 0
                for number, bounds in sorted(value.items()):
                    if (not isinstance(bounds, list) or len(bounds) != 2 or any(type(bound) is not int for bound in bounds)
                            or not 1 <= bounds[0] <= bounds[1] <= 255 or bounds[0] < previous):
                        raise ValueError('Enter ascending, non-overlapping heart-rate ranges for ' + sport + '.')
                    previous = bounds[1]
                current[key] = value
        saved[sport] = current
        if sport == 'swimming' and set(changes) & {'pool_length_metres', 'pool_length_unit'}:
            if current['pool_length_metres'] is not None and current['pool_length_unit'] is None:
                raise ValueError('Choose a unit for the saved pool length.')
            value = current['pool_length_metres']
            shown = value / (0.9144 if current['pool_length_unit'] == 'yd' else 1) if value is not None else None
            markdown = replace_label(markdown, 'Pool Length', f'{shown:.8g} {current["pool_length_unit"]}' if shown is not None else '')
        for key in changes:
            value = current[key]
            if key.startswith('pool_length_'):
                continue
            if key == 'zones':
                markdown = re.sub(r'^\*\*' + sport.title() + r' Zone [1-5] \([^\n]+\):\*\*[^\n]*(?:\n|$)', '', markdown, flags=re.M | re.I)
                for number, bounds in sorted(value.items()):
                    markdown = replace_label(markdown, f'{sport.title()} Zone {number} ({ZONE_NAMES[int(number)-1]})', f'{bounds[0]} - {bounds[1]} bpm')
            else:
                text = '' if value is None else ', '.join(value) if isinstance(value, list) else str(value)
                if key == 'ftp_watts' and value is not None:
                    text = f'{value:g} W'
                if key == 'css_seconds_per_100m' and value is not None:
                    text = f'{int(value)//60}:{int(value)%60:02d} /100m'
                markdown = replace_label(markdown, LABELS[sport][key], text)
    focus = training_mode(markdown)
    active = set(LABELS) if focus == 'triathlon' else {focus}
    for sport, current in saved.items():
        availability = current.get('available_days')
        if (sport in active or sport in updates) and availability and global_days and any(day not in global_days for day in availability):
            raise ValueError(f'{sport.title()} days must also be selected as available training days.')
    ride_days = saved['cycling'].get('preferred_long_days') or []
    cycling_days = saved['cycling'].get('available_days') or global_days
    if ('cycling' in active or 'cycling' in updates) and cycling_days and any(day not in cycling_days for day in ride_days):
        raise ValueError('Preferred long-ride days must fit your cycling availability.')
    return markdown
