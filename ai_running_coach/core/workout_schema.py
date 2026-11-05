"""Validate explicit watch-workout instructions without deriving training content.

FIT encodes time at millisecond, distance at centimetre and speed at
millimetre/second precision. Repetitions must already be explicitly flattened.
"""
import math

MAX_STEPS = 50
MAX_NAME_BYTES = 63
MAX_NOTES_BYTES = 200
MAX_UINT32_VALUE = 0xFFFFFFFE
INTENSITIES = {'warmup','active','recovery','cooldown','rest'}


def _object(value, required, optional=()):
    if not isinstance(value,dict) or not set(required).issubset(value) or set(value)-set(required)-set(optional):
        raise ValueError('Workout object has missing or unsupported fields')


def _name(value, required=False):
    if not isinstance(value,str):raise ValueError('Workout names must be text')
    value=value.strip()
    if required and not value:raise ValueError('Workout name is required')
    if any(ord(character)<32 or ord(character)==127 for character in value):
        raise ValueError('Workout names cannot contain control characters')
    try:encoded=value.encode('utf-8')
    except UnicodeEncodeError:raise ValueError('Workout names require valid Unicode text') from None
    if len(encoded)>MAX_NAME_BYTES:raise ValueError('Workout names must fit within 63 UTF-8 bytes')
    return value


def _notes(value):
    if not isinstance(value,str):raise ValueError('Workout step notes must be text')
    value=value.strip()
    if any((ord(c)<32 and c not in '\r\n\t') or ord(c)==127 for c in value):
        raise ValueError('Workout step notes contain invalid control characters')
    try:encoded=value.encode('utf-8')
    except UnicodeEncodeError:raise ValueError('Workout step notes require valid Unicode text') from None
    if len(encoded)>MAX_NOTES_BYTES:raise ValueError('Workout step notes must fit within 200 UTF-8 bytes')
    return value


def _scaled(value, scale, label):
    if isinstance(value,bool) or not isinstance(value,(int,float)):
        raise ValueError(label+' must be an explicit positive finite number')
    try:finite=math.isfinite(value)
    except OverflowError:finite=False
    if not finite or value<=0 or value>MAX_UINT32_VALUE+1:
        raise ValueError(label+' must be an explicit positive finite number within the FIT range')
    scaled=math.floor(value*scale+0.5)
    if not 1<=scaled<=MAX_UINT32_VALUE:
        raise ValueError(label+' is outside the FIT encoding range')
    return value


def validate_workout(workout):
    """Return an independent normalized copy, or reject incomplete instructions."""
    _object(workout,{'name','sport','steps'})
    name=_name(workout['name'],True)
    if workout['sport']!='running':raise ValueError('Only running FIT workouts are supported')
    if not isinstance(workout['steps'],list) or not 1<=len(workout['steps'])<=MAX_STEPS:
        raise ValueError('Workout must contain 1-50 explicit flat steps')
    steps=[]
    for index,step in enumerate(workout['steps']):
        try:
            _object(step,{'intensity','duration','target'},{'name','notes'})
            if step['intensity'] not in INTENSITIES:raise ValueError('Unknown workout step intensity')
            duration=step['duration'];target=step['target']
            if not isinstance(duration,dict):raise ValueError('Step duration must be explicit')
            kind=duration.get('type')
            if kind=='time':
                _object(duration,{'type','seconds'})
                clean_duration={'type':'time','seconds':_scaled(duration['seconds'],1000,'Duration seconds')}
            elif kind=='distance':
                _object(duration,{'type','metres'})
                clean_duration={'type':'distance','metres':_scaled(duration['metres'],100,'Duration metres')}
            elif kind=='open':
                _object(duration,{'type'});clean_duration={'type':'open'}
            else:raise ValueError('Unsupported step duration; use time, distance or open')
            if not isinstance(target,dict):raise ValueError('Step target must be explicit')
            kind=target.get('type')
            if kind=='open':
                _object(target,{'type'});clean_target={'type':'open'}
            elif kind=='heart_rate':
                _object(target,{'type','lowBpm','highBpm'})
                low,high=target['lowBpm'],target['highBpm']
                if (isinstance(low,bool) or isinstance(high,bool)
                        or not isinstance(low,(int,float)) or not isinstance(high,(int,float))
                        or not 1<=low<=high<=255 or low!=math.floor(low) or high!=math.floor(high)):
                    raise ValueError('Heart-rate bounds must be integer bpm in ascending order, 1-255')
                clean_target={'type':'heart_rate','lowBpm':int(low),'highBpm':int(high)}
            elif kind=='speed':
                _object(target,{'type','lowMps','highMps'})
                low=_scaled(target['lowMps'],1000,'Lower speed m/s')
                high=_scaled(target['highMps'],1000,'Upper speed m/s')
                if low>high:raise ValueError('Speed bounds must be in ascending m/s order')
                clean_target={'type':'speed','lowMps':low,'highMps':high}
            else:raise ValueError('Unsupported target; use open, heart_rate or speed')
            clean={'intensity':step['intensity'],'duration':clean_duration,'target':clean_target}
            if 'name' in step:clean['name']=_name(step['name'])
            if 'notes' in step:clean['notes']=_notes(step['notes'])
            steps.append(clean)
        except (ValueError,TypeError) as exc:
            raise ValueError(f'Workout step {index+1}: {exc}') from None
    return {'name':name,'sport':'running','steps':steps}
