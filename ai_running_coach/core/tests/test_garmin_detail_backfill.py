"""Synthetic original FIT wire data; no Garmin account or network is used."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
from io import BytesIO
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from unittest.mock import Mock, patch
from zipfile import ZipFile

from database_manager import ensure_schema, ingest_payload
from garmin_fit import decode_activity_archive, decode_fit, project_messages
from garmin_sync import backfill_activity_details, map_garmin_to_payload


def crc(data):
    value=0
    for byte in data:
        value ^= byte
        for _ in range(8):value=(value>>1)^0xa001 if value&1 else value>>1
    return value


def fixture():
    body=bytearray()
    def message(global_id,fields,values):
        body.extend(bytes([0x40,0,0])+struct.pack('<H',global_id)+bytes([len(fields)]))
        for field,size,kind in fields:body.extend(bytes([field,size,kind]))
        body.append(0)
        for (_,size,_),value in zip(fields,values):body.extend(int(value).to_bytes(size,'little',signed=value<0))
    t=1000000000
    # Session, 150 seconds elapsed; timestamp, start_time, elapsed time.
    message(18,[(253,4,0x86),(2,4,0x86),(7,4,0x86)],[t+150,t,150000])
    for dt,event in [(0,0),(35,4),(95,0),(150,4)]:
        message(21,[(253,4,0x86),(0,1,0),(1,1,0)],[t+dt,0,event])
    for dt,distance,hr in [(0,0,140),(29,2900,145),(30,3000,150),(100,5000,160),(149,9900,170)]:
        # Include position_lat to ensure it is discarded, signed altitude = -10m.
        message(20,[(253,4,0x86),(5,4,0x86),(2,2,0x84),(3,1,2),(6,2,0x84),(0,4,0x85)],
                [t+dt,distance,2450,hr,3000,12345678])
    message(19,[(2,4,0x86),(9,4,0x86),(7,4,0x86),(8,4,0x86),(21,2,0x84),(22,2,0x84),(15,1,2)],
            [t,9900,150000,90000,12,15,153])
    message(8,[(254,2,0x84),(1,1,2)],[0,140])
    message(8,[(254,2,0x84),(1,1,2)],[1,160])
    header=struct.pack('<BBHI4s',14,0x20,2100,len(body),b'.FIT')
    header+=struct.pack('<H',crc(header))
    raw=header+body
    raw+=struct.pack('<H',crc(raw))
    output=BytesIO()
    with ZipFile(output,'w') as archive:archive.writestr('../activity.fit',raw)
    return raw,output.getvalue()


class GarminDetailBackfillTests(unittest.TestCase):
    def test_real_decoder_elapsed_pause_gaps_laps_and_no_gps(self):
        raw,archive=fixture();detail=decode_activity_archive(archive)
        rows=detail['rows']
        self.assertEqual([r['time'] for r in rows],[0,30,60,90,120,150])
        self.assertEqual(rows[0]['distance'],29)
        self.assertEqual(rows[0]['altitude'],-10)
        self.assertEqual(rows[1]['timer_state'],'mixed')
        self.assertEqual(rows[1]['moving_state'],'unknown')
        self.assertIsNone(rows[1]['moving'])
        self.assertAlmostEqual(rows[1]['timer_running_fraction'],1/6,places=5)
        self.assertEqual(rows[2]['timer_state'],'paused')
        self.assertEqual(rows[2]['moving_state'],'unknown')
        self.assertIsNone(rows[2]['heartrate']);self.assertIsNone(rows[2]['distance'])
        self.assertEqual(detail['laps'][0]['elevation_loss_m'],15)
        self.assertEqual(detail['laps'][0]['start_distance_m'],0)
        self.assertIsNone(detail['laps'][0]['moving_time_s'])
        self.assertEqual(detail['activity_hr_zones']['boundaries'][0],{'zone':1,'max_bpm':140})
        self.assertNotIn('position',json.dumps(detail))
        corrupt=bytearray(raw);corrupt[-1]^=1
        with self.assertRaises(ValueError):decode_fit(bytes(corrupt))

    def test_timer_unknown_and_decreasing_distance_rejected(self):
        start=datetime(2020,1,1,tzinfo=timezone.utc)
        messages=[('session',{'start_time':start,'total_elapsed_time':30}),
                  ('record',{'timestamp':start,'speed':0,'distance':10})]
        self.assertEqual(project_messages(messages)['rows'][0]['moving_state'],'unknown')
        messages.append(('record',{'timestamp':start+timedelta(seconds=20),'distance':9}))
        with self.assertRaises(ValueError):project_messages(messages)

    def test_idempotent_commit_metadata_refresh_preserves_detail_and_failure_retains_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'db.sqlite';state=root/'state.json'
            state.write_text(json.dumps({'source_identity':'bound','base_url':'https://personal.connectapi.garmin.com'}))
            summary={'activityId':7,'startTimeLocal':'2020-01-01T12:00:00','activityType':{'typeKey':'running'}}
            payload=map_garmin_to_payload(summary)
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.row_factory=sqlite3.Row;ensure_schema(conn);ingest_payload(conn,payload)
                conn.execute("UPDATE activities SET notes='Keep', ai_feedback='Keep AI'")
            client=Mock();client.fingerprint='bound';client.base_url='https://personal.connectapi.garmin.com'
            client.max_requests=100;client.timeout=30;client.activity.return_value={}
            client.download_activity.return_value=fixture()[1]
            result=backfill_activity_details(7,client=client,db_path=db,state_file=state)
            self.assertEqual(result,{'activity_id':7,'status':'updated','stream_rows':6,'laps':1,'activity_hr_zones_status':'recorded'})
            backfill_activity_details(7,client=client,db_path=db,state_file=state)
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.row_factory=sqlite3.Row;ingest_payload(conn,payload)
                before=tuple(conn.execute('SELECT downsampled_streams_json,raw_laps_json,activity_hr_zones_json,notes,ai_feedback FROM activities').fetchone())
                self.assertTrue(before[0]);self.assertEqual(before[-2:],('Keep','Keep AI'))
                self.assertEqual(conn.execute('SELECT count(*) FROM activities').fetchone()[0],1)
            client.download_activity.return_value=b'bad zip'
            with self.assertRaises(ValueError):backfill_activity_details(7,client=client,db_path=db,state_file=state)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(tuple(conn.execute('SELECT downsampled_streams_json,raw_laps_json,activity_hr_zones_json,notes,ai_feedback FROM activities').fetchone()),before)
            client.reset_mock();client.fingerprint='other-account'
            with self.assertRaises(ValueError):backfill_activity_details(7,client=client,db_path=db,state_file=state)
            client.activity.assert_not_called()
            for identifier in (0,-1,True,'7'):
                with self.assertRaises(ValueError):backfill_activity_details(identifier,client=client,db_path=db,state_file=state)


    def test_eight_hour_race_fifty_laps_and_timer_stops(self):
        start=datetime(2020,1,1,tzinfo=timezone.utc)
        elapsed=8*3600
        messages=[('session',{'start_time':start,'total_elapsed_time':elapsed}),
                  ('event',{'timestamp':start,'event':'timer','event_type':'start'})]
        stops=list(range(600,elapsed,600))
        for stop in stops:
            messages.extend([('event',{'timestamp':start+timedelta(seconds=stop),'event':'timer','event_type':'stop_all'}),
                             ('event',{'timestamp':start+timedelta(seconds=stop+10),'event':'timer','event_type':'start'})])
        messages.extend(('record',{'timestamp':start+timedelta(seconds=t),'distance':t*50000/elapsed,
                                  'speed':0 if t==300 else 2,'heart_rate':150}) for t in range(elapsed+1))
        messages.extend(('lap',{'start_time':start+timedelta(seconds=i*576),
                                'total_distance':1000,'total_elapsed_time':576}) for i in reversed(range(50)))
        detail=project_messages(messages)
        self.assertEqual(len(detail['rows']),961)
        self.assertEqual(len(detail['laps']),50)
        self.assertEqual(detail['laps'][-1]['start_distance_m'],49000)
        self.assertEqual(detail['rows'][-1]['distance'],50000)
        # Timer running while speed is zero must never claim physical movement.
        self.assertEqual(detail['rows'][10]['timer_state'],'running')
        self.assertIsNone(detail['rows'][10]['moving'])
        self.assertEqual(detail['metadata']['activity_start_utc'],start.isoformat())
        running=sum(row['timer_running_fraction']*min(30,elapsed-row['time'])
                    for row in detail['rows'] if row['timer_running_fraction'] is not None)
        self.assertAlmostEqual(running,elapsed-10*len(stops),places=3)
        # Bound pathological event counts independently of race duration or record count.
        with patch('garmin_fit.MAX_TIMER_EVENTS',3):
            with self.assertRaisesRegex(ValueError,'timer-event limit'):
                project_messages(messages)
        with patch('garmin_fit.MAX_RECORDS',100):
            with self.assertRaisesRegex(ValueError,'record limit'):
                project_messages(messages)

    def test_zip_member_count_and_size_limits(self):
        out=BytesIO()
        with ZipFile(out,'w') as archive:
            archive.writestr('one.fit',fixture()[0]);archive.writestr('two.fit',fixture()[0])
        with self.assertRaises(ValueError):decode_activity_archive(out.getvalue())


if __name__=='__main__':unittest.main()
