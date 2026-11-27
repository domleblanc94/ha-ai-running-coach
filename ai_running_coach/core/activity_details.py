"""Allowlisted, offline activity detail projection; never returns raw provider JSON."""
import json
import math

# Numeric keys only: unknown provider extensions and coordinates cannot escape.
SPLIT_FIELDS = {
    'split': 'index', 'splitIndex': 'index', 'lapIndex': 'index',
    'noOfSplits': 'split_count', 'distance': 'distance_m', 'distance_m': 'distance_m',
    'elapsed_time': 'elapsed_time_s', 'elapsedDuration': 'elapsed_time_s',
    'moving_time': 'moving_time_s', 'movingDuration': 'moving_time_s',
    'duration': 'timer_duration_s', 'timerDuration': 'timer_duration_s',
    'average_heartrate': 'avg_heartrate', 'averageHR': 'avg_heartrate', 'maxHR': 'max_heartrate',
    'average_speed': 'avg_speed_m_s', 'averageSpeed': 'avg_speed_m_s', 'maxSpeed': 'max_speed_m_s',
    'averagePower': 'avg_watts', 'maxPower': 'max_watts',
    'averageRunCadence': 'avg_run_cadence', 'averageCadence': 'avg_source_cadence',
    'elevation_difference': 'elevation_change_m', 'elevationGain': 'elevation_gain_m',
    'elevationLoss': 'elevation_loss_m', 'startTime': 'source_start_time',
    **{k:k for k in ('lap_index','start_time_s','start_distance_m','distance_m','elapsed_time_s','moving_time_s','timer_duration_s','elevation_gain_m','elevation_loss_m','avg_heartrate')},
}
STREAM_FIELDS = {'time': 'time_s', 'heartrate': 'heartrate_bpm',
    'velocity_smooth': 'speed_m_s', 'watts': 'power_watts', 'cadence': 'cadence',
    'moving_fraction': 'moving_fraction', 'timer_running_fraction': 'timer_running_fraction', 'altitude': 'elevation_m', 'distance': 'distance_m', 'grade_smooth': 'grade_pct', 'moving': 'moving'}
SCALARS = ('id', 'name', 'description', 'type', 'normalized_type', 'start_date',
    'source_start_date_utc', 'distance_m', 'moving_time_s', 'timer_duration_s', 'elapsed_time_s',
    'elevation_gain_m', 'calories', 'perceived_exertion', 'avg_heartrate', 'max_heartrate',
    'avg_watts', 'weighted_avg_watts', 'training_load', 'cardiac_drift_pct', 'drift_validity',
    'hr_zone_basis', *(f'hr_zone_{i}_pct' for i in range(1, 6)), 'ai_feedback', 'notes')
TEXT = {'name', 'description', 'type', 'normalized_type', 'start_date', 'source_start_date_utc',
    'drift_validity', 'hr_zone_basis', 'ai_feedback', 'notes'}
PROVENANCE = ('source', 'source_activity_id', 'metrics_source', 'stream_source', 'imported_at')
JSON_COLUMNS = ('raw_splits_json', 'downsampled_streams_json', 'hr_zone_seconds_json', 'provider_metrics_json', 'hr_zones_json', 'raw_laps_json', 'activity_hr_zones_json', 'stream_metadata_json')
STORED_JSON_LIMIT = 4 * 1024 * 1024
UNITS = {'distance_m': 'metres', 'moving_time_s': 'seconds', 'timer_duration_s': 'seconds; may include rests',
    'elapsed_time_s': 'seconds', 'elevation_gain_m': 'metres', 'elevation_loss_m': 'metres',
    'elevation_change_m': 'metres', 'elevation_m': 'metres', 'avg_heartrate': 'bpm',
    'max_heartrate': 'bpm', 'heartrate_bpm': 'bpm', 'avg_watts': 'watts', 'max_watts': 'watts',
    'weighted_avg_watts': 'watts', 'power_watts': 'watts', 'speed_m_s': 'metres/second',
    'avg_speed_m_s': 'metres/second', 'max_speed_m_s': 'metres/second',
    'avg_run_cadence': 'source running cadence units; not established', 'avg_source_cadence': 'source cadence units; not established',
    'cadence': 'source cadence units; not established', 'time_s': 'seconds from activity start',
    'source_start_time': 'source timestamp units; not established', 'grade_pct': 'percent',
    'hr_zone_seconds': 'seconds', 'hr_zone_percentages': 'percent; see hr_zone_basis',
    'calories': 'kcal', 'perceived_exertion': 'self-reported 1-10',
    'cardiac_drift_pct': 'percent; interpret only with drift_validity',
    'training_load': 'provider load; scale not established', 'moving': 'legacy fraction or boolean movement state; see stream metadata',
    'moving_fraction': 'fraction of observed physical-motion samples flagged moving (0-1); unknown without observation',
    'timer_running_fraction': 'fraction of known timer state flagged running (0-1); not physical movement',
    'start_time_s': 'seconds elapsed from activity start', 'start_distance_m': 'metres cumulative from activity start'}


