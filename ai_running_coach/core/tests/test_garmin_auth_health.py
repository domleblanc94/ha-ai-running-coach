import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest
from health_status import public_state,public_garmin_auth

class GarminAuthHealthTests(unittest.TestCase):
    def test_input_action_survives_old_success_and_reaches_legacy_page_issues(self):
        state={'last_pipeline_success_at':'2026-11-04T03:00:00+00:00','last_publish_success_at':'2026-11-04T05:00:00+00:00','last_sync_success_at':'2026-11-03T03:00:00+00:00','pipeline_errors':[], 'garmin_auth':{'state':'action_required','code':'key_rejected','checked_at':'2026-11-04T11:00:00+00:00','http_status':401}}
        public=public_state(state)
        self.assertEqual(public['status'],'input_required')
        text=' '.join(public['issues']);self.assertIn('INPUT REQUIRED',text);self.assertIn('GARMIN_PERSONAL_API_KEY',text);self.assertIn('Last confirmed activity sync: 2026-11-03',text)
        self.assertIn('may be expired',text);self.assertNotIn('expired on',text)
    def test_only_safe_auth_fields_and_fixed_messages_are_public(self):
        auth={'state':'action_required','code':'missing_key','message':'private-key-value','action':'private-response-body','key':'private-key-value','fingerprint':'private-fingerprint','retry_after':float('inf'),'checked_at':'private-token'}
        text=json.dumps(public_garmin_auth(auth));self.assertNotIn('private-',text);self.assertNotIn('retry_after',text)
        self.assertEqual(public_garmin_auth({'state':{},'code':[]})['state'],'unknown')
    def test_success_clears_input_message_without_an_expiry_claim(self):
        public=public_state({'pipeline_errors':[],'garmin_auth':{'state':'ok','code':'ok','checked_at':'2026-11-04T11:00:00+00:00'}})
        self.assertFalse(any('INPUT REQUIRED' in issue for issue in public['issues']))
        self.assertNotIn('expiry',public['garmin_auth'])

    def test_persisted_token_failure_remedy_includes_explicit_refresh(self):
        public=public_garmin_auth({'state':'action_required','code':'access_token_rejected'})
        self.assertIn('--refresh-token',public['action'])
        self.assertIn('--refresh-token',public_garmin_auth({'state':'action_required','code':'key_rejected'})['action'])

    @unittest.skipUnless(shutil.which('node'),'Node is optional for browser status tests')
    def test_stale_auth_and_migration_are_not_presented_as_current(self):
        source=(Path(__file__).resolve().parents[1]/'dashboard_template.py').read_text(encoding='utf-8')
        function=re.search(r'function healthDisplay\(.*?\n        \}\}',source,re.S)[0].replace('{{','{').replace('}}','}')
        script=function+";const now=Date.parse('2026-11-04T12:00:00Z');const base={schema_version:1,issues:[],updated_at:'2026-11-01T12:00:00Z'};console.log(JSON.stringify([healthDisplay({...base,garmin_auth:{schema_version:1,state:'temporary_error'}},now),healthDisplay({...base,phase:'migration_pending'},now),healthDisplay({...base,garmin_auth:{schema_version:1,state:'action_required',message:'Key needs action',action:'Update privately'}},now)]));"
        result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True,check=True)
        values=json.loads(result.stdout)
        self.assertEqual(values[0]['label'],'Workflow status is stale');self.assertEqual(values[1]['label'],'Workflow status is stale')
        self.assertIn('Last reported input required',values[2]['label']);self.assertIn('stale',values[2]['details'])
