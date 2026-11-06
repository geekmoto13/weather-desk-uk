const {test}=require('node:test');
const assert=require('node:assert/strict');
const {describe,create}=require('../static/tokyo.js');
const at=Date.parse('2026-11-06T03:00:00Z');
const sample=(condition='no_rain')=>({status:'ok',condition,observedAt:at/1000,temperatureC:23,rawReport:'RJTT 060300Z'});

test('the dry reply uses Shannon’s exact Radio Ham phrase',()=>{
  const view=describe(sample(),at);
  assert.equal(view.short,'It is are not raining');
  assert.equal(view.answer,'It is are not raining');
});
test('rain, snow, nearby showers and unknown precipitation have separate replies',()=>{
  for(const key of ['rain','wintry','nearby','unknown'])assert.equal(describe(sample(key),at).key,key);
  assert.match(describe(sample('rain'),at).short,/Water wet… ew nasty/);
});
test('stale, future, missing and failed reports never use the dry phrase',()=>{
  assert.equal(describe(sample(),at+5400001).key,'stale');
  assert.equal(describe(sample(),at-300001).key,'unavailable');
  assert.equal(describe({...sample(),status:'unavailable'},at).key,'unavailable');
  assert.equal(describe({},at).key,'unavailable');
});

function harness(api){
  const ids=['tokyoCallBtn','tokyoDialog','tokyoCloseBtn','tokyoRainStatus','tokyoAnswer','tokyoObserved','tokyoTemperature','tokyoReport'];
  const elements=Object.fromEntries(ids.map(id=>[id,{textContent:'',dataset:{},events:{},attrs:{},
    addEventListener(type,fn){this.events[type]=fn;},setAttribute(k,v){this.attrs[k]=v;},
    showModal(){this.open=true;},close(){this.open=false;}}]));
  const intervals=[];
  const controller=create({api,document:{getElementById:id=>elements[id]},now:()=>at,
    every:(fn,ms)=>intervals.push({fn,ms})});
  return {controller,elements,intervals};
}
test('the compact line opens an accessible Tokyo report with Japan time',async()=>{
  const h=harness(async()=>sample('rain'));
  await h.controller.refresh();
  assert.match(h.elements.tokyoObserved.textContent,/12:00 JST/);
  assert.match(h.elements.tokyoTemperature.textContent,/23.0 °C \/ 73.4 °F/);
  assert.match(h.elements.tokyoCallBtn.attrs['aria-label'],/Water wet… ew nasty/);
  h.elements.tokyoCallBtn.events.click();assert.equal(h.elements.tokyoDialog.open,true);
  h.elements.tokyoCloseBtn.events.click();assert.equal(h.elements.tokyoDialog.open,false);
});
test('a local server failure replaces the current reply with off the air',async()=>{
  let fail=false;const h=harness(async()=>{if(fail)throw Error('offline');return sample();});
  await h.controller.refresh();assert.equal(h.elements.tokyoRainStatus.textContent,'It is are not raining');
  fail=true;await h.controller.refresh();
  assert.equal(h.elements.tokyoRainStatus.textContent,'Off the air');
  assert.match(h.elements.tokyoObserved.textContent,/Last received/);
});
test('start is idempotent, polls independently and does not require local weather settings',async()=>{
  const calls=[];const h=harness(async path=>{calls.push(path);return sample();});
  h.controller.start();h.controller.start();await Promise.resolve();
  assert.deepEqual(calls,['/api/tokyo-rain']);
  assert.equal(h.intervals.length,1);assert.equal(h.intervals[0].ms,60000);
});
