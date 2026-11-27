"""Bounded original FIT decoding; only non-location activity evidence survives."""
from datetime import datetime, timezone
from io import BytesIO
import math
from statistics import mean
from zipfile import ZipFile, BadZipFile

MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_FIT_BYTES = 64 * 1024 * 1024
MAX_FRAMES = 2000000
MAX_RECORDS = 200000
MAX_TIMER_EVENTS = 20000
MAX_MESSAGES = 250000
MAX_DURATION_S = 7 * 86400


def numeric(value):
    if isinstance(value, bool): return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError): return None


def decode_activity_archive(content):
    """Do not extract or retain the archive, FIT file, coordinates or unknown fields."""
    if not isinstance(content, bytes) or len(content) > MAX_ARCHIVE_BYTES:
        raise ValueError('Garmin activity archive exceeds the supported size.')
    try:
        with ZipFile(BytesIO(content)) as archive:
            members = archive.infolist()
            fits = [m for m in members if m.filename.lower().endswith('.fit') and not m.is_dir()]
            if len(members) > 16 or len(fits) != 1 or sum(m.file_size for m in members) > MAX_FIT_BYTES:
                raise ValueError('Garmin activity archive must contain one bounded FIT activity.')
            member = fits[0]
            if member.flag_bits & 1 or member.file_size > MAX_FIT_BYTES:
                raise ValueError('Garmin activity FIT is encrypted or exceeds the supported size.')
            with archive.open(member) as handle:
                raw = handle.read(MAX_FIT_BYTES + 1)
            if len(raw) > MAX_FIT_BYTES: raise ValueError('Garmin activity FIT exceeds the supported size.')
    except (BadZipFile, RuntimeError, OSError):
        raise ValueError('Garmin activity archive is invalid; previous evidence was retained.') from None
    return decode_fit(raw)


def decode_fit(raw):
    try: import fitdecode
    except ImportError:
        raise ValueError('Install the configured fitdecode dependency before backfilling activity details.') from None
    messages = []
    allowed = {
        'record': {'timestamp','distance','altitude','enhanced_altitude','heart_rate','speed','enhanced_speed'},
        'event': {'timestamp','event','event_type'},
        'lap': {'message_index','start_time','timestamp','total_distance','total_elapsed_time','total_timer_time',
                'total_moving_time','total_ascent','total_descent','avg_heart_rate'},
        'session': {'start_time','timestamp','total_elapsed_time','time_in_hr_zone'},
        'time_in_zone': {'reference_mesg','reference_index','time_in_hr_zone','hr_zone_high_boundary'},
        'hr_zone': {'message_index','high_bpm'},
        'zones_target': {'hr_calc_type','max_heart_rate','threshold_heart_rate'}}
    try:
        with fitdecode.FitReader(BytesIO(raw), check_crc=fitdecode.CrcCheck.RAISE,
                                 error_handling=fitdecode.ErrorHandling.RAISE) as reader:
            for count, frame in enumerate(reader):
                if count > MAX_FRAMES: raise ValueError('FIT frame limit exceeded.')
                if frame.frame_type != fitdecode.FIT_FRAME_DATA or frame.name not in allowed: continue
                # Select before retaining; no GPS field enters the decoded result.
                fields = {f.name:f.value for f in frame.fields if f.name in allowed[frame.name]}
                messages.append((frame.name, fields))
                if len(messages)>MAX_MESSAGES:raise ValueError('FIT retained-message limit exceeded.')
    except Exception as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)): raise
        raise ValueError('Garmin activity FIT could not be validated; previous evidence was retained.') from None
    return project_messages(messages)


