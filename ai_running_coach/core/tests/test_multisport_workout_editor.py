"""Offline browser-editor behavior, using a small DOM harness and the real encoder."""
import copy
import json
import subprocess
import unittest

from test_workout_fit import NODE, ROOT, FIXTURE
from test_multisport_workout_fit import CYCLE, POOL, yard_pool


HARNESS = r'''
const fs=require('node:fs'),vm=require('node:vm');
const payload=JSON.parse(fs.readFileSync(0,'utf8'));let encoded=null,filename=null;
class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.parentNode=null;this.listeners={};this.dataset={};this.style={};this.classList={add:()=>{}};this.value='';this.text='';}
 set textContent(value){this.text=String(value);this.children=[];} get textContent(){return this.text+this.children.map(c=>c.textContent).join('');}
 get firstChild(){return this.children[0];}
 append(...children){children.forEach(c=>{this.children.push(c);c.parentNode=this;});}
 replaceChildren(){this.children=[];}
 addEventListener(type,fn){(this.listeners[type]||(this.listeners[type]=[])).push(fn);}
 setAttribute(name,value){this[name]=value;}
 querySelector(selector){return find(this,n=>selector==='[data-workout-download]'?n.dataset.workoutDownload:n.tagName===selector);}
 remove(){if(this.parentNode)this.parentNode.children=this.parentNode.children.filter(c=>c!==this);}
 showModal(){this.open=true;} close(){this.open=false;this.emit('close');}
 emit(type,target=this){(this.listeners[type]||[]).forEach(fn=>fn({target}));if(this.parentNode)this.parentNode.emit(type,target);}
 click(){if(this.tagName==='a')filename=this.download;else this.emit('click');}
}
function walk(node){return [node,...node.children.flatMap(walk)];}
function find(node,predicate){return walk(node).find(predicate);}
global.window={};global.document={body:new Element('body'),createElement:tag=>new Element(tag)};
global.Blob=class{constructor(parts){this.parts=parts;}};global.URL={createObjectURL:()=> 'blob:offline',revokeObjectURL:()=>{}};global.setTimeout=()=>{};
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));window.WorkoutFit=WorkoutFit;
const realEncode=WorkoutFit.encode;window.WorkoutFit={encode:w=>{const result=realEncode(w,{createdAt:'2026-11-04T12:00:00Z'});encoded=JSON.parse(JSON.stringify(w));return result;}};
vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
const original=JSON.stringify(payload.session),api=window.WorkoutDownload,eligible=api.eligible(payload.session),reason=api.unsupportedReason(payload.session);
api.attach(document.body,payload.session);const attached=document.body.textContent;
api.open(payload.session);
const before=document.body.textContent;
function control(label,index=0){const scope=walk(document.body).filter(n=>n.tagName==='details')[index]||document.body;const field=find(scope,n=>n.tagName==='label'&&n.firstChild&&n.firstChild.textContent===label);if(!field)throw Error('Missing field '+label);return field.children[1];}
for(const edit of payload.edits||[]){const c=control(edit.label,edit.index||0);c.value=edit.value;c.emit('input');}
if(payload.confirm){const box=find(document.body,n=>n.type==='checkbox');if(box)box.checked=true;}
const download=find(document.body,n=>n.tagName==='button'&&n.textContent==='Download .FIT workout');if(download)download.click();
console.log(JSON.stringify({eligible,reason,attached,before,after:document.body.textContent,encoded,filename,unchanged:original===JSON.stringify(payload.session)}));
'''