def numeric(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def stored_json(value):
    if value == '__DETAIL_TOO_LARGE__':
        return 'too_large', None
    if value is None or value == '':
        return 'not_stored', None
    if not isinstance(value, str):
        return 'invalid_stored_data', None
    if len(value.encode('utf-8')) > STORED_JSON_LIMIT:
        return 'too_large', None
    try:
        return 'available', json.loads(value)
    except (ValueError, RecursionError):
        return 'invalid_stored_data', None


def page(value, mapping, offset, limit):
    status, rows = stored_json(value)
    result = {'status': status, 'items': [], 'total': None, 'offset': offset, 'limit': limit, 'next_offset': None}
    if status != 'available':
        return result
    if not isinstance(rows, list):
        result['status'] = 'invalid_stored_data'
        return result
    result['total'] = len(rows)
    result['status'] = 'available' if rows else 'not_stored'
    for index, row in enumerate(rows[offset:offset + limit], offset):
        clean = {'stored_index': index}
        if isinstance(row, dict):
            for source, target in mapping.items():
                if source in row:
                    clean[target] = row[source] if source == 'moving' and type(row[source]) is bool else numeric(row[source])
            if mapping is STREAM_FIELDS:
                if row.get('moving_state') in {'moving','paused','mixed','unknown'}:clean['moving_state'] = row['moving_state']
                if row.get('timer_state') in {'running','paused','mixed','unknown'}:clean['timer_state'] = row['timer_state']
            # Garmin summary types are codes, not arbitrary provider strings.
            if mapping is SPLIT_FIELDS and row.get('splitType') in {
                'INTERVAL_ACTIVE', 'INTERVAL_REST', 'INTERVAL_WARMUP', 'INTERVAL_COOLDOWN',
                'RUN', 'WALK', 'REST', 'RUN_WALK', 'INTERVAL_RECOVERY', 'MARKER_KILOMETER', 'MARKER_MILE'}:
                clean['split_type'] = row['splitType']
        result['items'].append(clean)
    if offset + limit < len(rows):
        result['next_offset'] = offset + limit
    return result



def zone_configuration(value, source=None, metrics_source=None):
    status, stored = stored_json(value)
    origin = metrics_source or source
    garmin = origin == 'garmin_personal'
    result = {'status': status, 'configurations': [], 'units': 'bpm; upper bound -1 means open-ended in offline zones',
              'scope': ('Garmin profile settings captured at sync when available; not established as historical activity bounds; not coach athlete.md zones'
                        if garmin else 'Saved source-import HR configuration; historical/activity-specific origin unknown; not coach athlete.md zones'),
              'configuration_role': 'garmin_profile_settings_at_sync' if garmin else 'imported_source_configuration',
              'source': origin, 'storage_field': 'hr_zones_json', 'coach_profile_substitution': False}
    if status != 'available':
        return result
    if isinstance(stored, list):
        records = stored
    elif isinstance(stored, dict) and isinstance(stored.get('official_configurations'), list):
        records = stored['official_configurations']
    elif isinstance(stored, dict) and isinstance(stored.get('heart_rate'), dict):
        rows = stored['heart_rate'].get('zones')
        if isinstance(rows, list):
            result['configurations'] = [{'zones': [{'min': numeric(z.get('min')), 'max': numeric(z.get('max'))}
                                        for z in rows[:5] if isinstance(z, dict)]}]
            return result
        records = None
    else:
        records = None
    if records is None:
        result['status'] = 'invalid_stored_data'
        return result
    for item in records[:20]:
        if not isinstance(item, dict):
            continue
        clean = {key: numeric(item[key]) for key in (
            'restingHeartRate', 'maxHeartRate', 'lactateThresholdHeartRate',
            *(f'zone{i}Floor' for i in range(1, 6))) if key in item}
        if item.get('sport') in {'RUNNING', 'CYCLING', 'SWIMMING', 'DEFAULT', 'ALL'}:
            clean['sport'] = item['sport']
        result['configurations'].append(clean)
    result['truncated'] = len(records) > 20
    if not records:
        result['status'] = 'not_stored'
    return result


def activity_zone_configuration(value):
    status, stored = stored_json(value)
    result = {'status': status, 'source': None, 'boundaries': [], 'units': 'bpm',
              'note': 'Activity-recorded bounds only; never substituted from current profile settings.'}
    if status != 'available':return result
    if not isinstance(stored, dict):
        result['status'] = 'invalid_stored_data'
        return result
    result['source'] = stored.get('source') if stored.get('source') in {'garmin_activity_fit','garmin_personal'} else None
    if stored.get('status') in {'recorded','not_recorded'}:result['recording_status'] = stored['status']
    boundaries = stored.get('boundaries') or []
    if not isinstance(boundaries,list):
        result['status'] = 'invalid_stored_data'
        return result
    if stored.get('seconds_basis') == 'fit_array_index_unmapped':
        result['seconds_basis'] = 'fit_array_index_unmapped'
        times = stored.get('source_index_times_s')
        if isinstance(times,list):result['source_index_times_s'] = [numeric(value) for value in times[:20]]
    if stored.get('basis') in {'fit_hr_zone_upper_bounds','fit_time_in_zone_hr_upper_bounds'}:
        result['basis'] = stored['basis']
    for row in boundaries[:10]:
        if isinstance(row,dict):result['boundaries'].append({key:numeric(row.get(key)) for key in ('zone','min_bpm','max_bpm')})
    return result


def stream_metadata(value):
    status, stored = stored_json(value)
    result = {'status': status}
    if status == 'available':
        if not isinstance(stored,dict):return {'status':'invalid_stored_data'}
        timestamp = stored.get('activity_start_utc')
        if isinstance(timestamp,str) and len(timestamp)<=40:
            from datetime import datetime, timezone
            try:
                parsed = datetime.fromisoformat(timestamp.replace('Z','+00:00'))
                if parsed.tzinfo is not None:result['activity_start_utc'] = parsed.astimezone(timezone.utc).isoformat()
            except ValueError:pass
        for key in ('bucket_seconds','sample_count','bucket_count','elapsed_duration_s'):
            if key in stored:result[key] = numeric(stored[key])
        # Fixed vocabularies prevent arbitrary provider extensions escaping.
        for key, choices in {'source':{'garmin_activity_fit','garmin_personal'},
                'time_basis':{'elapsed_bucket_start','elapsed_seconds'},
                'distance_basis':{'last_cumulative_sample','last_observed_cumulative'},
                'moving_basis':{'not_observed'},
                'timer_basis':{'fit_timer_events'},
                'aggregation':{'30_second_buckets'}}.items():
            if stored.get(key) in choices:result[key]=stored[key]
    return result


def activity_details(conn, payload, error):
    allowed = {'activity_id', 'source', 'source_activity_id', 'include_streams', 'offset', 'limit'}
    if not isinstance(payload, dict) or payload.keys() - allowed:
        raise error('invalid_input')
    identifier, include = payload.get('activity_id'), payload.get('include_streams', False)
    if 'source' in payload or 'source_activity_id' in payload:
        from source_policy import approved_activity_source
        source, external = payload.get('source'), payload.get('source_activity_id')
        if identifier is not None or not approved_activity_source(source) or not isinstance(external, str) or not external or len(external)>100:
            raise error('invalid_input')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(activities)')}
        if not {'source','source_activity_id'}.issubset(columns):
            raise error('not_found', 'Provider activity ID is not stored.')
        matches = conn.execute('SELECT id FROM activities WHERE source=? AND source_activity_id=? LIMIT 2', (source,external)).fetchall()
        if not matches:
            raise error('not_found', 'The requested provider activity was not found.')
        if len(matches)>1:
            raise error('conflict', 'The provider activity ID is ambiguous; use the internal activity ID.')
        identifier = matches[0][0]
    offset, limit = payload.get('offset', 0), payload.get('limit', 100)
    if (type(identifier) is not int or not -(2**63) <= identifier < 2**63 or identifier == 0
            or type(include) is not bool or type(offset) is not int or not 0 <= offset <= 1000000
            or type(limit) is not int or not 1 <= limit <= 200):
        raise error('invalid_input')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(activities)')}
    fields = [key for key in (*SCALARS, *PROVENANCE, *JSON_COLUMNS) if key in columns]
    # Avoid reading unbounded stored blobs into memory, even when not requested.
    expressions = []
    for key in fields:
        if key in JSON_COLUMNS:
            expressions.append(f"CASE WHEN length(CAST(\"{key}\" AS BLOB)) > {STORED_JSON_LIMIT} THEN '__DETAIL_TOO_LARGE__' ELSE \"{key}\" END")
        elif key in TEXT or key in PROVENANCE:
            expressions.append(f'substr("{key}",1,4000)')
        else:
            expressions.append(f'"{key}"')
    record = conn.execute('SELECT ' + ','.join(expressions) + ' FROM activities WHERE id=?', (identifier,)).fetchone()
    if record is None:
        raise error('not_found', 'The requested activity was not found.')
    row = dict(zip(fields, record))
    activity = {}
    for key in SCALARS:
        value = row.get(key)
        activity[key] = value[:4000] if key in TEXT and isinstance(value, str) else numeric(value)
    provenance = {key: str(row[key])[:200] if row.get(key) is not None else None for key in PROVENANCE}
    zones_status, zones = stored_json(row.get('hr_zone_seconds_json'))
    zones = {str(i): numeric(zones.get(str(i))) for i in range(1, 6)} if isinstance(zones, dict) else None
    if zones_status == 'available' and zones is None:
        zones_status = 'invalid_stored_data'
    splits = page(row.get('raw_splits_json'), SPLIT_FIELDS, offset, limit)
    streams = (page(row.get('downsampled_streams_json'), STREAM_FIELDS, offset, limit) if include else
               {'status': 'not_requested', 'items': [], 'total': None, 'offset': offset, 'limit': limit, 'next_offset': None})
    effort_status, effort = stored_json(row.get('provider_metrics_json'))
    effort = {key: numeric(effort.get(key)) for key in ('perceivedWorkoutEffort', 'directWorkoutFeel')} if isinstance(effort, dict) else None
    return {'activity': activity, 'provenance': provenance, 'units': UNITS,
        'unknown_fields': [key for key, value in activity.items() if value is None],
        'hr_zone_seconds': {'status': zones_status, 'values': zones},
        'hr_zone_configuration': zone_configuration(row.get('hr_zones_json'), row.get('source'), row.get('metrics_source')),
        'activity_hr_zone_configuration': activity_zone_configuration(row.get('activity_hr_zones_json')),
        'stream_metadata': stream_metadata(row.get('stream_metadata_json')),
        'provider_effort': {'status': effort_status if effort is not None or effort_status != 'available' else 'invalid_stored_data',
                            'values': effort, 'scale': 'not established; not self-reported RPE'},
        'splits': splits, 'laps': page(row.get('raw_laps_json'), SPLIT_FIELDS, offset, limit), 'streams': streams,
        'limitations': ['Only saved approved-source data; no refresh or AI request was made.',
            'Null scalar values are unknown or not stored, never zero by inference.',
            'Splits preserve stored order and may be grouped provider split summaries, not individual laps.',
            'Individual laps and activity-specific HR bounds are available only when retained; profile zones are separate.',
            'Streams use 30-second buckets; time is elapsed bucket start, cumulative distance is last observed; HR/elevation/speed are means when retained with metadata. Legacy streams may use bucket means for distance/time.',
            'offset and limit apply independently to splits, laps and streams; follow each next_offset separately.',
            'Timer state/fraction reflect recorded timer events, not physical movement. Physical movement remains unknown without a separate observation; inspect recorded speed/distance for slowing or stopping.',
            'GPS, coordinates, arbitrary provider extensions and credential fields are excluded.']}
