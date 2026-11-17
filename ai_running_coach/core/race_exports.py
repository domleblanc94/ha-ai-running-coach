"""Documented FIT course/GPX exports. These are not native Garmin PacePro plans.

FIT references: https://developer.garmin.com/fit/file-types/course/
and the official Garmin FIT SDK profile. No external library or network is needed.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import struct
import xml.etree.ElementTree as ET

from race_gpx import validate_route


def _crc(data):
    value = 0
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ 0xA001 if value & 1 else value >> 1
    return value & 0xFFFF


def _definition(local, global_id, fields):
    return bytes([0x40 | local, 0, 0]) + struct.pack('<H', global_id) + bytes([len(fields)]) + bytes(x for field in fields for x in field)


def _name(value):
    value = ''.join(c for c in str(value) if ord(c) >= 32 and ord(c) != 127)
    return value.encode('utf-8')[:63].decode('utf-8', errors='ignore').encode('utf-8').ljust(64, b'\x00')


def _coordinates(point):
    longitude = -180 if point['lon'] == 180 else point['lon']
    return round(point['lat'] * 2 ** 31 / 180), min(0x7FFFFFFE, round(longitude * 2 ** 31 / 180))


def _timing(route, strategy):
    if strategy is None:
        return None
    sections = strategy.get('sections') if isinstance(strategy, dict) else None
    measured = route['summary']['sections']
    if not isinstance(sections, list) or len(sections) != len(measured):
        raise ValueError('A timed course requires every saved strategy section.')
    times = []
    for authored, section in zip(sections, measured):
        low, high = authored.get('seconds_low'), authored.get('seconds_high')
        if authored.get('section_id') != section['id'] or type(low) not in (int, float) or type(high) not in (int, float) or not math.isfinite(low + high) or not 0 < low <= high <= 604800:
            raise ValueError('This strategy has no complete numeric pacing estimate; use the untimed course instead.')
        times.append((low + high) / 2)
    if sum(times) > 604800:
        raise ValueError('Timed course estimates must fit within seven days.')
    return times


def course_fit(route, name=None, strategy=None, *, created_at=None):
    route = validate_route(route)
    if route['summary']['discontinuities']:
        raise ValueError('Upload a continuous course before exporting FIT; GPX segment gaps cannot be joined safely.')
    times = _timing(route, strategy)
    points, sections = route['points'], route['summary']['sections']
    created = created_at or datetime.now(timezone.utc)
    epoch = datetime(1989, 12, 31, tzinfo=timezone.utc)
    stamp = int((created - epoch).total_seconds())
    if not 0 <= stamp <= 0xFFFFFFFE:
        raise ValueError('Creation date is outside the FIT range.')
    offsets, speeds = [], []
    for index, point in enumerate(points):
        if times is None:
            # Course timestamps order points; no target speed or lap duration is claimed.
            offsets.append(index)
            speeds.append(None)
            continue
        elapsed = 0.0
        speed = None
        for section, seconds in zip(sections, times):
            covered = min(section['distance_m'], max(0, point['distance_m'] - section['start_m']))
            elapsed += seconds * covered / section['distance_m']
            if section['start_m'] <= point['distance_m'] <= section['end_m'] + .02:
                speed = section['distance_m'] / seconds
        offsets.append(round(elapsed))
        speeds.append(speed)
    if stamp + max(offsets) > 0xFFFFFFFE:
        raise ValueError('Timed course is outside the FIT timestamp range.')
    output = bytearray()
    output += _definition(0, 0, [(0, 1, 0), (1, 2, 0x84), (2, 2, 0x84), (3, 4, 0x8C), (4, 4, 0x86)])
    identity = json.dumps([route['route_hash'], strategy.get('id') if strategy else None, times], separators=(',', ':'))
    serial = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16) or 1
    output += bytes([0, 6]) + struct.pack('<HHII', 255, 2, serial, stamp)
    output += _definition(1, 31, [(4, 1, 0), (5, 64, 7)])
    output += bytes([1, 1]) + _name(name or route['name'])
    # One lap describes the complete course. Duration is only included for AI-authored timed strategies.
    lap_fields = [(253, 4, 0x86), (2, 4, 0x86), (3, 4, 0x85), (4, 4, 0x85), (5, 4, 0x85), (6, 4, 0x85), (9, 4, 0x86)]
    lap = struct.pack('<IIiiiiI', stamp + offsets[-1], stamp, *_coordinates(points[0]), *_coordinates(points[-1]), round(route['summary']['distance_m'] * 100))
    if times is not None:
        lap_fields += [(7, 4, 0x86), (8, 4, 0x86)]
        lap += struct.pack('<II', round(sum(times) * 1000), round(sum(times) * 1000))
    output += _definition(2, 19, lap_fields) + bytes([2]) + lap
    output += _definition(3, 21, [(253, 4, 0x86), (0, 1, 0), (1, 1, 0)])
    output += bytes([3]) + struct.pack('<IBB', stamp, 0, 0)
    output += _definition(4, 20, [(253, 4, 0x86), (0, 4, 0x85), (1, 4, 0x85), (2, 2, 0x84), (5, 4, 0x86), (6, 2, 0x84)])
    for point, offset, speed in zip(points, offsets, speeds):
        elevation = point['elevation_m']
        altitude = 0xFFFF if elevation is None else round((elevation + 500) * 5)
        encoded_speed = 0xFFFF if speed is None else round(speed * 1000)
        if not 0 <= altitude <= 0xFFFF or not 0 <= encoded_speed <= 0xFFFF or (speed is not None and encoded_speed == 0xFFFF):
            raise ValueError('Course altitude or pacing speed is outside the supported FIT range.')
        output += bytes([4]) + struct.pack('<IiiHIH', stamp + offset, *_coordinates(point), altitude, round(point['distance_m'] * 100), encoded_speed)
    output += bytes([3]) + struct.pack('<IBB', stamp + offsets[-1], 0, 9)
    header = struct.pack('<BBHI4s', 14, 0x20, 21221, len(output), b'.FIT')
    header += struct.pack('<H', _crc(header))
    complete = header + output
    return complete + struct.pack('<H', _crc(complete))


def course_gpx(route, name=None):
    route = validate_route(route)
    root = ET.Element('gpx', {'version': '1.1', 'creator': 'AI Multisport Coach', 'xmlns': 'http://www.topografix.com/GPX/1/1'})
    if route.get('elevation_enrichment'):
        from race_elevation import source_notes, SOURCE_URL
        metadata = ET.SubElement(root, 'metadata')
        ET.SubElement(metadata, 'desc').text = source_notes(route)
        link = ET.SubElement(metadata, 'link', {'href': SOURCE_URL})
        ET.SubElement(link, 'text').text = 'Map-derived surface elevation: Open-Meteo / Copernicus GLO-90'
    track = ET.SubElement(root, 'trk')
    ET.SubElement(track, 'name').text = str(name or route['name'])[:120]
    segment_id, element = None, None
    for point in route['points']:
        if point['segment'] != segment_id:
            segment_id = point['segment']
            element = ET.SubElement(track, 'trkseg')
        child = ET.SubElement(element, 'trkpt', {'lat': str(point['lat']), 'lon': str(point['lon'])})
        if point['elevation_m'] is not None:
            ET.SubElement(child, 'ele').text = str(point['elevation_m'])
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)
