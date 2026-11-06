"""FIT encoder validation and wire-format checks; optional official-SDK round trip.

Set COACH_FIT_SDK_MODULE to a locally installed @garmin/fitsdk/src/index.js
for independent Garmin decoder checks. Tests never download packages or call APIs.
"""
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import unittest

from workout_schema import validate_workout

ROOT=Path(__file__).resolve().parents[1]
NODE=shutil.which('node')
FIXTURE={'name':'Synthetic intervals','sport':'running','steps':[
    {'name':'Warm up','intensity':'warmup','duration':{'type':'time','seconds':600},'target':{'type':'open'}},
    {'name':'Run','notes':'Comfortably fast; smooth form, not a maximum sprint.','intensity':'active','duration':{'type':'distance','metres':400},'target':{'type':'heart_rate','lowBpm':150,'highBpm':170}},
    {'name':'Recover','intensity':'recovery','duration':{'type':'time','seconds':90},'target':{'type':'speed','lowMps':2,'highMps':2.5}},
    {'name':'Cool down','intensity':'cooldown','duration':{'type':'open'},'target':{'type':'open'}}]}
NODE_RUNNER=r'''
const fs=require('node:fs'),vm=require('node:vm');
vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
const payload=JSON.parse(fs.readFileSync(0,'utf8'));
try {
    if (payload.mode==='validate') console.log(JSON.stringify({workout:WorkoutFit.validate(payload.workout)}));
    else console.log(JSON.stringify({bytes:Buffer.from(WorkoutFit.encode(payload.workout,{createdAt:'2026-11-04T12:00:00Z'})).toString('base64')}));
} catch(error) {console.log(JSON.stringify({error:error.message}));}
'''


def run_encoder(workout,mode='encode'):
    result=subprocess.run([NODE,'-e',NODE_RUNNER,str(ROOT/'dashboard_assets/workout-fit.js')],
        input=json.dumps({'workout':workout,'mode':mode}),capture_output=True,text=True,encoding='utf-8',check=True)
    return json.loads(result.stdout)


def crc(data):
    # Independent nibble implementation of CRC-16/ARC (check vector 0xbb3d).
    table=(0,0xcc01,0xd801,0x1400,0xf001,0x3c00,0x2800,0xe401,
           0xa001,0x6c00,0x7800,0xb401,0x5000,0x9c01,0x8801,0x4400)
    value=0
    for byte in data:
        value=(value>>4)^table[value&15]^table[byte&15]
        value=(value>>4)^table[value&15]^table[byte>>4]
    return value


def decode_raw(data):
    size=data[0];end=size+struct.unpack_from('<I',data,4)[0]
    if data[8:12]!=b'.FIT' or end+2!=len(data):raise ValueError('Invalid FIT header/length')
    if crc(data[:size-2])!=struct.unpack_from('<H',data,size-2)[0]:raise ValueError('Header CRC mismatch')
    if crc(data[:-2])!=struct.unpack_from('<H',data,len(data)-2)[0]:raise ValueError('File CRC mismatch')
    definitions={};messages=[];position=size
    while position<end:
        header=data[position];position+=1
        if header&0x80:raise ValueError('Unexpected compressed timestamp')
        local=header&15
        if header&0x40:
            reserved,architecture,global_id,count=struct.unpack_from('<BBHB',data,position)
            position+=5
            if architecture!=0 or reserved!=0:raise ValueError('Unexpected architecture')
            fields=[]
            for _ in range(count):
                field,size,base_type=struct.unpack_from('BBB',data,position);position+=3
                fields.append((field,size,base_type))
            definitions[local]=(global_id,fields)
        else:
            global_id,fields=definitions[local];message={}
            for field,size,base_type in fields:
                raw=data[position:position+size];position+=size
                message[field]=raw.split(b'\0',1)[0].decode('utf-8') if base_type==7 else int.from_bytes(raw,'little')
            messages.append((global_id,message))
    if position!=end:raise ValueError('Data length mismatch')
    return messages


class WorkoutSchemaTests(unittest.TestCase):
    def test_explicit_data_normalizes_without_mutating_or_inventing_fields(self):
        original=copy.deepcopy(FIXTURE)
        clean=validate_workout(original)
        self.assertEqual(clean,FIXTURE)
        clean['steps'][0]['duration']['seconds']=1
        self.assertEqual(original,FIXTURE)
        one={'name':'One step','sport':'running','steps':[{'intensity':'active','duration':{'type':'open'},'target':{'type':'open'}}]}
        self.assertEqual(validate_workout(one),one)

    def invalid_examples(self):
        invalid=[]
        for key in ('duration','target','intensity'):
            value=copy.deepcopy(FIXTURE);value['steps'][0].pop(key);invalid.append(value)
        for duration in ({'type':'time','seconds':0},{'type':'time','seconds':True},
                         {'type':'distance','metres':-1},{'type':'distance','metres':1e100},
                         {'type':'open','seconds':90},{'type':'repeat','count':4},
                         {'type':'time','seconds':'600'}):
            value=copy.deepcopy(FIXTURE);value['steps'][0]['duration']=duration;invalid.append(value)
        for target in ({'type':'heart_rate','lowBpm':170,'highBpm':150},
                       {'type':'heart_rate','lowBpm':150.1,'highBpm':170},
                       {'type':'speed','lowMps':4,'highMps':3},
                       {'type':'open','lowBpm':150},{'type':'pace','minKm':5}):
            value=copy.deepcopy(FIXTURE);value['steps'][0]['target']=target;invalid.append(value)
        value=copy.deepcopy(FIXTURE);value['steps']*=26;invalid.append(value)
        value=copy.deepcopy(FIXTURE);value['name']='x\0y';invalid.append(value)
        value=copy.deepcopy(FIXTURE);value['name']='😀'*16;invalid.append(value)
        value=copy.deepcopy(FIXTURE);value['sport']='multisport';invalid.append(value)
        value=copy.deepcopy(FIXTURE);value['steps'][0]['notes']='é'*101;invalid.append(value)
        return invalid

    def test_rejects_ambiguous_missing_repeated_or_nonfinite_instructions(self):
        for value in self.invalid_examples():
            with self.subTest(value=value),self.assertRaises(ValueError):validate_workout(value)


