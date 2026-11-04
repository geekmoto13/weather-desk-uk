const test=require('node:test');
const assert=require('node:assert/strict');
const climate=require('../static/climate.js');
const {beacon}=require('../static/design.js');
const calendar=require('../static/calendar.js');
const alerts=require('../static/alerts.js');
test('UK rainfall compares the displayed 0.1 millimetre amounts',()=>{
  assert.equal(climate.compare(1.04,1.02/25.4).kind,'at');
  assert.equal(climate.compare(1.11,1.02/25.4).kind,'above');
  assert.equal(climate.compare(.94,1.02/25.4).kind,'below');
  assert.equal(climate.compare(null,0).kind,'unknown');
});
test('UK calendar dates handle BST and repeated autumn hours',()=>{
  assert.equal(calendar.dayInZone(Date.parse('2026-10-24T23:30:00Z'),'Europe/London'),'2026-10-25');
  assert.equal(calendar.dayInZone(Date.parse('2026-10-25T01:30:00Z'),'Europe/London'),'2026-10-25');
  assert.equal(climate.bostonDay(Date.parse('2026-10-24T23:30:00Z')/1000),'2026-10-25');
});
test('UK model summaries still produce all four beacon signals',()=>{
  const now=1792874000;
  for(const [summary,pop,kind,flash]of [['Clear',0,'clear',false],['Overcast',10,'cloud',true],['Rain Showers',70,'rain',false],['Freezing Rain',80,'snow',true]]){
    const b=beacon({issuedAt:now,periods:[{start:now,end:now+43200,summary,pop}]},now);
    assert.equal(b.kind,kind);assert.equal(b.flash,flash);
  }
});
test('UK warning failures remain unknown and warning colours are preserved',()=>{
  const now=1792874000;
  assert.equal(alerts.view({alerts:[],checkedAt:now,error:'offline'},now).status,'unavailable');
  assert.equal(alerts.view({alerts:[],checkedAt:now},now).title,'No active Met Office warnings');
  const view=alerts.view({checkedAt:now,alerts:[{event:'Yellow wind warning',level:'yellow',validUntil:now+600}]},now);
  assert.equal(view.status,'active');assert.equal(view.tone,'yellow');
  assert.equal(alerts.view({checkedAt:now-181,alerts:[{event:'Yellow wind warning',level:'yellow',validUntil:now+600}]},now).alerts.length,0);
});
