const assert=require('node:assert/strict');
const {beacon}=require('../static/design.js');
const now=1792700000;
function outlook(blocks){
  let start=now;
  return {issuedAt:now-600,periods:blocks.map(([summary,hours,pop=0])=>{
    const p={start,end:start+hours*3600,summary,pop};start=p.end;return p;
  })};
}
function result(blocks){return beacon(outlook(blocks),now);}

// A short cloudier interval no longer controls an otherwise clear outlook.
let b=result([['Mostly Clear',9],['Partly Cloudy',3]]);
assert.equal(b.kind,'clear');assert.equal(b.flash,false);
assert.deepEqual(b.hours,{clear:9,cloud:3,rain:0,snow:0});
assert.equal(b.detail,'Clear 9h · Clouds 3h');
assert.equal(result([['Sunny',11],['Partly Cloudy',1]]).kind,'clear');
assert.equal(result([['Clear',4],['Mostly Cloudy',8]]).kind,'cloud');
assert.equal(result([['Mostly Sunny',12]]).kind,'clear');
assert.equal(result([['Partly Sunny',12]]).kind,'cloud');
assert.equal(result([['Fog',8],['Mostly Clear',4]]).kind,'cloud');

// Rain and snow follow the same prevailing-time policy, including mixed outlooks.
b=result([['Sunny',9],['Chance Rain Showers',3,60]]);
assert.equal(b.kind,'clear');assert.equal(b.hours.rain,3);
assert.match(b.detail,/Rain 3h/);
b=result([['Cloudy',3],['Rain',8,90],['Chance Snow',1,50]]);
assert.equal(b.kind,'rain');assert.equal(b.flash,false);assert.equal(b.color,'red');
assert.match(b.detail,/Snow\/ice 1h/);
b=result([['Rain',2,70],['Snow',8,80],['Mostly Clear',2]]);
assert.equal(b.kind,'snow');assert.equal(b.flash,true);
assert.equal(result([['Clear',4],['Cloudy',3],['Rain',5,70]]).kind,'rain');
assert.equal(result([['Slight Chance Rain Showers',12,20]]).kind,'cloud');
assert.equal(result([['Snow',12,29]]).kind,'cloud');
assert.equal(result([['Snow',12,30]]).kind,'snow');
assert.equal(result([['Rain',12,null]]).kind,'rain');
assert.equal(result([['Chance Rain',12,null]]).kind,'cloud');
assert.equal(result([['Freezing Rain',12,80]]).kind,'snow');

// Frost can replace the NWS sky wording. Its sky icon establishes clear/cloudy;
// frost is still disclosed and is not counted as falling snow or freezing rain.
const frost=outlook([['Clear',11],['Areas Of Frost',1]]);
frost.periods[1].sky='clear';
b=beacon(frost,now);
assert.equal(b.kind,'clear');assert.equal(b.flash,false);
assert.deepEqual(b.hours,{clear:12,cloud:0,rain:0,snow:0});
assert.match(b.detail,/Frost in outlook/);
frost.periods[1].sky='cloud';
assert.equal(beacon(frost,now).hours.cloud,1);
const cloudyFrost=outlook([['Areas Of Frost',12]]);cloudyFrost.periods[0].sky='cloud';
assert.equal(beacon(cloudyFrost,now).kind,'cloud');
const frostWithoutSky=outlook([['Areas Of Frost',12]]);
assert.equal(beacon(frostWithoutSky,now).kind,'unknown');
assert.match(beacon(frostWithoutSky,now).detail,/Areas Of Frost/);
const wetSky=outlook([['Freezing Rain',12,80]]);wetSky.periods[0].sky='clear';
assert.equal(beacon(wetSky,now).kind,'snow','A sky icon cannot override recognized precipitation');
const fogSky=outlook([['Patchy Fog And Frost',12]]);fogSky.periods[0].sky='clear';
assert.equal(beacon(fogSky,now).kind,'cloud','Fog wording has priority over a clear icon');
const laterFrost=outlook([['Clear',12],['Areas Of Frost',1]]);laterFrost.periods[1].sky='clear';
assert.doesNotMatch(beacon(laterFrost,now).detail,/Frost/,'Do not mention frost outside the 12h window');
for(const summary of [null,undefined,'','  ']){
  const missing=outlook([[summary,12]]);missing.periods[0].sky='clear';
  assert.equal(beacon(missing,now).kind,'unknown','A sky icon cannot replace a missing forecast');
}

// Exact ties use snow, rain, clouds, then clear, never an arbitrary input order.
assert.equal(result([['Clear',6],['Partly Cloudy',6]]).kind,'cloud');
assert.equal(result([['Rain',6,70],['Clear',6]]).kind,'rain');
assert.equal(result([['Rain',6,70],['Snow',6,70]]).kind,'snow');
assert.equal(result([['Clear',3],['Cloudy',3],['Rain',3,70],['Snow',3,70]]).kind,'snow');

// Weight by time, clip both ends, and ignore forecast hours outside the 12h window.
const weighted={issuedAt:now-600,periods:[
  {start:now-1800,end:now+19800,summary:'Clear',pop:0},
  {start:now+19800,end:now+21600,summary:'Cloudy',pop:0},
  {start:now+21600,end:now+43200,summary:'Clear',pop:0},
  {start:now+43200,end:now+86400,summary:'Snow',pop:80}
]};
b=beacon(weighted,now);
assert.equal(b.kind,'clear');assert.equal(b.hours.clear,11.5);assert.equal(b.hours.cloud,.5);
assert.equal(b.hours.snow,0);
weighted.periods.push({...weighted.periods[1]},{...weighted.periods[1]});
assert.equal(beacon(weighted,now).hours.cloud,.5,'Duplicate rows cannot inflate the cloud vote');
const unsorted={...weighted,periods:[...weighted.periods].reverse()};
assert.deepEqual(beacon(unsorted,now).hours,b.hours);
const cached=outlook([['Mostly Clear',10],['Cloudy',14]]);
b=beacon(cached,now+900);
assert.equal(b.kind,'clear');assert.equal(b.hours.clear,9.75);assert.equal(b.hours.cloud,2.25);
assert.equal(Object.values(b.hours).reduce((a,c)=>a+c,0),12);
const tiny=result([['Mostly Clear',11.99],['Cloudy',.01]]);
assert.match(tiny.detail,/Clouds <0.1h/);

// Missing, stale or unrecognised data must not imply clear weather.
assert.equal(result([['Clear',2]]).kind,'unknown');
const gap=outlook([['Clear',12]]);gap.periods[0].start=now+1800;
assert.equal(beacon(gap,now).kind,'unknown');
assert.equal(result([['Alien Weather',12]]).kind,'unknown');
assert.equal(beacon({...cached,stale:true},now).kind,'unknown');
assert.equal(beacon({...cached,error:'offline'},now).kind,'unknown');
assert.equal(beacon({...cached,issuedAt:now-43201},now).kind,'unknown');
assert.equal(beacon({...cached,periods:null},now).kind,'unknown');
assert.equal(beacon({...cached,demo:true},now).demo,true);
console.log('Prevailing 12-hour beacon: duration weights, all four signals, short cloud/rain/snow intervals, ties, clipping, cache rollover, duplicates and missing/stale data passed.');
