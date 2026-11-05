"""Public-release checks for credential boundaries and an independent first run."""
import json
import base64
import os
import re
from pathlib import Path
import stat
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import zipfile
import unittest
from unittest.mock import MagicMock,patch

from garmin_personal_api import GarminPersonalClient
from import_activities import import_activities
from database_manager import ensure_schema
import prepare_release

ROOT=Path(__file__).resolve().parents[1]


class PublicReleaseTests(unittest.TestCase):
    def token_response(self, clock, marker='first'):
        payload=base64.urlsafe_b64encode(json.dumps({'exp':clock+3600,'fixture':marker}).encode()).decode().rstrip('=')
        response=MagicMock(status_code=200,headers={})
        response.text='fixture.'+payload+'.signature'
        return response

    def test_official_garmin_cache_is_private_and_never_rewrites_env(self):
        for with_env in (False,True):
            with self.subTest(with_env=with_env),tempfile.TemporaryDirectory() as directory:
                base=Path(directory);clock=1800000000
                env=base/'.env'
                if with_env:env.write_bytes(b'# User-managed fixture settings\nGARMIN_PERSONAL_API_KEY=operator-fixture-key\n')
                before=env.read_bytes() if env.exists() else None
                session=MagicMock();session.request.return_value=self.token_response(clock)
                cache=base/'.garmin_personal_token.json'
                client=GarminPersonalClient('operator-fixture-key',cache,session=session,clock=lambda:clock)
                first=client.access_token();second=client.access_token()
                self.assertEqual(first,second)
                self.assertEqual(session.request.call_count,1)
                self.assertEqual(session.request.call_args.kwargs['headers']['Authorization'],'ApiKey operator-fixture-key')
                self.assertFalse(session.request.call_args.kwargs['allow_redirects'])
                self.assertEqual(env.read_bytes() if env.exists() else None,before)
                self.assertTrue(cache.is_file())
                if os.name!='nt':self.assertEqual(stat.S_IMODE(cache.stat().st_mode),0o600)
                self.assertEqual(set(path.name for path in base.iterdir()),{cache.name}|({'.env'} if with_env else set()))

    def test_official_garmin_key_or_region_change_invalidates_cache_and_cooldown(self):
        with tempfile.TemporaryDirectory() as directory:
            cache=Path(directory)/'.garmin_personal_token.json';clock=1800000000
            session=MagicMock();session.request.return_value=self.token_response(clock)
            original=GarminPersonalClient('first-fixture-key',cache,session=session,clock=lambda:clock)
            first=original.access_token()
            value=json.loads(cache.read_text());value['not_before']=clock+3600
            cache.write_text(json.dumps(value))
            for key,region in [('new-fixture-key','global'),('first-fixture-key','china')]:
                with self.subTest(key=key,region=region):
                    replacement=MagicMock();replacement.request.return_value=self.token_response(clock,region)
                    client=GarminPersonalClient(key,cache,region=region,session=replacement,clock=lambda:clock)
                    self.assertNotEqual(client.access_token(),first)
                    self.assertEqual(replacement.request.call_count,1)

    def test_offline_imports_need_no_model_key_or_optional_garmin_package(self):
        code='''import builtins, os, sys, socket
def deny_network(*args,**kwargs):
    raise AssertionError('Offline import attempted network access')
socket.create_connection=deny_network
socket.socket.connect=deny_network
original = builtins.__import__
def guarded(name,*args,**kwargs):
    if name == 'garminconnect' or name == 'google' or name.startswith('google.'):
        raise ImportError('Optional SDK blocked for cold-start check')
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
sys.path.insert(0,sys.argv[1])
import ai_coach, database_manager, garmin_sync, planning_engine, coach_checkin, generate_dashboard
assert not os.listdir('.')
os.environ['GARMIN_PERSONAL_API_KEY']=''
try:
    garmin_sync.get_garmin_client()
except ValueError as error:
    assert 'GARMIN_PERSONAL_API_KEY' in str(error)
else:
    raise AssertionError('Official Garmin client should validate its explicitly configured key')
'''
        env={key:value for key,value in os.environ.items() if key not in
             {'GEMINI_API_KEY','OPENAI_API_KEY','ANTHROPIC_API_KEY','GARMIN_EMAIL','GARMIN_PASSWORD','GARMIN_PERSONAL_API_KEY'}}
        with tempfile.TemporaryDirectory() as directory:
            result=subprocess.run([sys.executable,'-c',code,str(ROOT)],cwd=directory,env=env,
                                  capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_optional_private_fixture_tests_have_no_machine_specific_fallback(self):
        # Private replay must be deliberately opted in on every machine.
        for path in (ROOT/'tests').glob('test_*.py'):
            text=path.read_text(encoding='utf-8-sig')
            self.assertIsNone(re.search(r"Path\(['\"][A-Za-z]:[/\\]Users[/\\][^'\"]+[/\\]Downloads",text),str(path)+" has a machine-specific private fixture fallback")

    def test_demo_server_does_not_expose_project_credentials_or_bind_all_interfaces(self):
        readme=(ROOT/'README.md').read_text(encoding='utf-8')
        commands=[line.strip() for line in readme.splitlines() if re.search(r'python(?:3)? -m http\.server',line)]
        self.assertTrue(commands)
        for command in commands:
            self.assertIn('--bind 127.0.0.1',command)
            self.assertIn('--directory demo',command)

    def test_vendored_assets_have_corresponding_licenses(self):
        assets=ROOT/'dashboard_assets'
        for name in ('chart-LICENSE.md','marked-LICENSE.md','purify-LICENSE.txt',
                     'tailwind-LICENSE.txt','fontawesome-LICENSE.txt'):
            self.assertGreater((assets/name).stat().st_size,100)


class LocalImportTests(unittest.TestCase):
    def row(self, **overrides):
        row={'id':1,'start_date':'2026-11-04T08:00:00','type':'Run',
             'distance_m':5000,'moving_time_s':1800,'description':'Fictional device report'}
        row.update(overrides)
        return row

    def write(self, root, rows):
        path=Path(root)/'summaries.json'
        path.write_text(json.dumps(rows),encoding='utf-8')
        return path

    def test_preview_has_no_database_side_effects_and_empty_values_stay_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'activities.sqlite'
            data=self.write(root,[self.row(elevation_gain_m=None,avg_heartrate=None)])
            self.assertTrue(import_activities(data,db,dry_run=True)['dry_run'])
            self.assertFalse(db.exists())
            import_activities(data,db)
            with closing(sqlite3.connect(db)) as conn:
                row=conn.execute('SELECT source,distance_m,moving_time_s,elevation_gain_m,avg_heartrate FROM activities').fetchone()
            self.assertEqual(row,('local:watch',5000,1800,None,None))

    def test_csv_schema_uses_explicit_metres_seconds_and_no_estimated_hr(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);data=root/'device.csv';db=root/'activities.sqlite'
            data.write_text('id,start_date,type,distance_m,moving_time_s,elevation_gain_m,avg_heartrate\n1,2026-11-04,TrailRun,5000,1800,120,\n',encoding='utf-8')
            import_activities(data,db)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT distance_m,moving_time_s,elevation_gain_m,avg_heartrate FROM activities').fetchone(),(5000,1800,120,None))

    def test_invalid_batch_never_partially_imports_or_creates_a_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'activities.sqlite'
            data=self.write(root,[self.row()]);import_activities(data,db)
            before=db.read_bytes()
            data=self.write(root,[self.row(id=2),self.row(id=3,distance_m=-5)])
            with self.assertRaises(ValueError):import_activities(data,db)
            self.assertEqual(db.read_bytes(),before)
            self.assertFalse(list(root.glob('*.import-backup-*.sqlite')))
            data=self.write(root,[self.row(source='strava')])
            with self.assertRaises(ValueError):import_activities(data,db)

    def test_update_is_stable_preserves_feedback_and_keeps_a_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'activities.sqlite'
            data=self.write(root,[self.row()]);import_activities(data,db)
            with closing(sqlite3.connect(db)) as conn,conn:
                conn.execute("UPDATE activities SET ai_feedback='Keep feedback',notes='Local report'")
            data=self.write(root,[self.row(description='Corrected own-device report',distance_m=None)])
            result=import_activities(data,db)
            self.assertTrue(Path(result['backup']).is_file())
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT count(*),distance_m,ai_feedback,notes FROM activities').fetchone(),(1,5000,'Keep feedback','Local report'))
            with closing(sqlite3.connect(result['backup'])) as backup:
                self.assertEqual(backup.execute('SELECT description FROM activities').fetchone()[0],'Fictional device report')

    def test_local_records_never_relabel_matching_service_or_legacy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'activities.sqlite'
            with closing(sqlite3.connect(db)) as conn,conn:
                ensure_schema(conn)
                conn.execute("INSERT INTO activities(id,type,start_date,distance_m,moving_time_s,source,source_activity_id) VALUES(1,'Run','2026-11-04T08:00:00',5000,1800,'strava','1')")
                conn.execute("INSERT INTO activities(id,type,start_date,distance_m,moving_time_s) VALUES(2,'Run','2026-11-04T08:00:00',5000,1800)")
            data=self.write(root,[self.row(),self.row(id=2)])
            import_activities(data,db)
            import_activities(data,db)
            with closing(sqlite3.connect(db)) as conn:
                self.assertEqual(conn.execute('SELECT count(*) FROM activities').fetchone()[0],4)
                self.assertEqual(conn.execute('SELECT source FROM activities WHERE id=1').fetchone()[0],'strava')
                self.assertIsNone(conn.execute('SELECT source FROM activities WHERE id=2').fetchone()[0])
                self.assertEqual(conn.execute("SELECT count(*) FROM activities WHERE source='local:watch'").fetchone()[0],2)

    def test_duplicate_ids_and_ambiguous_units_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);db=root/'activities.sqlite'
            for rows in ([self.row(),self.row()], [self.row(distance_km=5)],
                         [self.row(id=True)],[self.row(start_date='20261104')]):
                with self.subTest(rows=rows):
                    data=self.write(root,rows)
                    with self.assertRaises(ValueError):import_activities(data,db)
                    self.assertFalse(db.exists())


