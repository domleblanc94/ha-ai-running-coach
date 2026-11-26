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
}
STREAM_FIELDS = {'time': 'time_s', 'heartrate': 'heartrate_bpm',
    'velocity_smooth': 'speed_m_s', 'watts': 'power_watts', 'cadence': 'cadence',
    'altitude': 'elevation_m', 'distance': 'distance_m', 'grade_smooth': 'grade_pct', 'moving': 'moving'}
SCALARS = ('id', 'name', 'description', 'type', 'normalized_type', 'start_date',
    'source_start_date_utc', 'distance_m', 'moving_time_s', 'timer_duration_s', 'elapsed_time_s',
    'elevation_gain_m', 'calories', 'perceived_exertion', 'avg_heartrate', 'max_heartrate',
    'avg_watts', 'weighted_avg_watts', 'training_load', 'cardiac_drift_pct', 'drift_validity',
    'hr_zone_basis', *(f'hr_zone_{i}_pct' for i in range(1, 6)), 'ai_feedback', 'notes')
TEXT = {'name', 'description', 'type', 'normalized_type', 'start_date', 'source_start_date_utc',
    'drift_validity', 'hr_zone_basis', 'ai_feedback', 'notes'}
PROVENANCE = ('source', 'source_activity_id', 'metrics_source', 'stream_source', 'imported_at')
JSON_COLUMNS = ('raw_splits_json', 'downsampled_streams_json', 'hr_zone_seconds_json', 'provider_metrics_json', 'hr_zones_json')
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
    'training_load': 'provider load; scale not established', 'moving': 'fraction of bucket samples flagged moving (0-1)'}


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
                    clean[target] = numeric(row[source])
            # Garmin summary types are codes, not arbitrary provider strings.
            if mapping is SPLIT_FIELDS and row.get('splitType') in {
                'INTERVAL_ACTIVE', 'INTERVAL_REST', 'INTERVAL_WARMUP', 'INTERVAL_COOLDOWN',
                'RUN', 'WALK', 'REST', 'RUN_WALK', 'INTERVAL_RECOVERY', 'MARKER_KILOMETER', 'MARKER_MILE'}:
                clean['split_type'] = row['splitType']
        result['items'].append(clean)
    if offset + limit < len(rows):
        result['next_offset'] = offset + limit
    return result



def zone_configuration(value):
    status, stored = stored_json(value)
    result = {'status': status, 'configurations': [], 'units': 'bpm; upper bound -1 means open-ended in offline zones'}
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


def activity_details(conn, payload, error):
    allowed = {'activity_id', 'include_streams', 'offset', 'limit'}
    if not isinstance(payload, dict) or 'activity_id' not in payload or payload.keys() - allowed:
        raise error('invalid_input')
    identifier, include = payload['activity_id'], payload.get('include_streams', False)
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
        'hr_zone_configuration': zone_configuration(row.get('hr_zones_json')),
        'provider_effort': {'status': effort_status if effort is not None or effort_status != 'available' else 'invalid_stored_data',
                            'values': effort, 'scale': 'not established; not self-reported RPE'},
        'splits': splits, 'laps': {'status': 'not_stored', 'items': []}, 'streams': streams,
        'limitations': ['Only saved approved-source data; no refresh or AI request was made.',
            'Null scalar values are unknown or not stored, never zero by inference.',
            'Splits preserve stored order and may be grouped provider split summaries, not individual laps.',
            'No separate lap records or raw streams are retained by the current ingestion schema.',
            'Streams are stored 30-second bucket averages, not raw samples; cadence units depend on source.',
            'offset and limit apply independently to both splits and streams; follow each next_offset separately.',
            'GPS, coordinates, arbitrary provider extensions and credential fields are excluded.']}
