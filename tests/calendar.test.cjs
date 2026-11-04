const test=require('node:test');
const assert=require('node:assert/strict');
const calendar=require('../static/calendar.js');
const climate=require('../static/climate.js');
const past=require('../static/past.js');

test('calendar month navigation handles years and leap months',()=>{
  assert.equal(calendar.shiftMonth('2026-01',-1),'2025-12');
  assert.equal(calendar.shiftMonth('2026-12',1),'2027-01');
  assert.equal(calendar.shiftMonth('2028-02',1),'2028-03');
});
test('California references use the local date at Eastern midnight',()=>{
  const stamp=Date.parse('2026-10-23T05:00:00Z')/1000;
  assert.equal(calendar.dayInZone(stamp*1000,'America/Los_Angeles'),'2026-10-22');
  const site={schemaVersion:2,stationId:'USW00093134',timezone:'America/Los_Angeles',date:'2026-10-22',normal:{through:'2026-10-22',rainMonthToDateIn:2},records:{high:{valueF:90,years:[1950]}}};
  assert.equal(climate.reference(site,null,stamp,'USW00093134').high.valueF,90);
  assert.equal(climate.reference(site,null,stamp,'BOSthr').high,null);
});
test('rain records and historical model reject another climate station',()=>{
  const stamp=Date.parse('2026-10-22T18:00:00Z')/1000;
  const payload={schemaVersion:2,stationId:'USW00093134',timezone:'America/Los_Angeles',date:'2026-10-22',through:'2026-10-22',throughYear:2025,month:{wettest:{valueIn:5,years:[1950]}}};
  assert.equal(climate.rainRecords(payload,stamp,'USW00093134','America/Los_Angeles').monthWettest.valueIn,5);
  assert.equal(climate.rainRecords(payload,stamp,'BOSthr','America/Los_Angeles').monthWettest,null);
  assert.equal(past.model({date:'2026-10-22',timezone:'America/Los_Angeles',climate:{...payload,normal:{through:'2026-10-22'}}},'2026-10-22','USW00093134').climate.stationId,'USW00093134');
});
test('Ambient Pacific aliases match the local historical date',()=>{
  const day='2026-10-22';
  const stationData={date:day,timezone:'America/Los_Angeles',last:{timestamp:Date.parse(day+'T20:00:00Z')/1000}};
  assert.ok(past.model({date:day,timezone:'US/Pacific',stationData},day).data);
});
