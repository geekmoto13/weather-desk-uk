const {test}=require('node:test');
const assert=require('node:assert/strict');
const {windows,create}=require('../static/outlook.js');
const start=Date.parse('2026-11-07T17:00:00Z')/1000, now=start+1800;
function forecast(){return {issuedAt:now,stale:false,error:null,source:'National Weather Service',periods:Array.from({length:50},(_,i)=>({start:start+i*3600,end:start+(i+1)*3600,summary:i<6?'Rain':'Cloudy',tempF:40+i,windMph:i%12,pop:i%3?0:60}))};}
test('the outlook covers exactly 48 rolling hours in four non-overlapping windows',()=>{
 const rows=windows(forecast(),now);assert.equal(rows.length,4);
 assert.equal(rows[0].start,now);assert.equal(rows[3].end,now+48*3600);
 assert.ok(rows.every(r=>r.complete&&r.coverage===43200));
 assert.equal(rows[0].end,rows[1].start);
});
test('summary duration wins while rain is shown separately as peak hourly chance',()=>{
 const row=windows(forecast(),now)[0];assert.equal(row.summary,'Cloudy');assert.equal(row.popPeak,60);
 assert.equal(row.tempMinF,40);assert.equal(row.tempMaxF,52);assert.equal(row.windMaxMph,11);
});
test('zero wind, freezing temperature and zero rain probability remain valid values',()=>{
 const f=forecast();f.periods.forEach(p=>Object.assign(p,{tempF:0,windMph:0,pop:0}));
 const r=windows(f,now)[0];assert.equal(r.tempMinF,0);assert.equal(r.windMaxMph,0);assert.equal(r.popPeak,0);
});
test('a missing hour marks only its affected window incomplete without guessed extrema',()=>{
 const f=forecast();f.periods.splice(8,1);const rows=windows(f,now);
 assert.equal(rows[0].status,'partial');assert.equal(rows[0].tempMinF,null);assert.equal(rows[0].popPeak,null);
 assert.equal(rows[1].status,'complete');
});
test('duplicate and overlapping reports never add extra coverage or overweight a condition',()=>{
 const f=forecast();f.periods.push({...f.periods[1],summary:'Snow'});
 const row=windows(f,now)[0];assert.equal(row.coverage,43200);assert.equal(row.summary,'Cloudy');
});
test('missing probabilities do not become zero and partial numeric coverage is withheld',()=>{
 const f=forecast();delete f.periods[2].pop;delete f.periods[3].tempF;f.periods[4].windMph=true;
 const r=windows(f,now)[0];assert.equal(r.popPeak,null);assert.equal(r.tempMinF,null);assert.equal(r.windMaxMph,null);
});
test('stale, failed, missing and future-issued forecasts stay unavailable',()=>{
 for(const f of [null,{...forecast(),stale:true},{...forecast(),error:'offline'},{...forecast(),issuedAt:now-43201},{...forecast(),issuedAt:now+601}])
  assert.ok(windows(f,now).every(r=>r.status==='unavailable'));
});
test('daylight saving transitions keep each window twelve real hours long',()=>{
 const at=Date.parse('2026-11-01T04:30:00Z')/1000, f=forecast();f.issuedAt=at;
 f.periods=Array.from({length:49},(_,i)=>({start:at+i*3600,end:at+(i+1)*3600,summary:'Clear',tempF:50,windMph:1,pop:0}));
 assert.ok(windows(f,at).every(r=>r.complete&&r.end-r.start===43200));
});
test('render uses local time, both temperatures and text-safe forecast descriptions',()=>{
 const ids=['outlookGrid','outlookStatus','outlookSource'];
 const make=()=>({children:[],dataset:{},textContent:'',append(el){this.children.push(el)},replaceChildren(){this.children=[]}});
 const nodes=Object.fromEntries(ids.map(id=>[id,make()]));
 const panel=create({document:{getElementById:id=>nodes[id],createElement:make},getSettings:()=>({timezone:'America/New_York'}),clock:()=>now*1000});
 panel.render(forecast());assert.equal(nodes.outlookGrid.children.length,4);
 const card=nodes.outlookGrid.children[0];assert.match(card.children[0].textContent,/12:30 PM/);
 assert.match(card.children[2].textContent,/40–52°F/);assert.match(card.children[2].textContent,/4–11°C/);
 panel.render({...forecast(),error:'offline'});assert.equal(nodes.outlookGrid.hidden,true);assert.match(nodes.outlookStatus.textContent,/unavailable/);
});

test('the horizon buffer preserves all 48 hours between fifteen-minute source checks',()=>{
 const rows=windows(forecast(),now+900);assert.ok(rows.every(r=>r.complete));
});