class ReleaseArchiveTests(unittest.TestCase):
    def test_private_runtime_files_misplaced_in_asset_folders_are_not_packaged(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed';output=root/'public'
            sentinel='private-fixture-'+os.urandom(16).hex()
            prepare_release.build_release(ROOT,seed)
            for relative in ('.env','athlete.md','data.sqlite','examples/.env',
                             'examples/private.sqlite','dashboard_assets/.strava_tokens.json'):
                path=seed/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text(sentinel,encoding='utf-8')
            archive=prepare_release.build_release(seed,output)
            with zipfile.ZipFile(archive) as package:
                names=package.namelist()
                self.assertIn('public/.env.example',names)
                for name in names:
                    self.assertFalse(sentinel.encode() in package.read(name),name)
                self.assertNotIn('public/examples/.env',names)
                self.assertNotIn('public/examples/private.sqlite',names)
                self.assertNotIn('public/athlete.md',names)
                self.assertNotIn('public/dashboard_assets/.strava_tokens.json',names)

    def test_release_rejects_symlinked_source_before_copying_target_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);seed=root/'seed';output=root/'public'
            sentinel='private-fixture-'+os.urandom(16).hex()
            prepare_release.build_release(ROOT,seed)
            secret=root/'outside-private.txt';secret.write_text(sentinel,encoding='utf-8')
            readme=seed/'README.md';readme.unlink()
            try:readme.symlink_to(secret)
            except OSError:self.skipTest('Creating symlinks is unavailable on this host')
            with self.assertRaises(ValueError):prepare_release.build_release(seed,output)
            if (output/'README.md').exists():
                self.assertFalse(sentinel in (output/'README.md').read_text())


if __name__=='__main__':unittest.main()
