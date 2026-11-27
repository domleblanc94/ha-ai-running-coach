"""Offline parent/opaque-frame graph preference contract checks."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is needed for iframe bridge checks')
class GraphPreferencesTests(unittest.TestCase):
    def test_validated_frame_persistence_and_storage_fallback(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const code=fs.readFileSync(process.argv[1],'utf8');
const key='coach.chart-preferences.v1';
const choices={view:'plan',metric:'load',sport:'all',week:'2026-11-23',open:true};
const store=new Map();
function boot(blocked=false) {
  const sent=[],events={},frameEvents={},writes=[];
  const child={postMessage:(message,target)=>sent.push({message,target})};
  const frame={contentWindow:child,addEventListener:(name,fn)=>frameEvents[name]=fn};
  const scope={document:{getElementById:id=>id==='dashboard'?frame:null},
    window:{addEventListener:(name,fn)=>events[name]=fn},
    localStorage:{getItem:k=>{if(blocked)throw Error('blocked');return store.get(k)},
      setItem:(k,v)=>{if(blocked)throw Error('blocked');writes.push([k,v]);store.set(k,v)}}};
  vm.runInNewContext(code,scope);
  return {sent,writes,load:()=>frameEvents.load(),
    message:(data,source=child)=>events.message({data,source,origin:'null'})};
}
const ready={type:'coach:chart-preferences-ready',version:1};
const change=preferences=>({type:'coach:chart-preferences-change',version:1,preferences});
let app=boot(); app.message(ready);assert.equal(app.sent.length,0);
app.message(change(choices),{});assert.equal(app.writes.length,0);
for(const invalid of [{...choices,metric:'distance'},{...choices,view:'month'},
  {...choices,week:'2026-02-31'},{...choices,week:'2026-11-24'},
  {...choices,week:'x'.repeat(10000)},{...choices,open:'true'},
  {...choices,sport:'race'},{...choices,secret:'no'}]) app.message(change(invalid));
app.message({...change(choices),version:2});
app.message({...change(choices),key:'arbitrary'});
assert.equal(app.writes.length,0);
app.message(change(choices));assert.equal(app.writes.length,1);
assert.equal(app.writes[0][0],key);
app.message(ready);app.load();assert.equal(app.sent.length,2);
assert.equal(app.sent[0].target,'*');
assert.deepEqual(JSON.parse(JSON.stringify(app.sent[0].message)),
  {type:'coach:chart-preferences',version:1,preferences:choices});
app=boot();app.message(ready);assert.equal(app.sent.length,1);
assert.deepEqual(JSON.parse(JSON.stringify(app.sent[0].message.preferences)),choices);
app=boot(true);app.message(change(choices));app.load();assert.equal(app.sent.length,1);
store.set(key,'{malformed');app=boot();app.load();assert.equal(app.sent.length,0);
store.set(key,JSON.stringify({...choices,week:'not-a-date'}));
app=boot();app.message(ready);assert.equal(app.sent.length,0);
console.log('Frame source, schema, version, refresh, regeneration and blocked storage passed');
'''
        path = Path(__file__).resolve().parents[1] / 'ai_running_coach/app/web/graph-preferences.js'
        subprocess.run([shutil.which('node'), '-e', script, str(path)], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
