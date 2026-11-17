"""Bounded private GPX parsing and descriptive course measurements; no pacing policy."""
import hashlib
import json
import math
import statistics
import xml.etree.ElementTree as ET

MAX_GPX_BYTES = 5 * 1024 * 1024
MAX_POINTS = 50000
MAX_SECTIONS = 12


def _tag(element):
    return element.tag.rsplit('}', 1)[-1]


def _number(value, lower, upper, label):
    if isinstance(value, bool):
        raise ValueError('GPX ' + label + ' must be numeric, not boolean.')
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError('GPX ' + label + ' must be a finite number.') from None
    if not math.isfinite(number) or not lower <= number <= upper:
        raise ValueError('GPX ' + label + ' is outside the supported range.')
    return number


def _distance(a, b):
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat, dlon = lat2 - lat1, math.radians(b['lon'] - a['lon'])
    value = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1.0, max(0.0, value))))


def _text(value, maximum=120):
    value = str(value or '').strip()
    return ''.join(char for char in value if ord(char) >= 32 and ord(char) != 127)[:maximum]


def _smooth_elevations(points):
    """Distance-based filtering prevents dense GPS jitter being counted as hills.

    Known contiguous spans are sampled every 25 m, median-filtered over 5 samples,
    then use a 5 m vertical deadband. Missing heights/segment gaps stay missing.
    """
    result = [None] * len(points)
    start = 0
    while start < len(points):
        if points[start]['elevation_m'] is None:
            start += 1
            continue
        end = start + 1
        while end < len(points) and points[end]['segment'] == points[start]['segment'] and points[end]['elevation_m'] is not None:
            end += 1
        part = points[start:end]
        length = part[-1]['distance_m'] - part[0]['distance_m']
        if length <= 0:
            result[start:end] = [point['elevation_m'] for point in part]
            start = end
            continue
        distances = [part[0]['distance_m'] + offset for offset in range(0, math.ceil(length), 25)]
        distances.append(part[-1]['distance_m'])
        sampled, cursor = [], 1
        for distance in distances:
            while cursor < len(part) - 1 and part[cursor]['distance_m'] < distance:
                cursor += 1
            a, b = part[cursor - 1], part[cursor]
            span = b['distance_m'] - a['distance_m']
            fraction = max(0, min(1, (distance - a['distance_m']) / span)) if span else 0
            sampled.append(a['elevation_m'] + fraction * (b['elevation_m'] - a['elevation_m']))
        filtered = [statistics.median(sampled[max(0, i - 2):min(len(sampled), i + 3)]) for i in range(len(sampled))]
        filtered[0], filtered[-1] = sampled[0], sampled[-1]
        anchor = filtered[0]
        for i in range(1, len(filtered) - 1):
            if abs(filtered[i] - anchor) >= 5:
                anchor = filtered[i]
            filtered[i] = anchor
        cursor = 1
        for index, point in enumerate(part):
            while cursor < len(distances) - 1 and distances[cursor] < point['distance_m']:
                cursor += 1
            span = distances[cursor] - distances[cursor - 1]
            fraction = max(0, min(1, (point['distance_m'] - distances[cursor - 1]) / span)) if span else 0
            result[start + index] = filtered[cursor - 1] + fraction * (filtered[cursor] - filtered[cursor - 1])
        start = end
    return result


