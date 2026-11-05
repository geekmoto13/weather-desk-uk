const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const {windMean10m, beaufort, rainSinceText} = require('../static/readings.js');
const end = 20000;
const point = (offset, windMph, rest={}) => ({timestamp:end+offset,windMph,...rest});

test('native ten-minute mean takes priority over live speed, gust and history',()=>{
  const value=windMean10m(point(0,1,{windMean10mMph:20,gustMph:50}),[point(-600,100),point(-300,100)]);
  assert.equal(value.mph,20);assert.equal(value.estimated,false);
  assert.equal(beaufort(value.mph).force,5);
});
test('a real calm ten-minute mean remains zero',()=>{
  assert.equal(windMean10m(point(0,5,{windMean10mMph:0})).mph,0);
});
test('time weighting uses speed magnitude without cancellation of opposite directions',()=>{
  const history=[point(-600,10,{windDeg:0}),point(-450,10,{windDeg:180}),point(-300,20,{windDeg:0})];
  const value=windMean10m(point(0,20,{windDeg:180}),history);
  assert.equal(value.estimated,true);assert.equal(value.mph,16.25);
});
test('window start is interpolated and samples beyond the window are excluded',()=>{
  const value=windMean10m(point(0,20),[point(-700,0),point(-400,10),point(-100,20),point(60,400)]);
  assert.ok(Math.abs(value.mph-13.0555555556)<1e-8);
});
test('too few points, long gaps and missing current wind do not become a ten-minute mean',()=>{
  for(const rows of [[],[point(-600,10)],[point(-600,10),point(-500,10)]])
    assert.equal(windMean10m(point(0,10),rows).mph,null);
  assert.equal(windMean10m(point(0,null),[point(-600,10),point(-300,10)]).mph,null);
});
test('missing, boolean and negative native averages stay invalid',()=>{
  for(const bad of [true,-1,NaN,Infinity,'10'])assert.equal(windMean10m(point(0,10,{windMean10mMph:bad})).mph,null);
});
test('elapsed days and lower bounds retain their meaning',()=>{
  assert.equal(rainSinceText({status:'known',seconds:3*86400+3600}), '3 days');
  assert.equal(rainSinceText({status:'at_least',seconds:3*86400}), '≥ 3 days');
  assert.equal(rainSinceText({status:'known',seconds:86400,approximate:true}), '≈ 1 day');
  assert.equal(rainSinceText({status:'known',seconds:7200}), '2h · 0 days');
  assert.equal(rainSinceText({status:'unavailable',seconds:null}), 'Awaiting rain history');
});

const context={window:{},Intl,Date,Math};vm.createContext(context);
vm.runInContext(fs.readFileSync(require.resolve('../static/astro.js'),'utf8'),context);
const astro=context.window.WeatherAstro;
test('daylight and astronomical night work at both polar extremes',()=>{
  assert.equal(astro.twilight(Date.parse('2026-06-21T12:00:00Z'),89,0).stage,'Daylight');
  assert.equal(astro.twilight(Date.parse('2026-12-21T12:00:00Z'),89,0).stage,'Night');
});
test('a full day includes each twilight stage and both dawn and dusk',()=>{
  const start=Date.parse('2026-11-04T00:00:00Z'),stages=new Set(),phases=new Set();
  for(let i=0;i<1440;i++){
    const v=astro.twilight(start+i*60000,42.466,-70.95);stages.add(v.stage);if(v.phase)phases.add(v.phase);
  }
  assert.deepEqual([...stages].sort(),['Astronomical twilight','Civil twilight','Daylight','Nautical twilight','Night'].sort());
  assert.deepEqual([...phases].sort(),['dawn','dusk']);
});
test('twilight stages agree with geometric altitude boundaries throughout the day',()=>{
  const start=Date.parse('2026-03-29T00:00:00Z');
  for(let i=0;i<1440;i++){
    const t=start+i*60000,a=astro.altitude(t,52.77,-1.56,astro.sun),v=astro.twilight(t,52.77,-1.56);
    const expected=a>=-.8333?'Daylight':a>=-6?'Civil twilight':a>=-12?'Nautical twilight':a>=-18?'Astronomical twilight':'Night';
    assert.equal(v.stage,expected);
  }
});
test('sky calculation exposes twilight independently of station connectivity',()=>{
  const v=astro.calculate(Date.parse('2026-10-25T06:30:00Z'),52.77,-1.56,'Europe/London');
  assert.ok(v.twilight.stage);assert.equal(v.twilight.sunAltitude,v.sunAltitude);
});
