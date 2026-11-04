const test=require('node:test');
const assert=require('node:assert/strict');
const past=require('../static/past.js');
const day='2026-10-08',stamp=Date.parse(day+'T23:55:00-04:00')/1000;
function payload(){return {date:day,timezone:'America/New_York',stationData:{date:day,timezone:'America/New_York',last:{timestamp:stamp,rainMonthMm:25.4},high:{value:70},low:{value:40},rainDayMm:0},
  annual:{start:'2026-01-01',through:day,complete:true,mm:25.4*26,timestamp:stamp},
  climate:{date:day,stationId:'BOSthr',schemaVersion:2,normal:{through:day,highF:65,lowF:48,precipIn:.12,rainMonthToDateIn:1,rainYearToDateIn:33},observed:{date:day,highF:68,lowF:49,precipIn:0},records:{high:{valueF:85,years:[1920]},low:{valueF:28,years:[1940]},rain:{valueIn:2,years:[1900]}},precipitation:{through:day,month:{start:'2026-10-01',through:day,observedIn:1.5},year:{start:'2026-01-01',through:day,observedIn:30}}},
  records:{date:day,through:day,throughYear:2025,startYear:1872,stationId:'BOSthr',schemaVersion:2,month:{wettest:{valueIn:8,years:[1996]},driest:{valueIn:0,years:[1947]}},year:{wettest:{valueIn:50,years:[1958]},driest:{valueIn:18,years:[1965]}}}};}
test('date arithmetic handles leap days, year changes and invalid input',()=>{
  assert.equal(past.shiftDay('2024-03-01',-1),'2024-02-29');
  assert.equal(past.shiftDay('2026-01-01',-1),'2025-12-31');
  assert.equal(past.shiftDay('2026-02-30',-1),null);
  assert.equal(past.shiftDay('',-1),null);
});
test('local dates use station timezone across UTC midnight and DST',()=>{
  assert.equal(past.localDay(Date.parse('2026-10-09T03:55Z'),'America/New_York'),day);
  assert.equal(past.localDay(Date.parse('2026-11-01T06:30Z'),'America/New_York'),'2026-11-01');
  assert.equal(past.canonicalZone('US/Eastern'),'America/New_York');
});
test('historical comparisons survive old observation timestamps',()=>{
  const m=past.model(payload(),day);
  assert.equal(m.month.comparison.kind,'at');assert.equal(m.year.comparison.kind,'below');
  assert.equal(m.refs.high.valueF,85);assert.equal(m.observed.highF,68);
  assert.equal(m.records.yearWettest.years[0],1958);assert.equal(m.year.bostonIn,30);
  assert.equal(m.rainDayMm,0);
});
test('wrong day response clears all observations and climate',()=>{
  const m=past.model(payload(),'2026-10-07');
  assert.equal(m.data,null);assert.equal(m.refs.high,null);assert.equal(m.year.stationMm,null);
  assert.equal(m.month.comparison.kind,'unknown');
});
test('station timestamp and timezone must match selected local day',()=>{
  let p=payload();p.stationData.last.timestamp=stamp+86400;
  assert.equal(past.model(p,day).data,null);
  p=payload();p.stationData.timezone='UTC';assert.equal(past.model(p,day).data,null);
  p=payload();p.stationData.last.stationTimezone='UTC';
  const m=past.model(p,day);assert.ok(m.data);assert.equal(m.month.stationMm,null);assert.equal(m.year.stationMm,null);assert.equal(m.rainDayMm,null);
});
test('normals and Boston observations use exact selected date windows',()=>{
  let p=payload();p.climate.normal.through='2026-10-09';
  assert.equal(past.model(p,day).month.normalIn,null);
  p=payload();p.climate.precipitation.year.through='2026-10-07';
  assert.equal(past.model(p,day).year.bostonIn,null);
  p=payload();p.climate.observed.date='2026-10-07';assert.equal(past.model(p,day).observed,null);
});
test('year totals require matching year start, through date and timestamp',()=>{
  for(const field of ['start','through','timestamp']){
    const p=payload();p.annual[field]=field==='timestamp'?stamp+86400:'2026-10-07';
    assert.equal(past.model(p,day).year.stationMm,null);
  }
  const p=payload();p.annual.mm=0;assert.equal(past.model(p,day).year.stationMm,0);
});
test('missing readings and invalid record windows never become zeros',()=>{
  const p=payload();p.stationData=null;p.annual=null;p.records.through='2026-10-07';
  const m=past.model(p,day);assert.equal(m.month.stationMm,null);assert.equal(m.month.comparison.kind,'unknown');
  assert.equal(m.records.yearWettest,null);assert.equal(m.rainDayMm,null);
});

test('rapid date changes ignore late responses and request failures offer retry',async()=>{
  const nodes=new Map();
  global.document={getElementById(id){
    if(!nodes.has(id))nodes.set(id,{textContent:'',value:'',hidden:false,disabled:false,dataset:{},listeners:{},addEventListener(event,fn){this.listeners[event]=fn;}});
    return nodes.get(id);
  }};
  const pending=[],pause=()=>new Promise(resolve=>setTimeout(resolve,190));
  const controller=past.create({api:url=>new Promise((resolve,reject)=>pending.push({url,resolve,reject})),
    getSettings:()=>({timezone:'America/New_York',location:'Test station'}),getStation:()=> 'A',isVisible:()=>true,onLive:()=>{},onSettings:()=>{}});
  try{
    controller.start();controller.open();await pause();
    const first=pending[0];assert.ok(first);
    nodes.get('pastDate').value=day;nodes.get('pastDate').listeners.change();await pause();
    assert.equal(pending.length,2);
    first.resolve({date:'2026-10-01',station:'A',timezone:'America/New_York',stationData:null});
    await Promise.resolve();await Promise.resolve();
    assert.equal(nodes.get('pastDate').value,day);
    assert.equal(nodes.get('pastHighF').textContent,'—');
    const current=payload();current.station='A';current.connected=true;current.loading=false;
    current.stationData.coverage={count:48,first:stamp-84600,last:stamp,dayHours:24,fullSpan:true};
    pending[1].resolve(current);await Promise.resolve();await Promise.resolve();
    assert.equal(nodes.get('pastHighF').textContent,'70.0');
    assert.equal(nodes.get('pastMonthComparison').textContent,'→ At normal');
    nodes.get('pastRetryBtn').listeners.click();await pause();
    pending[2].reject(new Error('History connection unavailable'));await Promise.resolve();await Promise.resolve();
    assert.equal(nodes.get('pastMode').textContent,'DAY UNAVAILABLE');
    assert.equal(nodes.get('pastRetryBtn').disabled,false);
    assert.equal(nodes.get('pastHighF').textContent,'—');
  }finally{controller.close();delete global.document;}
});
