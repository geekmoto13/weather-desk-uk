const test=require('node:test'),assert=require('node:assert/strict');
const radio=require('../static/radio.js');
const NOW=Date.parse('2026-10-23T02:10Z')/1000;
function data(){
  const preferences={mode:'CW',power:100,txGain:0,rxGain:0,noise:'RESIDENTIAL',target:'uk'};
  const context={txLat:42.47,txLon:-70.95,destination:'England',preferences};
  return {settings:preferences,context,sources:{kp:{stale:false,value:{timestamp:NOW-3600,current:{kp:2.3},points:[{timestamp:NOW+3600,kp:4,kind:'predicted'},{timestamp:NOW+7200,kp:6,kind:'predicted'},{timestamp:NOW+10800,kp:8,kind:'observed'}]}}},
    path:{stale:false,value:{timestamp:NOW-600,context,bands:radio.BANDS.map((band,i)=>({band,mhz:[28.85,24.94,21.225,18.118,14.175,10.12,7.15,5.33,3.65,1.9][i],hours:Array.from({length:24},()=>({bcr:i===6?80:i===7?50:i===8?49:0,snr:10+i}))}))}}};
}
test('Maidenhead grid handles Lynn, world boundaries and invalid coordinates',()=>{
  assert.equal(radio.grid(42.4668,-70.9495),'FN42ml');assert.equal(radio.grid(-90,-180),'AA00aa');
  assert.equal(radio.grid(90,180),'RR99xx');assert.equal(radio.grid(null,1),'—');
});
test('GOES long-wave flux is formatted with the correct flare class',()=>{
  assert.equal(radio.xrayClass(6.3e-7),'B6.3');assert.equal(radio.xrayClass(1e-6),'C1.0');
  assert.equal(radio.xrayClass(2.5e-5),'M2.5');assert.equal(radio.xrayClass(1e-4),'X1.0');
  assert.equal(radio.xrayClass(null),'—');assert.equal(radio.xrayClass(0),'A0.0');
});
test('source freshness ages observations and preserves missing versus zero',()=>{
  const p={sources:{scales:{stale:false,value:{timestamp:NOW-100,levels:{G:0}}}}};
  assert.equal(radio.source(p,'scales',NOW).stale,false);
  assert.equal(radio.source(p,'scales',NOW+1000).stale,true);
  p.sources.scales.error='offline';assert.equal(radio.source(p,'scales',NOW).stale,true);
  assert.equal(radio.source(p,'missing',NOW).value,null);
});
test('all ten bands use the UTC model hour and the lower limit is a tested band',()=>{
  const p=radio.pathModel(data(),NOW);
  assert.equal(p.valid,true);assert.equal(p.rows.length,10);assert.equal(p.hour,2);
  assert.equal(p.lowest.band,'60m');assert.equal(p.highest.band,'40m');assert.equal(p.best.band,'40m');
  assert.equal(p.lowest.mhz,5.33);assert.equal(p.count,24);
});
test('future model starts label the next hour and old models limit the horizon',()=>{
  let d=data();d.path.value.timestamp=NOW+3000;
  let p=radio.pathModel(d,NOW);assert.equal(p.hour,3);assert.equal(p.time,Date.parse('2026-10-23T03:00Z')/1000);
  d=data();d.path.value.timestamp=NOW-7800;p=radio.pathModel(d,NOW);
  assert.equal(p.count,22);
});
test('wrong path, settings, stale or too-future model never produces recommendations',()=>{
  for(const mutate of [d=>d.path.stale=true,d=>d.path.error='offline',d=>d.path.value.timestamp=NOW-20000,
    d=>d.path.value.timestamp=NOW+7200,d=>d.path.value.context={...d.context,txLat:1},d=>d.settings={...d.settings,power:10}]){
    const d=data();mutate(d);const p=radio.pathModel(d,NOW);assert.equal(p.valid,false);assert.equal(p.lowest,null);
  }
});
test('zero and missing reliability do not become a usable-band recommendation',()=>{
  const d=data();for(const band of d.path.value.bands)for(const cell of band.hours)cell.bcr=0;
  const p=radio.pathModel(d,NOW);assert.equal(p.lowest,null);assert.equal(p.best,null);
  assert.equal(radio.category(null),'unknown');assert.equal(radio.category(0),'poor');assert.equal(radio.category(50),'fair');
});
test('Kp outlook includes actual future predictions and rejects stale data',()=>{
  let d=data();assert.deepEqual(radio.futureKp(d,NOW).map(p=>p.kp),[4,6]);
  d.sources.kp.stale=true;assert.deepEqual(radio.futureKp(d,NOW),[]);
});
test('daily cards use only NOAA with valid zero, saved data and NOAA fallback',()=>{
  const feed=(v,stale=false)=>({stale,value:{timestamp:NOW-7200,date:'2026-10-22',...v}});
  const d={sources:{daily:feed({sfi:106,sn:0}),wwv:feed({sfi:107,aIndex:0}),hamqsl:feed({sfi:999,sn:999,aIndex:999})}};
  let picked=radio.noaaDaily(d,NOW);
  assert.equal(picked.solar.value.sfi,106);assert.equal(picked.spots.value.sn,0);assert.equal(picked.aIndex.value.aIndex,0);
  d.sources.daily.stale=true;picked=radio.noaaDaily(d,NOW);assert.equal(picked.solar.value.sfi,107);
  delete d.sources.daily;delete d.sources.wwv;picked=radio.noaaDaily(d,NOW);
  assert.equal(picked.solar.value,null);assert.equal(picked.spots.value,null);assert.equal(picked.aIndex.value,null);
});
test('NOAA geomagnetic levels keep G0 distinct from missing observations',()=>{
  assert.equal(radio.geomagnetic(0),'Below G1');assert.equal(radio.geomagnetic(2),'G2 · Moderate');
  for(const value of [null,undefined,NaN,-1,6,true])assert.equal(radio.geomagnetic(value),'—');
});
test('three-hour path columns wrap UTC midnight and stop at the model horizon',()=>{
  const p=radio.pathModel(data(),NOW);p.hour=23;p.count=5;
  const row={hours:Array.from({length:24},(_,i)=>({bcr:i}))};
  assert.equal(radio.pathHour(p,row,0).bcr,23);assert.equal(radio.pathHour(p,row,3).bcr,2);
  assert.equal(radio.pathHour(p,row,6),null);assert.equal(radio.pathHour({...p,valid:false},row,0),null);
});
