"""Sport-specific FIT wire checks and independent pinned-Garmin decoding."""
import base64
import copy
import hashlib
import json
import os
import subprocess
import unittest

from workout_schema import validate_workout
from test_workout_fit import FIXTURE, NODE, ROOT, decode_raw, run_encoder


CYCLE = {'name': 'Synthetic bike intervals', 'sport': 'cycling', 'steps': [
    {'name': 'Warm up', 'intensity': 'warmup', 'duration': {'type': 'time', 'seconds': 300}, 'target': {'type': 'open'}},
    {'name': 'Steady power', 'intensity': 'active', 'duration': {'type': 'time', 'seconds': 600}, 'target': {'type': 'power', 'lowWatts': 180, 'highWatts': 220}},
    {'name': 'Cadence', 'notes': 'Keep the effort comfortable.', 'intensity': 'active', 'duration': {'type': 'distance', 'metres': 2000}, 'target': {'type': 'cadence', 'lowRpm': 80, 'highRpm': 95}},
    {'name': 'Finish', 'intensity': 'cooldown', 'duration': {'type': 'open'}, 'target': {'type': 'heart_rate', 'lowBpm': 100, 'highBpm': 120}},
]}
POOL = {'name': 'Synthetic pool swim', 'sport': 'swimming', 'subSport': 'lap_swimming',
        'poolLengthMetres': 25, 'poolLengthUnit': 'm', 'steps': [
    {'name': 'Swim', 'intensity': 'active', 'duration': {'type': 'distance', 'metres': 100}, 'target': {'type': 'stroke', 'stroke': 'freestyle'}},
    {'name': 'Rest', 'intensity': 'rest', 'duration': {'type': 'time', 'seconds': 20}, 'target': {'type': 'open'}},
    {'name': 'Drill', 'intensity': 'active', 'duration': {'type': 'distance', 'metres': 25}, 'target': {'type': 'stroke', 'stroke': 'drill'}},
]}


def yard_pool():
    workout = copy.deepcopy(POOL)
    workout.update(poolLengthMetres=22.86, poolLengthUnit='yd')
    workout['steps'][0]['duration']['metres'] = 91.44
    workout['steps'][2]['duration']['metres'] = 22.86
    return workout


class MultisportWorkoutSchemaTests(unittest.TestCase):
    def test_explicit_sport_data_survives_without_defaults_or_mutation(self):
        for fixture in (CYCLE, POOL, yard_pool()):
            original = copy.deepcopy(fixture)
            result = validate_workout(fixture)
            self.assertEqual(result, original)
            result['steps'][0]['name'] = 'changed'
            self.assertEqual(fixture, original)

    @staticmethod
    def invalid_examples():
        invalid = []
        for sport in ('multisport', 'triathlon', 'open_water', ['cycling']):
            value = copy.deepcopy(CYCLE); value['sport'] = sport; invalid.append(value)
        for field in ('subSport', 'poolLengthMetres', 'poolLengthUnit'):
            value = copy.deepcopy(POOL); value.pop(field); invalid.append(value)
        for length in (0, -25, True, '25', float('inf'), 655.35):
            value = copy.deepcopy(POOL); value['poolLengthMetres'] = length; invalid.append(value)
        for unit in ('yards', None, ['m']):
            value = copy.deepcopy(POOL); value['poolLengthUnit'] = unit; invalid.append(value)
        value = copy.deepcopy(POOL); value['subSport'] = 'open_water'; invalid.append(value)
        value = copy.deepcopy(CYCLE); value['poolLengthMetres'] = 25; invalid.append(value)
        for target in ({'type': 'power', 'lowWatts': 0, 'highWatts': 200},
                       {'type': 'power', 'lowWatts': 200, 'highWatts': 100},
                       {'type': 'power', 'lowWatts': 100.5, 'highWatts': 200},
                       {'type': 'power', 'lowWatts': 100, 'highWatts': 0xFFFFFFFF},
                       {'type': 'power', 'lowPercentFTP': 70, 'highPercentFTP': 80},
                       {'type': 'cadence', 'lowRpm': True, 'highRpm': 90},
                       {'type': 'cadence', 'lowRpm': 100, 'highRpm': 90},
                       {'type': 'stroke', 'stroke': 'freestyle'}):
            value = copy.deepcopy(CYCLE); value['steps'][0]['target'] = target; invalid.append(value)
        for target in ({'type': 'heart_rate', 'lowBpm': 100, 'highBpm': 120},
                       {'type': 'speed', 'lowMps': 1, 'highMps': 2},
                       {'type': 'power', 'lowWatts': 100, 'highWatts': 200},
                       {'type': 'stroke', 'stroke': 'unknown'},
                       {'type': 'stroke', 'stroke': ['freestyle']}):
            value = copy.deepcopy(POOL); value['steps'][0]['target'] = target; invalid.append(value)
        value = copy.deepcopy(FIXTURE); value['steps'][0]['target'] = {'type': 'cadence', 'lowRpm': 80, 'highRpm': 90}; invalid.append(value)
        return invalid

    def test_wrong_sport_missing_pool_and_ambiguous_targets_are_rejected(self):
        for value in self.invalid_examples():
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_workout(value)


