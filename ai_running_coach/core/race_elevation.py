"""Bounded map-derived surface heights; no model calls or training decisions."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

PROVIDER = 'Open-Meteo'
DATASET = 'Copernicus DEM 2021 GLO-90'
ALGORITHM = 'missing-only-segment-linear-v1'
ENDPOINT = 'https://api.open-meteo.com/v1/elevation'
SOURCE_URL = 'https://open-meteo.com/en/docs/elevation-api'
LICENCE_URL = 'https://creativecommons.org/licenses/by/4.0/'
COPERNICUS_URL = 'https://doi.org/10.5270/ESA-c5d3d65'
MAX_SAMPLES = 500
BATCH_SIZE = 100
MAX_RESPONSE_BYTES = 64 * 1024
REQUEST_TIMEOUT = 10
TOTAL_TIMEOUT = 55
CACHE_VERSION = 1
MAX_CACHE_FILES = 64


class ElevationError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _height(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or not -500 <= value <= 9000:
        raise ValueError('Map elevations must be finite heights in metres or null.')
    return float(value)


def _segments(points):
    groups = []
    for index, point in enumerate(points):
        if not groups or points[groups[-1][-1]]['segment'] != point['segment']:
            groups.append([])
        groups[-1].append(index)
    return groups


def _sampling(route):
    points = route['points']
    groups = [group for group in _segments(points) if any(points[i]['elevation_m'] is None for i in group)]
    minimum = sum(min(2, len(group)) for group in groups)
    if minimum > MAX_SAMPLES:
        raise ElevationError('elevation_too_fragmented')
    length = sum(points[group[-1]]['distance_m'] - points[group[0]]['distance_m'] for group in groups)
    spacing = max(90.0, length / max(1, MAX_SAMPLES - minimum))
    while True:
        selected = []
        for group in groups:
            selected.append(group[0])
            anchor = points[group[0]]['distance_m']
            for index in group[1:-1]:
                if points[index]['distance_m'] - anchor >= spacing:
                    selected.append(index)
                    anchor = points[index]['distance_m']
            if group[-1] != group[0]:
                selected.append(group[-1])
        if len(selected) <= MAX_SAMPLES:
            return selected, round(spacing, 3)
        spacing *= 1.05


def _ranges(indices):
    result = []
    for index in indices:
        if result and result[-1][1] + 1 == index:
            result[-1][1] = index
        else:
            result.append([index, index])
    return result


def _apply_samples(original, samples):
    points = copy.deepcopy(original['points'])
    by_segment = {}
    for index, height in samples:
        by_segment.setdefault(points[index]['segment'], []).append((index, height))
    filled = []
    for group in _segments(points):
        anchors = by_segment.get(points[group[0]]['segment'], [])
        if not anchors:
            continue
        cursor = 0
        for index in group:
            if points[index]['elevation_m'] is not None:
                continue
            while cursor + 1 < len(anchors) and anchors[cursor + 1][0] <= index:
                cursor += 1
            left, low = anchors[cursor]
            height = None
            if left == index:
                height = low
            elif cursor + 1 < len(anchors):
                right, high = anchors[cursor + 1]
                span = points[right]['distance_m'] - points[left]['distance_m']
                if low is not None and high is not None and span > 0:
                    fraction = (points[index]['distance_m'] - points[left]['distance_m']) / span
                    height = low + (high - low) * fraction
                elif low is not None and high == low:
                    height = low
            if height is not None:
                points[index]['elevation_m'] = round(height, 3)
                filled.append(index)
    return points, _ranges(filled)


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        raise ValueError('Invalid elevation retrieval timestamp.')
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Invalid elevation retrieval timestamp.') from None
    if stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
        raise ValueError('Elevation retrieval timestamp must be UTC.')
    return value


def _decorate(original, samples, spacing, retrieved_at):
    from race_gpx import _measure
    points, filled = _apply_samples(original, samples)
    result = _measure(points, original['name'], original['sport'])
    provenance = {'version': 1, 'provider': PROVIDER, 'dataset': DATASET, 'algorithm': ALGORITHM,
                  'source_route_hash': original['route_hash'], 'resolution_m': 90,
                  'sample_spacing_m': spacing, 'samples': copy.deepcopy(samples),
                  'filled_ranges': filled, 'retrieved_at': _timestamp(retrieved_at)}
    stable = {key: value for key, value in provenance.items() if key != 'retrieved_at'}
    result['route_hash'] = _hash({'measured_route_hash': result['route_hash'], 'elevation_enrichment': stable})
    result['elevation_enrichment'] = provenance
    count = sum(end - start + 1 for start, end in filled)
    source = {'provider': PROVIDER, 'dataset': DATASET, 'source_type': 'digital_surface_model',
              'resolution_m': 90, 'sample_spacing_m': spacing, 'sample_count': len(samples),
              'filled_points': count, 'original_coverage': original['summary']['elevation_coverage'],
              'coverage': result['summary']['elevation_coverage'], 'retrieved_at': retrieved_at,
              'attribution_url': SOURCE_URL, 'licence_url': LICENCE_URL,
              'copernicus_url': COPERNICUS_URL,
              'changes': 'Missing heights filled by segment-local interpolation of map samples; supplied GPX heights retained.'}
    result['summary']['elevation_source'] = source
    result['summary']['derived_elevation_points'] = count
    result['summary']['warnings'] = [warning.replace('it is not terrain-corrected.', 'totals are approximate.')
                                     for warning in result['summary']['warnings']]
    result['summary']['warnings'] += [
        'Map-derived surface elevations from Open-Meteo / Copernicus GLO-90 are approximate, not measured trail heights. Buildings, vegetation, bridges and tunnels can differ.',
        'Source grid resolution is about 90 m; requested sampling spacing is ' + str(spacing) + ' m. Sparse GPX points can make effective spacing larger.',
        'Existing GPX heights are retained; mixed height sources and vertical datums can introduce artificial steps at their boundaries.']
    return result


def validate_enrichment(route, measured):
    """Reconstruct original heights and derive every enriched value/metric again."""
    from race_gpx import _measure
    meta = route.get('elevation_enrichment')
    keys = {'version', 'provider', 'dataset', 'algorithm', 'source_route_hash', 'resolution_m',
            'sample_spacing_m', 'samples', 'filled_ranges', 'retrieved_at'}
    if not isinstance(meta, dict) or set(meta) != keys or type(meta['version']) is not int or type(meta['resolution_m']) is not int or (meta['version'], meta['provider'], meta['dataset'], meta['algorithm'], meta['resolution_m']) != (1, PROVIDER, DATASET, ALGORITHM, 90):
        raise ValueError('Unsupported map-elevation provenance.')
    if not isinstance(meta['source_route_hash'], str) or not re.fullmatch('[0-9a-f]{64}', meta['source_route_hash']):
        raise ValueError('Invalid original course identity.')
    ranges = meta['filled_ranges']
    if not isinstance(ranges, list) or not 1 <= len(ranges) <= len(measured['points']):
        raise ValueError('Invalid filled elevation ranges.')
    original_points = copy.deepcopy(measured['points'])
    previous = -1
    for pair in ranges:
        if not isinstance(pair, list) or len(pair) != 2 or any(type(value) is not int for value in pair):
            raise ValueError('Invalid filled elevation ranges.')
        start, end = pair
        if not previous < start <= end < len(original_points):
            raise ValueError('Filled elevation ranges must be ordered and disjoint.')
        for index in range(start, end + 1):
            if original_points[index]['elevation_m'] is None:
                raise ValueError('Filled elevation range includes an unknown height.')
            original_points[index]['elevation_m'] = None
        previous = end
    original = _measure(original_points, measured['name'], measured['sport'])
    if original['route_hash'] != meta['source_route_hash']:
        raise ValueError('Original GPX elevations or geometry changed.')
    indices, spacing = _sampling(original)
    raw = meta['samples']
    if not isinstance(raw, list) or len(raw) != len(indices) or type(meta['sample_spacing_m']) not in (int, float) or meta['sample_spacing_m'] != spacing:
        raise ValueError('Invalid elevation sampling provenance.')
    samples = []
    for index, sample in zip(indices, raw):
        if not isinstance(sample, list) or len(sample) != 2 or type(sample[0]) is not int or sample[0] != index:
            raise ValueError('Invalid elevation sample order.')
        samples.append([index, _height(sample[1])])
    result = _decorate(original, samples, spacing, meta['retrieved_at'])
    if result['points'] != measured['points'] or result['elevation_enrichment']['filled_ranges'] != ranges or result['route_hash'] != route.get('route_hash'):
        raise ValueError('Map-derived course changed; import the original GPX again.')
    return result


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(locations, timeout):
    # This is a checked lookup budget, not a guaranteed OS/DNS wall-clock cap.
    # HA also imposes a hard subprocess timeout. read1 avoids waiting to fill a
    # large buffer while a server trickles a chunked response indefinitely.
    deadline = time.monotonic() + timeout
    query = urllib.parse.urlencode({'latitude': ','.join(format(point['lat'], '.6f') for point in locations),
                                   'longitude': ','.join(format(point['lon'], '.6f') for point in locations)})
    request = urllib.request.Request(ENDPOINT + '?' + query, headers={'User-Agent': 'AI-Multisport-Coach elevation client', 'Accept': 'application/json'})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.status != 200:
                raise ElevationError('elevation_unavailable')
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise ElevationError('elevation_invalid_response')
            declared = response.headers.get('Content-Length')
            if declared is not None and (not declared.isdigit() or int(declared) > MAX_RESPONSE_BYTES):
                raise ElevationError('elevation_invalid_response')
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ElevationError('elevation_unavailable')
                chunk = response.read1(min(4096, MAX_RESPONSE_BYTES + 1 - len(body)))
                if time.monotonic() >= deadline:
                    raise ElevationError('elevation_unavailable')
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    raise ElevationError('elevation_invalid_response')
            if declared is not None and len(body) != int(declared):
                raise ElevationError('elevation_invalid_response')
        return json.loads(body.decode('utf-8'), parse_constant=lambda value: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    except urllib.error.HTTPError as exc:
        raise ElevationError('elevation_rate_limit' if exc.code == 429 else 'elevation_unavailable') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ElevationError('elevation_unavailable') from None
    except (ValueError, UnicodeError):
        raise ElevationError('elevation_invalid_response') from None


def _cache_key(route):
    return _hash([CACHE_VERSION, route['route_hash'], PROVIDER, DATASET, ALGORITHM])


def _read_cache(directory, original):
    from race_gpx import validate_route
    path = Path(directory) / (_cache_key(original) + '.json')
    try:
        if path.stat().st_size > 1024 * 1024:
            return None
        cached = json.loads(path.read_text(encoding='utf-8'))
        if set(cached) != {'source_route_hash', 'samples', 'sample_spacing_m', 'retrieved_at'} or cached['source_route_hash'] != original['route_hash']:
            return None
        indices, spacing = _sampling(original)
        if spacing != cached['sample_spacing_m'] or not isinstance(cached['samples'], list) or len(cached['samples']) != len(indices):
            return None
        samples = []
        for index, sample in zip(indices, cached['samples']):
            if not isinstance(sample, list) or len(sample) != 2 or type(sample[0]) is not int or sample[0] != index:
                return None
            samples.append([index, _height(sample[1])])
        result = _decorate(original, samples, spacing, cached['retrieved_at'])
        return validate_route(result) if not result['summary']['missing_elevation_points'] else None
    except (OSError, ValueError, TypeError, KeyError, ElevationError):
        return None


def _write_cache(directory, route):
    meta = route['elevation_enrichment']
    directory = Path(directory)
    temp = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        value = {key: meta[key] for key in ('source_route_hash', 'samples', 'sample_spacing_m', 'retrieved_at')}
        key = _hash([CACHE_VERSION, meta['source_route_hash'], PROVIDER, DATASET, ALGORITHM])
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory, prefix='.elevation-', suffix='.tmp', delete=False) as handle:
            temp = Path(handle.name)
            json.dump(value, handle, allow_nan=False, separators=(',', ':'))
        temp.replace(directory / (key + '.json'))
        temp = None
        files = [path for path in directory.iterdir() if re.fullmatch(r'[0-9a-f]{64}\.json', path.name) and path.is_file() and not path.is_symlink()]
        for path in sorted(files, key=lambda item: item.stat().st_mtime)[:-MAX_CACHE_FILES]:
            path.unlink()
    except OSError:
        # Caching is an optimization; a validated lookup can still be displayed.
        pass
    finally:
        if temp is not None:
            temp.unlink(missing_ok=True)


def enrich_missing_elevations(route, cache_dir, *, current_route=None, transport=None, now=None):
    """Automatically fill missing heights; caller owns atomic active-course storage."""
    from race_gpx import validate_route
    original = validate_route(route)
    current = validate_route(current_route) if current_route is not None else original
    if current['route_hash'] != original['route_hash'] and current.get('elevation_enrichment', {}).get('source_route_hash') != original['route_hash']:
        raise ValueError('Elevation retry requires the same original GPX course.')
    if not original['summary']['missing_elevation_points']:
        return original
    if original.get('elevation_enrichment'):
        raise ValueError('Retry elevation lookup using the preserved original GPX route.')
    cached = _read_cache(cache_dir, original)
    if cached is not None:
        return cached
    indices, spacing = _sampling(original)
    samples = []
    started = time.monotonic()
    for offset in range(0, len(indices), BATCH_SIZE):
        remaining = TOTAL_TIMEOUT - (time.monotonic() - started)
        if remaining <= 0:
            raise ElevationError('elevation_unavailable')
        batch = indices[offset:offset + BATCH_SIZE]
        locations = [{'lat': original['points'][index]['lat'], 'lon': original['points'][index]['lon']} for index in batch]
        try:
            response = (transport or _request)(locations, min(REQUEST_TIMEOUT, remaining))
        except ElevationError:
            raise
        except Exception:
            raise ElevationError('elevation_unavailable') from None
        if time.monotonic() - started > TOTAL_TIMEOUT:
            raise ElevationError('elevation_unavailable')
        try:
            if not isinstance(response, dict) or response.get('error') or not isinstance(response.get('elevation'), list) or len(response['elevation']) != len(batch):
                raise ValueError('Invalid map response')
            samples.extend([index, _height(value)] for index, value in zip(batch, response['elevation']))
        except (ValueError, TypeError):
            raise ElevationError('elevation_invalid_response') from None
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    result = _decorate(original, samples, spacing, stamp)
    if not result['summary']['derived_elevation_points']:
        raise ElevationError('elevation_no_data')
    result = validate_route(result)
    if any(old['elevation_m'] is not None and new['elevation_m'] is None for old, new in zip(current['points'], result['points'])):
        raise ElevationError('elevation_no_improvement')
    if not result['summary']['missing_elevation_points']:
        _write_cache(cache_dir, result)
    return result


def source_notes(route):
    from race_gpx import validate_route
    route = validate_route(route)
    source = route['summary'].get('elevation_source')
    if not source:
        return 'Elevation source: supplied GPX heights. Missing heights remain unknown.\n'
    return ('Map-derived surface elevation: Open-Meteo / Copernicus DEM 2021 GLO-90.\n'
            + SOURCE_URL + '\n' + COPERNICUS_URL + '\n'
            + 'API data: CC BY 4.0, ' + LICENCE_URL + '\n'
            + 'Copernicus WorldDEM-90: DLR e.V. 2010-2014; Airbus Defence and Space GmbH 2014-2018; provided under COPERNICUS by the European Union and ESA.\n'
            + 'Source licence and required notices: https://dataspace.copernicus.eu/sites/default/files/media/files/2025-06/copernicus_contributing_mission_data_access_v2_cop_dem_licenses.pdf\n'
            + 'Changes: ' + source['changes'] + '\n'
            + 'Approximate 90 m surface model (including buildings/vegetation), not measured trail heights. Bridges, tunnels and mixed elevation datums may differ.\n'
            + 'Requested sampling spacing: ' + str(source['sample_spacing_m']) + ' m; samples: ' + str(source['sample_count']) + '. Sparse GPS points may make spacing larger.\n'
            + 'Retrieved: ' + source['retrieved_at'] + '. Filled points: ' + str(source['filled_points']) + '.\n'
            + 'Free Open-Meteo API service is for personal/noncommercial use: https://open-meteo.com/en/terms\n')