@unittest.skipUnless(NODE,'Node.js is required for browser encoder tests')
class FitEncoderTests(unittest.TestCase):
    def encode(self,workout=FIXTURE):
        response=run_encoder(workout)
        self.assertNotIn('error',response)
        return base64.b64decode(response['bytes'])

    def test_file_order_crc_and_step_units_match_fit_profile(self):
        self.assertEqual(crc(b'123456789'),0xbb3d)
        data=self.encode();messages=decode_raw(data)
        self.assertEqual([number for number,_ in messages],[0,26,27,27,27,27])
        file_id,workout=messages[0][1],messages[1][1]
        self.assertEqual(file_id[0],5)
        self.assertEqual(file_id[1],255) # Development, not a Garmin-manufactured device.
        self.assertNotEqual(file_id[3],0)
        self.assertEqual(workout[4],1)
        self.assertEqual(workout[6],4)
        self.assertEqual(workout[8],FIXTURE['name'])
        warmup,active,recovery,cooldown=[message for _,message in messages[2:]]
        self.assertEqual((warmup[1],warmup[2],warmup[7]),(0,600000,2))
        self.assertEqual((active[1],active[2],active[3],active[4]),(1,40000,1,0))
        self.assertEqual(active[8],FIXTURE['steps'][1]['notes'])
        self.assertEqual((active[5],active[6]),(250,270)) # Absolute bpm + 100.
        self.assertEqual((recovery[5],recovery[6]),(2000,2500)) # m/s * 1000.
        self.assertEqual((cooldown[1],cooldown[2],cooldown[3]),(5,0,2))
        self.assertEqual([step[254] for _,step in messages[2:]],list(range(4)))
        corrupted=bytearray(data);corrupted[-10]^=1
        with self.assertRaises(ValueError):decode_raw(corrupted)

    def test_javascript_and_python_validators_agree_on_rejection_and_normalization(self):
        check=WorkoutSchemaTests();invalid=check.invalid_examples()
        for value in invalid:
            with self.subTest(value=value):self.assertIn('error',run_encoder(value,'validate'))
        self.assertEqual(run_encoder(FIXTURE,'validate')['workout'],validate_workout(FIXTURE))

    def test_running_device_fifty_step_boundary_is_enforced(self):
        workout=copy.deepcopy(FIXTURE)
        workout['steps']=[copy.deepcopy(FIXTURE['steps'][1]) for _ in range(50)]
        self.assertEqual(len(validate_workout(workout)['steps']),50)
        self.assertEqual(decode_raw(self.encode(workout))[1][1][6],50)
        workout['steps'].append(copy.deepcopy(FIXTURE['steps'][1]))
        with self.assertRaises(ValueError):validate_workout(workout)
        self.assertIn('error',run_encoder(workout))

    def test_flat_interval_count_and_unicode_survive_without_silent_expansion(self):
        workout={'name':'Café 🏃','sport':'running','steps':[]}
        for _ in range(4):workout['steps']+=copy.deepcopy(FIXTURE['steps'][1:3])
        decoded=decode_raw(self.encode(workout))
        self.assertEqual(decoded[1][1][6],8)
        self.assertEqual(decoded[1][1][8],'Café 🏃')
        self.assertEqual(sum(step[2] for _,step in decoded[2:] if step[1]==1),160000)
        self.assertEqual(sum(step[2] for _,step in decoded[2:] if step[1]==0),360000)

    @unittest.skipUnless(os.getenv('COACH_FIT_SDK_MODULE'),'Optional official Garmin SDK module not configured')
    def test_official_garmin_decoder_roundtrip(self):
        code=r'''
const fs=require('node:fs'),vm=require('node:vm'),{pathToFileURL}=require('node:url');
(async()=>{
  const sdk=await import(pathToFileURL(process.argv[2]).href);
  vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
  const workout=JSON.parse(fs.readFileSync(0,'utf8'));
  const bytes=WorkoutFit.encode(workout,{createdAt:'2026-11-04T12:00:00Z'});
  const decoder=new sdk.Decoder(sdk.Stream.fromByteArray(Array.from(bytes)));
  const valid=decoder.checkIntegrity();const decoded=decoder.read();
  console.log(JSON.stringify({valid,...decoded}));
})().catch(error=>{console.error(error.message);process.exitCode=1});
'''
        result=subprocess.run([NODE,'-e',code,str(ROOT/'dashboard_assets/workout-fit.js'),os.environ['COACH_FIT_SDK_MODULE']],
                input=json.dumps(FIXTURE),capture_output=True,text=True,encoding='utf-8',check=True)
        decoded=json.loads(result.stdout)
        self.assertTrue(decoded['valid'])
        self.assertEqual(decoded['errors'],[])
        steps=decoded['messages']['workoutStepMesgs']
        self.assertEqual(steps[0]['durationTime'],600)
        self.assertEqual(steps[1]['durationDistance'],400)
        self.assertEqual(steps[1]['notes'],FIXTURE['steps'][1]['notes'])
        self.assertEqual(steps[1]['customTargetHeartRateLow'],250)
        self.assertEqual(steps[2]['customTargetSpeedHigh'],2.5)
        self.assertEqual(steps[3]['durationType'],'open')


if __name__=='__main__':unittest.main()
