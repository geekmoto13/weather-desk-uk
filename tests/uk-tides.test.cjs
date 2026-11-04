const test=require('node:test'),assert=require('node:assert/strict');
const {chart}=require('../static/uk-tides.js');
test('observed tide curve leaves missing half-hour-plus intervals blank',()=>{
 const now=1700000000,svg=chart([{timestamp:now-7200,heightM:-2},{timestamp:now-6300,heightM:-1},{timestamp:now-1800,heightM:1},{timestamp:now-900,heightM:2}],now);
 assert.equal((svg.match(/stroke="var\(--mint\)"/g)||[]).length,2);assert.ok(svg.includes('24 hours ago'));assert.ok(svg.includes('Now'));
});
test('invalid or stale tide readings never create a chart',()=>{
 assert.equal(chart([{timestamp:1,heightM:2},{timestamp:1700000000,heightM:NaN}],1700000000),'');
});