def project_messages(messages):
    """Project decoded FIT messages. Timer events describe timer state, not speed inference."""
    records = [m for name,m in messages if name == 'record' and isinstance(m.get('timestamp'), datetime)]
    sessions = [m for name,m in messages if name == 'session']
    if len(records)>MAX_RECORDS:raise ValueError('Garmin FIT record limit exceeded.')
    if not records: raise ValueError('Garmin FIT has no timestamped records; previous evidence was retained.')
    if len(sessions) != 1: raise ValueError('Only single-session FIT activities are supported for detail backfill.')
    session = sessions[0]
    origin = session.get('start_time')
    if not isinstance(origin, datetime): raise ValueError('Garmin FIT is missing its session start time.')
    def elapsed(value):
        return (value-origin).total_seconds() if isinstance(value, datetime) else None
    records.sort(key=lambda m:m['timestamp'])
    duration = numeric(session.get('total_elapsed_time'))
    end = max(elapsed(records[-1]['timestamp']), duration or 0)
    if not 0 <= end <= MAX_DURATION_S: raise ValueError('Garmin FIT duration is outside supported bounds.')
    events = []
    for name,m in messages:
        if name == 'event' and m.get('event') == 'timer' and elapsed(m.get('timestamp')) is not None:
            state = True if m.get('event_type') in ('start','begin') else False if m.get('event_type') in ('stop','stop_all','stop_disable','stop_disable_all','end','end_all') else None
            if state is not None: events.append((elapsed(m['timestamp']),state))
    if len(events)>MAX_TIMER_EVENTS:raise ValueError('Garmin FIT timer-event limit exceeded.')
    events.sort(key=lambda p:p[0])
    bucket_count=int(end//30)+1
    timer_known=[0.0]*bucket_count
    timer_running=[0.0]*bucket_count
    def add_interval(start, stop, state):
        # One sweep across disjoint intervals: total work O(events + buckets).
        if state is None:return
        while start<stop:
            index=int(start//30)
            boundary=min(stop,(index+1)*30)
            timer_known[index]+=boundary-start
            if state:timer_running[index]+=boundary-start
            start=boundary
    cursor,state=0.0,None
    for t,next_state in events:
        if t<0:
            state=next_state
            continue
        if t>end:break
        add_interval(cursor,t,state)
        cursor,state=t,next_state
    add_interval(cursor,end,state)
    buckets = {}
    previous_distance = None
    for m in records:
        t = elapsed(m['timestamp'])
        if t < 0: raise ValueError('Garmin FIT records precede the session start.')
        row = buckets.setdefault(int(t//30), {})
        for source,target in [('heart_rate','heartrate'),('altitude','altitude'),('enhanced_altitude','altitude'),('speed','velocity_smooth'),('enhanced_speed','velocity_smooth')]:
            if source in ('altitude','speed') and m.get('enhanced_'+source) is not None: continue
            value = numeric(m.get(source))
            if value is not None and (target == 'altitude' or value >= 0): row.setdefault(target,[]).append(value)
        distance = numeric(m.get('distance'))
        if distance is not None and distance >= 0:
            if previous_distance is not None and distance < previous_distance:
                raise ValueError('Garmin FIT cumulative distance decreases; previous evidence was retained.')
            previous_distance = distance
            row['distance'] = distance
    series = []
    for index in range(int(end//30)+1):
        values = buckets.get(index,{})
        row = {'time':index*30}
        for field in ('heartrate','altitude','velocity_smooth'):
            row[field] = round(mean(values[field]),3) if values.get(field) else None
        row['distance'] = values.get('distance')
        interval=min(30,end-index*30)
        fraction=timer_running[index]/interval if interval>0 and abs(timer_known[index]-interval)<.001 else None
        row['timer_running_fraction'] = round(fraction,6) if fraction is not None else None
        row['timer_state'] = 'unknown' if fraction is None else 'running' if fraction == 1 else 'paused' if fraction == 0 else 'mixed'
        # A running timer can include standing at an aid station. Physical motion
        # is not established by timer events, and speed samples are not interval coverage.
        row['moving_fraction'] = None
        row['moving'] = None
        row['moving_state'] = 'unknown'
        series.append(row)
    laps, cumulative = [], 0
    ordered_laps=sorted([m for name,m in messages if name=='lap'],key=lambda m:elapsed(m.get('start_time')) if elapsed(m.get('start_time')) is not None else math.inf)
    for m in ordered_laps:
        start = elapsed(m.get('start_time'))
        distance = numeric(m.get('total_distance'))
        lap = {'lap_index':len(laps)+1,'start_time':m['start_time'].isoformat() if isinstance(m.get('start_time'),datetime) else None,
               'start_time_s':start,'start_distance_m':cumulative,'distance_m':distance}
        for source,target in [('total_elapsed_time','elapsed_time_s'),('total_timer_time','timer_duration_s'),
                              ('total_moving_time','moving_time_s'),('total_ascent','elevation_gain_m'),
                              ('total_descent','elevation_loss_m'),('avg_heart_rate','avg_heartrate')]:
            lap[target] = numeric(m.get(source))
        laps.append(lap)
        cumulative = cumulative+distance if cumulative is not None and distance is not None else None
    laps.sort(key=lambda lap:lap['start_time_s'] if lap['start_time_s'] is not None else math.inf)
    for index,lap in enumerate(laps,1): lap['lap_index'] = index
    # FIT hr_zone reports upper bounds. Keep that convention explicit; do not invent lower bounds.
    boundaries = []
    for name,m in messages:
        if name == 'hr_zone' and numeric(m.get('high_bpm')) is not None:
            index = m.get('message_index')
            if isinstance(index,int): boundaries.append({'zone':(index & 0xfff)+1,'max_bpm':numeric(m['high_bpm'])})
    boundaries.sort(key=lambda b:b['zone'])
    seconds = session.get('time_in_hr_zone')
    zone_messages=[m for name,m in messages if name=='time_in_zone' and m.get('reference_mesg') in ('session',18) and m.get('reference_index') in (None,0)]
    if len(zone_messages)==1:
        used=zone_messages[0]
        upper=used.get('hr_zone_high_boundary')
        if isinstance(upper,(list,tuple)):
            boundaries=[{'zone':i+1,'max_bpm':numeric(value)} for i,value in enumerate(upper) if numeric(value) is not None]
        seconds=used.get('time_in_hr_zone',seconds)
    if not isinstance(seconds,(list,tuple)): seconds = []
    zones = {'source':'garmin_activity_fit','status':'recorded' if boundaries else 'not_recorded',
             'basis':'fit_hr_zone_upper_bounds','boundaries':boundaries,
             'seconds_basis':'fit_array_index_unmapped',
             'source_index_times_s':[numeric(v) for v in seconds]}
    return {'rows':series,'laps':laps,'activity_hr_zones':zones,
            'metadata':{'source':'garmin_activity_fit','bucket_seconds':30,'sample_count':len(records),
                        'bucket_count':len(series),'elapsed_duration_s':end,'time_basis':'elapsed_bucket_start',
                        'distance_basis':'last_cumulative_sample','moving_basis':'not_observed','timer_basis':'fit_timer_events',
                        'activity_start_utc':origin.replace(tzinfo=timezone.utc).isoformat() if origin.tzinfo is None else origin.astimezone(timezone.utc).isoformat()}}
