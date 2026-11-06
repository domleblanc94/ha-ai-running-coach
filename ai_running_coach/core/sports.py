"""Explicit disciplines and conservative compatibility for older running plans."""
import re

TRAINING_MODES = {'running', 'cycling', 'swimming', 'triathlon'}
ENDURANCE_SPORTS = {'running', 'cycling', 'swimming'}
GOAL_SPORTS = ENDURANCE_SPORTS | {'multisport'}
SESSION_SPORTS = GOAL_SPORTS | {'strength', 'mobility', 'rest', 'other'}
SWIM_SUB_SPORTS = {'lap_swimming', 'open_water'}
SPORT_LABELS = {'running': 'Running', 'cycling': 'Cycling', 'swimming': 'Swimming',
                'multisport': 'Triathlon', 'strength': 'Strength', 'mobility': 'Mobility', 'rest': 'Rest', 'other': 'Other'}


def sport_label(session):
    if session_sport(session) == 'swimming':
        return {'lap_swimming': 'Pool swimming', 'open_water': 'Open-water swimming'}.get(session.get('sub_sport'), 'Swimming')
    return SPORT_LABELS.get(session_sport(session), 'Other')


def parse_sport_label(value):
    if not str(value).strip():
        return None, None
    label = str(value).strip().casefold()
    if label == 'pool swimming':return 'swimming', 'lap_swimming'
    if label == 'open-water swimming':return 'swimming', 'open_water'
    names = {text.casefold(): sport for sport, text in SPORT_LABELS.items()}
    names.update({sport: sport for sport in SESSION_SPORTS})
    if label not in names:raise ValueError('Unsupported sport in plan table.')
    return names[label], None


def training_mode(profile):
    return profile.get('training_mode', 'running')


def effective_goal_sport(goal):
    # Changing the training focus must not retag old running goals.
    return goal.get('sport', 'running')


def _label(value):
    value = re.sub(r'([a-z])([A-Z])', r'\1 \2', str(value or ''))
    return re.sub(r'[^a-z]+', ' ', value.lower()).strip()


def legacy_running_label(value):
    kind = _label(value)
    nonrunning = bool(re.search(r'\b(?:strength|swim|swimming|cycling|cycle|bike|biking|ride|yoga|walk|walking|hike|hiking|rest|off|mobility|row|rowing|cross train|cross training)\b', kind))
    qualifier = re.search(r'\b(?:(?:pre|post) ?run(?:ning)?|(?:before|after) (?:a )?run(?:ning)?|for (?:runners?|running))\b', kind)
    if nonrunning and qualifier:
        return False
    aliases = {'easyrun', 'longrun', 'recoveryrun', 'trailrun', 'trailrunning',
               'trackrun', 'trackrunning', 'treadmillrun', 'treadmillrunning', 'runwalk'}
    if kind in aliases or re.search(r'\b(?:run|running|runs|jog|jogging)\b', kind):
        return True
    return not nonrunning and bool(re.search(r'\b(?:easy|long|recovery|progression|aerobic|tempo|threshold|intervals?|speed|fartlek|hills?|trail|race|strides?)\b', kind))


def session_sport(session):
    """An explicit discipline wins; old labels are a read-only compatibility path."""
    if 'sport' in session:
        return session['sport']
    workout = session.get('workout')
    if isinstance(workout, dict) and isinstance(workout.get('sport'),str) and workout.get('sport') in ENDURANCE_SPORTS:
        return workout['sport']
    label = _label(session.get('workout_type'))
    if legacy_running_label(label):
        return 'running'
    if re.search(r'\b(?:cycling|cycle|bike|biking|ride)\b', label):
        return 'cycling'
    if re.search(r'\b(?:swim|swimming)\b', label):
        return 'swimming'
    if re.search(r'\b(?:strength|weights?)\b', label):
        return 'strength'
    if re.search(r'\b(?:mobility|yoga|stretching)\b', label):
        return 'mobility'
    if label in {'rest', 'rest day', 'full rest', 'full rest day', 'complete rest', 'complete rest day', 'off', 'off day'}:
        return 'rest'
    return 'other'


def activity_sport(value):
    """Map recorded provider kinds, without rewriting their stored raw values."""
    key = re.sub(r'[^a-z]', '', str(value or '').lower())
    groups = {
        'running': {'run', 'running', 'trailrun', 'trailrunning', 'virtualrun', 'treadmillrunning', 'trackrun', 'trackrunning', 'ultrarun', 'ultrarunning'},
        'cycling': {'ride', 'cycling', 'virtualride', 'virtualcycling', 'indoorcycling', 'mountainbikeride', 'mountainbiking', 'roadbiking', 'gravelcycling', 'gravelbiking', 'ebikeride', 'ebiking'},
        'swimming': {'swim', 'swimming', 'lapswimming', 'poolswimming', 'openwaterswimming', 'openwaterswim'},
        'multisport': {'multisport', 'triathlon', 'duathlon', 'brick'},
        'strength': {'strength', 'strengthtraining', 'weighttraining'},
        'mobility': {'yoga', 'mobility', 'stretching'},
    }
    return next((sport for sport, aliases in groups.items() if key in aliases), 'other')


def activity_sub_sport(value):
    key = re.sub(r'[^a-z]', '', str(value or '').lower())
    if key in {'lapswimming', 'poolswimming'}:
        return 'lap_swimming'
    if key in {'openwaterswimming', 'openwaterswim'}:
        return 'open_water'
    return None


def validate_session_sport(session):
    if 'sport' in session and (not isinstance(session['sport'],str) or session['sport'] not in SESSION_SPORTS):
        raise ValueError('Unsupported session sport.')
    sport = session_sport(session)
    if 'sub_sport' in session and (sport != 'swimming' or not isinstance(session['sub_sport'],str) or session['sub_sport'] not in SWIM_SUB_SPORTS):
        raise ValueError('Swimming sub_sport must be lap_swimming or open_water.')
    workout = session.get('workout')
    if workout is not None:
        if sport not in ENDURANCE_SPORTS or not isinstance(workout, dict) or workout.get('sport') != sport:
            raise ValueError('Session sport and structured workout sport differ.')
        if sport == 'swimming' and (session.get('sub_sport') != 'lap_swimming' or workout.get('subSport') != 'lap_swimming'):
            raise ValueError('Only explicitly configured pool swimming sessions support FIT workouts.')
    if 'brick' in session:
        brick = session['brick']
        if (sport not in ENDURANCE_SPORTS or not isinstance(brick, dict) or set(brick) != {'id', 'order'}
                or not isinstance(brick['id'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', brick['id'])
                or type(brick['order']) is not int or not 1 <= brick['order'] <= 4):
            raise ValueError('A brick leg needs a single sport, stable brick ID and order 1-4.')


def validate_bricks(sessions):
    groups = {}
    for session in sessions:
        if 'brick' in session:
            groups.setdefault(session['brick']['id'], []).append(session)
    for legs in groups.values():
        orders = sorted(leg['brick']['order'] for leg in legs)
        if (not 2 <= len(legs) <= 4 or orders != list(range(1, len(legs)+1))
                or len({leg['date'] for leg in legs}) != 1
                or len({session_sport(leg) for leg in legs}) < 2):
            raise ValueError('Brick legs must share one date with consecutive unique order and at least two sports.')
