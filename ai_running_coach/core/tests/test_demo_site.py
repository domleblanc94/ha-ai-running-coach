"""Exercise the public-demo data boundary and real renderer without accounts."""
from contextlib import redirect_stdout
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from build_demo_site import build_demo_site
from demo_data import SAMPLE_DATE,populate_demo
from planning_engine import parse_profile,apply_plan_changes
from prepare_release import build_release

ROOT=Path(__file__).resolve().parents[1]


class Assets(HTMLParser):
    def __init__(self):super().__init__();self.external=[];self.forms=[]
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in {'script','iframe','img','link'}:
            self.external.extend(value for key,value in attrs.items() if key in {'src','href'} and not value.startswith('data:'))
        if tag=='form':self.forms.append(attrs)


class PublicDemoTests(unittest.TestCase):
    def test_build_is_deterministic_static_only_and_ignores_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);private=root/'private';private.mkdir()
            secret='PRIVATE_SENTINEL_NEVER_PUBLISH_7241'
            for name in ('.env','athlete.md','training_plan.json','ai_coach_master.sqlite'):
                (private/name).write_text(secret,encoding='utf-8')
            before={p.name:p.read_bytes() for p in private.iterdir()}
            with patch('setup_coach.ROOT',private),patch.dict(os.environ,{'GEMINI_API_KEY':secret,'GARMIN_PERSONAL_API_KEY':secret,'COACH_PUBLISH_ENABLED':'true'}),\
                    patch('dotenv.load_dotenv',side_effect=AssertionError('No private config should load')),\
                    patch.object(socket.socket,'connect',side_effect=AssertionError('No network allowed')),redirect_stdout(io.StringIO()):
                first=build_demo_site(root/'first');second=build_demo_site(root/'second')
            self.assertEqual({p.name for p in first.iterdir()},{'index.html'})
            self.assertEqual((first/'index.html').read_bytes(),(second/'index.html').read_bytes())
            self.assertEqual(before,{p.name:p.read_bytes() for p in private.iterdir()})
            html=(first/'index.html').read_text(encoding='utf-8')
            self.assertNotIn(secret,html)
            self.assertNotIn(str(private),html)
            parser=Assets();parser.feed(html)
            self.assertEqual(parser.external,[])
            self.assertEqual(parser.forms,[])
            self.assertIn('SYNTHETIC DEMO',html)
            self.assertIn('2027-05-05',html)
            self.assertIn('https://github.com/domleblanc94/ha-ai-running-coach#readme',html)
            with self.assertRaises(ValueError):build_demo_site(first)
            self.assertEqual((first/'index.html').read_bytes(),(second/'index.html').read_bytes())

    def test_allowlisted_clean_source_builds_from_unrelated_private_cwd(self):
        with tempfile.TemporaryDirectory() as directory,redirect_stdout(io.StringIO()):
            root=Path(directory);source=root/'source';private=root/'private';private.mkdir()
            build_release(ROOT,source,create_archive=False)
            self.assertTrue((source/'docs/STANDALONE.md').is_file())
            self.assertTrue((source/'.github/workflows/pages.yml').is_file())
            (private/'.env').write_text('GEMINI_API_KEY=private-cwd-sentinel\n',encoding='utf-8')
            (private/'athlete.md').write_text('private-cwd-profile',encoding='utf-8')
            script='''import sys,socket
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
with patch.object(socket.socket,'connect',side_effect=AssertionError('No network')),patch('dotenv.load_dotenv',side_effect=AssertionError('No private config')):
 from build_demo_site import build_demo_site
 build_demo_site(sys.argv[2])
'''
            result=subprocess.run([sys.executable,'-B','-c',script,str(source),str(root/'site')],cwd=private,capture_output=True,text=True,timeout=30)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual({p.name for p in (root/'site').iterdir()},{'index.html'})
            html=(root/'site/index.html').read_text(encoding='utf-8')
            self.assertNotIn('private-cwd-sentinel',html);self.assertNotIn('private-cwd-profile',html)

    def test_sample_week_is_valid_multisport_with_coherent_brick_and_pool(self):
        with tempfile.TemporaryDirectory() as directory:
            root=populate_demo(Path(directory)/'data',SAMPLE_DATE)
            profile=parse_profile((root/'athlete.md').read_text(encoding='utf-8'))
            plan=json.loads((root/'training_plan.json').read_text(encoding='utf-8'))
            apply_plan_changes(plan,[],profile,SAMPLE_DATE.isoformat())
            self.assertEqual({row['sport'] for row in plan['sessions']},{'running','cycling','swimming','mobility','rest'})
            self.assertEqual({row['date'] for row in plan['sessions']},{'2027-05-0'+str(day) for day in range(3,10)})
            legs=[row for row in plan['sessions'] if 'brick' in row]
            self.assertEqual([(row['sport'],row['brick']['order'],row['date']) for row in legs],[('cycling',1,'2027-05-08'),('running',2,'2027-05-08')])
            pool=next(row['workout'] for row in plan['sessions'] if row['sport']=='swimming')
            self.assertEqual(sum(step['duration']['metres'] for step in pool['steps']),1200)
            self.assertEqual(pool['poolLengthMetres'],25)
            self.assertFalse((root/'.env').exists())

    @unittest.skipUnless(shutil.which('node') and os.getenv('COACH_FIT_SDK_MODULE'),'Node and official FIT SDK required for sample-file decoding')
    def test_downloadable_samples_decode_with_official_garmin_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root=populate_demo(Path(directory)/'data',SAMPLE_DATE)
            plan=json.loads((root/'training_plan.json').read_text(encoding='utf-8'))
        workouts=[row['workout'] for row in plan['sessions'] if 'workout' in row]
        script='''const fs=require('fs'),vm=require('vm'),{pathToFileURL}=require('url');
(async()=>{const sdk=await import(pathToFileURL(process.argv[2]).href);vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const values=JSON.parse(fs.readFileSync(0,'utf8')).map(w=>{const bytes=WorkoutFit.encode(w,{createdAt:'2027-05-05T12:00:00Z'});const d=new sdk.Decoder(sdk.Stream.fromByteArray(Array.from(bytes)));return {valid:d.checkIntegrity(),...d.read()};});console.log(JSON.stringify(values));})().catch(e=>{console.error(e);process.exitCode=1;});'''
        result=subprocess.run([shutil.which('node'),'-e',script,str(ROOT/'dashboard_assets/workout-fit.js'),os.environ['COACH_FIT_SDK_MODULE']],input=json.dumps(workouts),capture_output=True,text=True,check=True)
        decoded=json.loads(result.stdout)
        self.assertEqual(len(decoded),5)
        for authored,item in zip(workouts,decoded):
            self.assertTrue(item['valid']);self.assertEqual(item['errors'],[])
            self.assertEqual(item['messages']['workoutMesgs'][0]['sport'],authored['sport'])
            self.assertEqual(len(item['messages']['workoutStepMesgs']),len(authored['steps']))
        self.assertEqual(decoded[2]['messages']['workoutMesgs'][0]['poolLength'],25)
        self.assertEqual(sum(row['durationDistance'] for row in decoded[2]['messages']['workoutStepMesgs']),1200)


if __name__=='__main__':unittest.main()