@unittest.skipUnless(NODE, 'Node.js is required for browser-editor checks')
class MultisportWorkoutEditorTests(unittest.TestCase):
    def editor(self, session, **options):
        result = subprocess.run([NODE, '-e', HARNESS,
            str(ROOT/'dashboard_assets/workout-fit.js'), str(ROOT/'dashboard_assets/workout-ui.js')],
            input=json.dumps({'session':session, **options}), capture_output=True, text=True, encoding='utf-8', check=True)
        return json.loads(result.stdout)

    def test_structured_cycling_preserves_targets_notes_and_separate_brick_order(self):
        session={'sport':'cycling','date':'2026-11-08','workout_type':'Bike intervals','workout':copy.deepcopy(CYCLE),'brick':{'id':'brick-1','order':1}}
        result=self.editor(session)
        self.assertTrue(result['eligible']);self.assertTrue(result['unchanged'])
        self.assertEqual(result['encoded'],CYCLE)
        self.assertIn('180–220 W',result['before']);self.assertIn('80–95 rpm',result['before'])
        self.assertIn('separate cycling file',result['before']);self.assertIn('-leg-1-cycling-',result['filename'])

    def test_yard_preview_and_edited_yards_encode_metres_without_mutating_plan(self):
        session={'sport':'swimming','sub_sport':'lap_swimming','pool_length_metres':22.86,'pool_length_unit':'yd','workout':yard_pool()}
        unchanged=self.editor(session)
        self.assertIn('Pool: 25 yd',unchanged['before']);self.assertIn('100 yd',unchanged['before'])
        self.assertEqual(unchanged['encoded'],yard_pool())
        edited=self.editor(session,edits=[{'label':'Duration value','value':'200'}],confirm=True)
        self.assertEqual(edited['encoded']['steps'][0]['duration']['metres'],182.88)
        self.assertEqual(edited['encoded']['poolLengthMetres'],22.86);self.assertTrue(edited['unchanged'])
        precise=yard_pool();precise['steps'][0]['duration']['metres']=1000
        result=self.editor({**session,'workout':precise})
        self.assertIn('≈1093.61 yd',result['before'])
        self.assertEqual(result['encoded']['steps'][0]['duration']['metres'],1000)

    def test_fractional_pool_lengths_are_warned_but_kept_exactly(self):
        workout=copy.deepcopy(POOL);workout['steps'][0]['duration']['metres']=112.37
        result=self.editor({'sport':'swimming','sub_sport':'lap_swimming','workout':workout})
        self.assertIn('not whole pool lengths',result['before'])
        self.assertEqual(result['encoded']['steps'][0]['duration']['metres'],112.37)
        self.assertTrue(result['unchanged'])

    def test_unsupported_missing_and_mismatched_pool_have_reason_and_no_download(self):
        examples=[({'sport':'multisport'},'separately planned brick leg'),
                  ({'sport':'swimming','sub_sport':'open_water'},'Open-water'),
                  ({'sport':'swimming','sub_sport':'lap_swimming'},'No pool size is assumed'),
                  ({'sport':'swimming','sub_sport':'lap_swimming','workout':POOL,'pool_length_metres':50,'pool_length_unit':'m'},'differs'),
                  ({'sport':'swimming','sub_sport':'lap_swimming','workout':POOL,'pool_length_metres':25,'pool_length_unit':'yd'},'differs'),
                  ({'sport':'swimming','sub_sport':'lap_swimming','workout':POOL,'pool_length_metres':None,'pool_length_unit':None},'differs'),
                  ({'sport':'cycling','workout':FIXTURE},'sport does not match')]
        for session,reason in examples:
            with self.subTest(session=session):
                result=self.editor(session)
                self.assertFalse(result['eligible']);self.assertIn(reason,result['reason']);self.assertIn(reason,result['attached'])
                self.assertIsNone(result['encoded']);self.assertIsNone(result['filename']);self.assertTrue(result['unchanged'])

    def test_unstructured_bike_never_infers_power_and_requires_review(self):
        session={'sport':'cycling','workout_type':'Endurance ride','amount':'60 min','target':'Easy effort, 70% FTP'}
        result=self.editor(session)
        self.assertIsNone(result['encoded']);self.assertIn('Review the workout and confirm',result['after'])
        result=self.editor(session,confirm=True)
        self.assertEqual(result['encoded']['steps'][0]['duration'],{'type':'time','seconds':3600})
        self.assertEqual(result['encoded']['steps'][0]['target'],{'type':'open'})

    def test_editor_stroke_and_cycling_speed_target_changes_are_explicit(self):
        pool=self.editor({'sport':'swimming','sub_sport':'lap_swimming','workout':POOL},edits=[{'label':'Stroke','value':'backstroke'}],confirm=True)
        self.assertEqual(pool['encoded']['steps'][0]['target'],{'type':'stroke','stroke':'backstroke'})
        bike=self.editor({'sport':'cycling','workout':CYCLE},edits=[{'label':'Watch target','value':'speed'},{'label':'Lower speed (km/h)','value':'18'},{'label':'Upper speed (km/h)','value':'27'}],confirm=True)
        self.assertEqual(bike['encoded']['steps'][0]['target'],{'type':'speed','lowMps':5,'highMps':7.5})

    def test_legacy_running_targets_are_not_rounded_by_an_unchanged_preview(self):
        workout=copy.deepcopy(FIXTURE);workout['steps'][2]['target']={'type':'speed','lowMps':2.003,'highMps':2.503}
        result=self.editor({'workout_type':'Running intervals','workout':workout})
        self.assertEqual(result['encoded'],workout);self.assertTrue(result['unchanged'])

    def test_legacy_strides_stay_running_and_do_not_become_a_ride(self):
        result=self.editor({'workout_type':'Easy Run + Strides','amount':'4 km including 4 x 30s strides'},confirm=True)
        self.assertEqual(result['encoded']['sport'],'running')
        self.assertEqual(len(result['encoded']['steps']),9)
        self.assertEqual(sum(step['duration'].get('seconds',0) for step in result['encoded']['steps']),120)
        self.assertTrue(all(step['duration']['type']!='distance' for step in result['encoded']['steps']))
        self.assertIn('finish manually at the stated overall distance',result['before'])