def _measure(points, name, sport):
    distance, discontinuities, max_gap = 0.0, 0, 0.0
    elevations = []
    for index, point in enumerate(points):
        if index:
            previous = points[index - 1]
            gap = _distance(previous, point)
            if point['segment'] != previous['segment']:
                if gap > 1:
                    discontinuities += 1
            else:
                distance += gap
                max_gap = max(max_gap, gap)
        point['distance_m'] = round(distance, 3)
        elevations.append(point['elevation_m'])
    if distance < 10 or distance > 1000000:
        raise ValueError('GPX must describe between 10 metres and 1,000 kilometres of recorded route.')
    smooth = _smooth_elevations(points)
    noisy = any(a is not None and b is not None and abs(a - b) > 10 for a, b in zip(elevations, smooth))
    count = min(MAX_SECTIONS, max(2, math.ceil(distance / 1000)))
    sections = [{'id': index + 1, 'start_m': round(distance * index / count, 2),
                 'end_m': round(distance * (index + 1) / count, 2),
                 'distance_m': round(distance / count, 2), '_gain': 0.0, '_loss': 0.0,
                 '_known': 0.0, '_net': 0.0, '_missing': False} for index in range(count)]
    known_distance, gain, loss = 0.0, 0.0, 0.0
    for index in range(1, len(points)):
        previous, current = points[index - 1], points[index]
        if previous['segment'] != current['segment']:
            continue
        length = current['distance_m'] - previous['distance_m']
        if length <= 0:
            continue
        known = smooth[index - 1] is not None and smooth[index] is not None
        delta = smooth[index] - smooth[index - 1] if known else 0
        if known:
            known_distance += length
            gain += max(delta, 0)
            loss += max(-delta, 0)
        for section in sections:
            overlap = max(0, min(current['distance_m'], section['end_m']) - max(previous['distance_m'], section['start_m']))
            if overlap and known:
                section['_known'] += overlap
                section['_gain'] += max(delta, 0) * overlap / length
                section['_loss'] += max(-delta, 0) * overlap / length
                section['_net'] += delta * overlap / length
            elif overlap:
                section['_missing'] = True
    coverage = min(1.0, known_distance / distance)
    for section in sections:
        complete = not section['_missing'] and section['_known'] >= section['distance_m'] - .05
        section.update(ascent_m=round(section['_gain'], 1) if complete else None,
                       descent_m=round(section['_loss'], 1) if complete else None,
                       net_grade_pct=round(100 * section['_net'] / section['distance_m'], 2) if complete else None,
                       elevation_coverage=round(min(1.0, section['_known'] / section['distance_m']), 3))
        for key in ('_known', '_gain', '_loss', '_net', '_missing'):
            del section[key]
    warnings = ['Distances follow the uploaded GPS polyline; surfaces, weather and aid-station delays are not inferred.',
                'Elevation uses 25 m distance sampling, five-sample median smoothing and a 5 m vertical deadband. Small features can be lost; it is not terrain-corrected.']
    complete_elevation = all(value is not None for value in elevations)
    if not complete_elevation:
        warnings.append('Elevation is missing on part or all of the course. Total ascent/descent are unknown; missing height is not flat terrain.')
    if noisy:
        warnings.append('Isolated elevation spikes were smoothed. Climb totals remain approximate; check the course source.')
    if discontinuities:
        warnings.append('The GPX has disconnected track segments. Missing gaps are not joined; upload a continuous course before pacing or FIT export.')
    if max_gap > 500:
        warnings.append('GPS points are sparse in places; distance and gradients between those points are approximate.')
    summary = dict(distance_m=round(distance, 2), ascent_m=round(gain, 1) if complete_elevation else None,
                   descent_m=round(loss, 1) if complete_elevation else None, elevation_coverage=round(coverage, 3),
                   point_count=len(points), segment_count=len({p['segment'] for p in points}),
                   discontinuities=discontinuities, max_point_gap_m=round(max_gap, 2), warnings=warnings, sections=sections)
    summary['missing_elevation_points'] = sum(value is None for value in elevations)
    summary['derived_elevation_points'] = 0
    digest = hashlib.sha256(json.dumps({'sport': sport, 'points': points}, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    # Include endpoints; presentation must use segment IDs rather than connect separate parts.
    indices = sorted({round(i * (len(points) - 1) / min(255, len(points) - 1)) for i in range(min(256, len(points)))})
    preview = []
    previous_index = None
    for index in indices:
        height = smooth[index]
        if previous_index is not None and points[previous_index]['segment'] == points[index]['segment']:
            # A small missing-height span can fall between sampled preview points.
            # Preserve that visual gap rather than imply known terrain across it.
            if any(value is None for value in smooth[previous_index:index + 1]):
                height = None
        preview.append({**points[index], 'elevation_m': height})
        previous_index = index
    return {'schema_version': 1, 'route_hash': digest, 'name': _text(name) or 'Uploaded race course',
            'sport': sport, 'points': points, 'summary': summary, 'preview': preview}


def parse_gpx(data, *, name='', sport='running'):
    if sport != 'running':
        raise ValueError('Race planning currently supports running and trail-running GPX courses.')
    if isinstance(data, str):
        try:
            data = data.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('GPX must use valid UTF-8 text.') from None
    if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_GPX_BYTES:
        raise ValueError('Upload a GPX file no larger than 5 MiB.')
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError('GPX must use UTF-8 text encoding.') from None
    if '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        raise ValueError('GPX document types and entities are not supported.')
    if text.count('<') > MAX_POINTS * 10:
        raise ValueError('GPX has too many XML elements; simplify it before uploading.')
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        raise ValueError('The uploaded file is not valid GPX XML.') from None
    if _tag(root) != 'gpx':
        raise ValueError('The uploaded XML must have a GPX root.')
    groups = []
    for track in root:
        if _tag(track) == 'trk':
            groups.extend([list(segment) for segment in track if _tag(segment) == 'trkseg'])
    if not groups:
        groups = [list(route) for route in root if _tag(route) == 'rte']
    points = []
    for segment, group in enumerate(groups):
        for point in group:
            if _tag(point) not in ('trkpt', 'rtept'):
                continue
            if len(points) >= MAX_POINTS:
                raise ValueError('GPX has more than 50,000 points; simplify the course before uploading.')
            elevation = next((child.text for child in point if _tag(child) == 'ele'), None)
            points.append({'lat': _number(point.get('lat'), -90, 90, 'latitude'),
                           'lon': _number(point.get('lon'), -180, 180, 'longitude'),
                           'elevation_m': _number(elevation, -500, 9000, 'elevation') if elevation is not None else None,
                           'segment': segment})
    if len(points) < 2:
        raise ValueError('GPX needs at least two track or route points.')
    found_name = next((child.text for item in root if _tag(item) in ('trk', 'rte') for child in item if _tag(child) == 'name'), '')
    return _measure(points, name or found_name, sport)


def validate_route(route):
    """Recompute metrics/hash from private points rather than trust uploaded JSON metrics."""
    if not isinstance(route, dict) or route.get('schema_version') != 1 or route.get('sport') != 'running':
        raise ValueError('Unsupported race course.')
    raw = route.get('points')
    if not isinstance(raw, list) or not 2 <= len(raw) <= MAX_POINTS:
        raise ValueError('Race course must contain 2–50,000 points.')
    clean = []
    previous_segment = -1
    for point in raw:
        if not isinstance(point, dict):
            raise ValueError('Race course points must be objects.')
        segment = point.get('segment')
        if type(segment) is not int or segment < 0 or segment < previous_segment or segment > MAX_POINTS:
            raise ValueError('Race course segments must be ordered.')
        previous_segment = segment
        elevation = point.get('elevation_m')
        clean.append({'lat': _number(point.get('lat'), -90, 90, 'latitude'), 'lon': _number(point.get('lon'), -180, 180, 'longitude'),
                      'elevation_m': _number(elevation, -500, 9000, 'elevation') if elevation is not None else None, 'segment': segment})
    measured = _measure(clean, route.get('name'), 'running')
    if 'elevation_enrichment' in route:
        from race_elevation import validate_enrichment
        return validate_enrichment(route, measured)
    if route.get('route_hash') != measured['route_hash']:
        raise ValueError('Race course changed; upload it again before planning.')
    return measured
