// Execute the shipped chart logic against a tiny DOM/Chart adapter, without a browser or network.
const fs=require('fs'),vm=require('vm'),assert=require('node:assert/strict'),path=require('path');
const source=fs.readFileSync(path.join(__dirname,'..','dashboard_template.py'),'utf8');
const script=source.slice(source.indexOf("        const chartKey="),source.indexOf('    </script>',source.indexOf("        const chartKey="))).replaceAll('{{','{').replaceAll('}}','}');
const chartData={planned:[{date:'2026-11-23',sport:'running',km:5},{date:'2026-11-24',sport:'running',minutes:40},{date:'2026-12-02',sport:'running',km:12}],actual:[{date:'2026-11-23',sport:'running',km:4,load:30},{date:'2026-11-24',sport:'running'}],plan_start:'2026-11-23',plan_end:'2026-12-02',first_week:'2026-11-23',last_week:'2026-11-30'};
function start(saved,{blocked=false,embedded=false}={}){
  const elements={},storage={},sent=[],listeners={};
  if(saved)storage['coach.chart-preferences.v1']=JSON.stringify(saved);
  const get=id=>elements[id]??=( {value:'',open:false,addEventListener(name,fn){this[name]=fn;},getContext(){return {};},appendChild(){},setAttribute(name,value){this[name]=value;}} );
  const window={addEventListener(name,fn){listeners[name]=fn;}};window.parent=embedded?{postMessage(message){sent.push(message);}}:window;
  const ctx=vm.createContext({chartData,todayIso:'2026-11-25',chartSport:'running',chartMetric:'km',volumeChart:null,dataWindow:56,window,document:{getElementById:get,createElement:()=>({}),querySelectorAll:()=>[]},localStorage:{getItem(key){if(blocked)throw Error('blocked');return storage[key]??null;},setItem(key,value){if(blocked)throw Error('blocked');storage[key]=value;}},requestAnimationFrame:fn=>fn(),Chart:class{constructor(_ctx,config){Object.assign(this,config);}update(){}resize(){}}});
  vm.runInContext(script,ctx);
  return {ctx,elements,storage,sent,listeners,run:code=>vm.runInContext(code,ctx)};
}
let page=start();
assert.equal(page.run('volumeChart.data.labels.length'),7);
assert.equal(page.run('volumeChart.data.datasets[0].data[0]'),5);
assert.equal(page.run('volumeChart.data.datasets[0].data[1]'),null); // Time-only plan is unknown km.
assert.equal(page.run('volumeChart.data.datasets[1].data[1]'),null); // Missing recording stays unknown.
assert.equal(page.run('volumeChart.data.datasets[1].data[3]'),null); // Future recording stays blank.
page.run("switchChartView('plan');switchChartMetric('load');trends.open=true;trends.toggle();selectChartWeek('2026-11-30');");
assert.equal(page.run('volumeChart.data.labels.length'),2);
assert.equal(page.run('volumeChart.data.datasets[0].data.every(value=>value===null)'),true);
assert.match(page.elements.chartCoverage.textContent,/Planned Garmin load is unavailable/);
let restored=start(JSON.parse(page.storage['coach.chart-preferences.v1']));
assert.equal(restored.run('graphState.view'),'plan');assert.equal(restored.run('graphState.metric'),'load');assert.equal(restored.run('graphState.week'),'2026-11-30');assert.equal(restored.elements.trainingTrends.open,true);
assert.equal(start({view:'wrong'}).run('graphState.view'),'week');
assert.equal(start({...JSON.parse(page.storage['coach.chart-preferences.v1']),week:'2025-01-06'}).run('graphState.week'),'2026-11-23');
page.run("switchChartSport('all');switchChartMetric('load');");
assert.equal(start(JSON.parse(page.storage['coach.chart-preferences.v1'])).run('graphState.metric'),'load');
page.run("switchChartMetric('km');");assert.match(page.elements.chartCoverage.textContent,/Combined distance across sports/);
page=start(null,{blocked:true,embedded:true});
page.run("switchChartView('plan');switchChartMetric('load');");
assert.equal(page.sent[0].type,'coach:chart-preferences-ready');assert.equal(page.sent.at(-1).version,1);
const preferences={view:'week',metric:'km',sport:'swimming',week:'2026-11-30',open:true};
page.listeners.message({source:{},data:{type:'coach:chart-preferences',version:1,preferences}});
assert.equal(page.run('graphState.view'),'plan');
page.listeners.message({source:page.ctx.window.parent,data:{type:'coach:chart-preferences',version:1,preferences}});
assert.equal(page.run('graphState.sport'),'swimming');assert.equal(page.elements.trainingTrends.open,true);
page.listeners.message({source:page.ctx.window.parent,data:{type:'coach:chart-preferences',version:1,preferences:{...preferences,week:'2026-02-30'}}});
assert.equal(page.run('graphState.week'),'2026-11-30');
console.log('Chart views, missing data, reload persistence, blocked storage and strict iframe protocol passed.');