@unittest.skipUnless(NODE, 'Node.js is required for browser FIT checks')
class MultisportFitEncoderTests(unittest.TestCase):
    def encode(self, fixture):
        response = run_encoder(fixture)
        self.assertNotIn('error', response)
        return base64.b64decode(response['bytes'])

    def test_legacy_running_fixture_remains_byte_identical(self):
        self.assertEqual(hashlib.sha256(self.encode(FIXTURE)).hexdigest(),
                         '53ac21a233f3615c8d524de637ba32d3cb1b24cabeeae994f2ff9536cacd74be')

    def test_python_javascript_validation_agrees_across_sports(self):
        for fixture in (CYCLE, POOL, yard_pool()):
            self.assertEqual(run_encoder(fixture, 'validate')['workout'], validate_workout(fixture))
        for value in MultisportWorkoutSchemaTests.invalid_examples():
            if value.get('poolLengthMetres') == float('inf'):
                continue  # Non-finite JavaScript values are tested below without JSON coercion.
            with self.subTest(value=value):
                self.assertIn('error', run_encoder(value, 'validate'))
        script = "const fs=require('fs'),vm=require('vm');vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));const w=JSON.parse(process.argv[2]);w.poolLengthMetres=Infinity;try{WorkoutFit.validate(w);process.exit(1);}catch(e){console.log(e.message);}"
        result = subprocess.run([NODE, '-e', script, str(ROOT/'dashboard_assets/workout-fit.js'), json.dumps(POOL)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_cycling_wire_targets_are_absolute_watts_and_cadence(self):
        messages = decode_raw(self.encode(CYCLE))
        self.assertEqual([number for number, _ in messages], [0, 26, 27, 27, 27, 27])
        self.assertEqual(messages[1][1][4], 2)
        power, cadence, heart_rate = [message for _, message in messages[3:]]
        self.assertEqual((power[3], power[4], power[5], power[6]), (4, 0, 1180, 1220))
        self.assertEqual((cadence[3], cadence[4], cadence[5], cadence[6]), (3, 0, 80, 95))
        self.assertEqual(cadence[2], 200000)
        self.assertEqual((heart_rate[5], heart_rate[6]), (200, 220))

    def test_pool_wire_units_and_strokes_match_metric_and_yard_pools(self):
        for fixture, length, units, swim_distance in ((POOL, 2500, 0, 10000), (yard_pool(), 2286, 1, 9144)):
            messages = decode_raw(self.encode(fixture))
            workout = messages[1][1]
            self.assertEqual((workout[4], workout[11], workout[14], workout[15]), (5, 17, length, units))
            self.assertEqual(workout[6], 3)
            swim, rest, drill = [message for _, message in messages[2:]]
            self.assertEqual((swim[1], swim[2], swim[3], swim[4], swim[5], swim[6]), (1, swim_distance, 11, 0, 0, 0))
            self.assertEqual((rest[1], rest[2], rest[3], rest[7]), (0, 20000, 2, 1))
            self.assertEqual((drill[3], drill[4], drill[5], drill[6]), (11, 4, 0, 0))

    @unittest.skipUnless(os.getenv('COACH_FIT_SDK_MODULE'), 'Official Garmin SDK module not configured')
    def test_official_garmin_decoder_independently_reads_all_new_sports(self):
        script = r'''
const fs=require('node:fs'),vm=require('node:vm'),{pathToFileURL}=require('node:url');
(async()=>{
 const sdk=await import(pathToFileURL(process.argv[2]).href);
 vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));
 const fixtures=JSON.parse(fs.readFileSync(0,'utf8'));
 const results=fixtures.map(workout=>{
  const bytes=WorkoutFit.encode(workout,{createdAt:'2026-11-04T12:00:00Z'});
  const decoder=new sdk.Decoder(sdk.Stream.fromByteArray(Array.from(bytes)));
  return {valid:decoder.checkIntegrity(),...decoder.read()};
 });
 console.log(JSON.stringify(results));
})().catch(error=>{console.error(error.message);process.exitCode=1;});
'''
        cycle = copy.deepcopy(CYCLE)
        cycle['steps'].append({'intensity':'active','duration':{'type':'time','seconds':12.345},'target':{'type':'speed','lowMps':5,'highMps':7.5}})
        all_strokes = copy.deepcopy(POOL)
        all_strokes['steps'] = [{'intensity':'active','duration':{'type':'distance','metres':112.37},'target':{'type':'stroke','stroke':stroke}}
                                for stroke in ('freestyle','backstroke','breaststroke','butterfly','drill','mixed','im')]
        result = subprocess.run([NODE, '-e', script, str(ROOT/'dashboard_assets/workout-fit.js'), os.environ['COACH_FIT_SDK_MODULE']],
                                input=json.dumps([cycle, POOL, yard_pool(), all_strokes]), capture_output=True, text=True, encoding='utf-8', check=True)
        decoded = json.loads(result.stdout)
        for item in decoded:
            self.assertTrue(item['valid']); self.assertEqual(item['errors'], [])
            self.assertEqual(item['messages']['fileIdMesgs'][0]['type'], 'workout')
        cycle = decoded[0]['messages']
        self.assertEqual(cycle['workoutMesgs'][0]['sport'], 'cycling')
        self.assertEqual(cycle['workoutMesgs'][0]['numValidSteps'], 5)
        self.assertEqual(cycle['workoutStepMesgs'][0]['targetType'], 'open')
        self.assertEqual(cycle['workoutStepMesgs'][1]['customTargetPowerLow'], 1180)
        self.assertEqual(cycle['workoutStepMesgs'][1]['customTargetPowerHigh'], 1220)
        self.assertEqual(cycle['workoutStepMesgs'][2]['customTargetCadenceHigh'], 95)
        self.assertEqual(cycle['workoutStepMesgs'][3]['customTargetHeartRateLow'], 200)
        self.assertEqual(cycle['workoutStepMesgs'][3]['customTargetHeartRateHigh'], 220)
        self.assertEqual(cycle['workoutStepMesgs'][4]['customTargetSpeedLow'], 5)
        self.assertEqual(cycle['workoutStepMesgs'][4]['customTargetSpeedHigh'], 7.5)
        self.assertEqual(cycle['workoutStepMesgs'][4]['durationTime'], 12.345)
        for item, length, units, distance in ((decoded[1], 25, 'metric', 100), (decoded[2], 22.86, 'statute', 91.44)):
            workout = item['messages']['workoutMesgs'][0]
            self.assertEqual((workout['sport'], workout['subSport'], workout['poolLength'], workout['poolLengthUnit']), ('swimming', 'lapSwimming', length, units))
            steps = item['messages']['workoutStepMesgs']
            self.assertEqual(steps[0]['targetStrokeType'], 'freestyle')
            self.assertEqual(steps[0]['durationDistance'], distance)
            self.assertEqual(steps[2]['targetStrokeType'], 'drill')
        steps = decoded[3]['messages']['workoutStepMesgs']
        self.assertEqual([step['targetStrokeType'] for step in steps], ['freestyle','backstroke','breaststroke','butterfly','drill','mixed','im'])
        self.assertEqual([step['durationDistance'] for step in steps], [112.37]*7)

