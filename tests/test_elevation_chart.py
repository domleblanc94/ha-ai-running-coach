"""Offline SVG axis and gap regressions; no map, DEM or AI requests."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which('node'), 'Node is needed for SVG renderer checks')
class ElevationChartTests(unittest.TestCase):
    def test_units_full_distance_signed_flat_and_unknown_samples(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Node {
  constructor(){this.children=[];this.attributes={};this.clientWidth=308;this.textContent='';}
  append(node){this.children.push(node);}
  replaceChildren(){this.children=[];}
  setAttribute(name,value){this.attributes[name]=String(value);}
}
const scope={window:{},document:{createElementNS:()=>new Node()}};vm.createContext(scope);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),scope);
const {model,render}=scope.window.CoachElevationChart;
const point=(distance,elevation,segment=0)=>({distance_m:distance,elevation_m:elevation,segment});
const input=[point(0,null),point(2000,-30),point(3000,-10),point(4000,null),point(5000,10),point(6000,30,1),point(7000,20,1),point(10000,null)];
const before=JSON.stringify(input),chart=model(input,10000,308);
assert.equal(JSON.stringify(input),before);assert.equal(chart.end,10000);
assert.equal(chart.xTicks[0].label,'0');assert.equal(chart.xTicks.at(-1).label,'10');
assert.ok(chart.yTicks.some(t=>t.value<0&&t.label.startsWith('-')));
assert.equal(chart.runs.length,3);assert.equal(chart.runs[0].length,2);assert.equal(chart.runs[1].length,1);assert.equal(chart.runs[2].length,2);
assert.ok(chart.runs[0][0].x>chart.box.left);assert.ok(chart.runs[2].at(-1).x<chart.box.right);
assert.equal(chart.hasProfile,true);
const svg=new Node();render(svg,input,10000);
const texts=svg.children.map(n=>n.textContent);
assert.ok(texts.includes('Distance (km)'));assert.ok(texts.includes('Elevation (m)'));
assert.ok(svg.attributes['aria-label'].includes('kilometres'));
const xLabels=svg.children.filter(n=>n.attributes.class==='elevation-tick elevation-x-tick');
assert.equal(xLabels[0].attributes['text-anchor'],'start');assert.equal(xLabels.at(-1).attributes['text-anchor'],'end');
assert.equal(svg.children.filter(n=>n.attributes.class==='elevation-line').length,2);
assert.equal(svg.children.filter(n=>n.attributes.class==='elevation-sample').length,1);
const flat=model([point(0,-15),point(1000,-15)],1000,990);
assert.equal(flat.runs[0][0].y,flat.runs[0][1].y);
assert.ok(flat.yTicks[0].value<-15&&flat.yTicks.at(-1).value>-15);
const unknown=model([point(0,null),point(12000,null)],12000,308);
assert.equal(unknown.yTicks.length,0);assert.equal(unknown.runs.length,0);assert.equal(unknown.xTicks.at(-1).label,'12');
render(svg,[point(0,null),point(12000,null)],12000);
assert.ok(svg.children.some(n=>n.textContent==='Elevation unknown'));
assert.ok(!svg.children.some(n=>n.attributes.class==='elevation-line'));
const one=model([point(0,null),point(800,5),point(1000,null)],1000,308);
assert.equal(one.knownCount,1);assert.equal(one.hasProfile,false);assert.equal(one.runs[0].length,1);
const empty=model([],0,308);assert.equal(empty.hasProfile,false);assert.equal(empty.xTicks.length,1);
for(const [metres,expected] of [[10,'0.01'],[8792.314,'8.79'],[37500,'37.5'],[1000000,'1,000']]) {
  const sized=model([point(0,5),point(metres,10)],metres,308);
  assert.equal(sized.xTicks.at(-1).label,expected);
  assert.equal(sized.xTicks.at(-1).value,metres);assert.equal(sized.xTicks.at(-1).position,sized.box.right);
  for(const tick of sized.xTicks)assert.ok(Math.abs(Number(tick.label.replaceAll(',',''))*1000-tick.value)<=(metres<1000?.5:5));
}
for(const sample of [chart,flat,unknown,one,empty,model([point(0,0),point(10,.00001)],10,308)]) {
  for(const tick of [...sample.xTicks,...sample.yTicks])assert.ok(Number.isFinite(tick.position));
  for(const run of sample.runs)for(const p of run)assert.ok(Number.isFinite(p.x)&&Number.isFinite(p.y));
  assert.equal(new Set(sample.xTicks.map(t=>t.label)).size,sample.xTicks.length);
}
console.log('Elevation axes, units, partial distances, segment gaps and flat/unknown states passed');
'''
        path = Path(__file__).resolve().parents[1] / 'ai_running_coach/app/web/race-elevation-chart.js'
        subprocess.run([shutil.which('node'), '-e', script, str(path)], check=True, capture_output=True, text=True)


if __name__ == '__main__':
    unittest.main()
