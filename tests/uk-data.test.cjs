const test=require('node:test');
const assert=require('node:assert/strict');
const data=require('../static/uk-data.js');
test('public rainfall retains lower-bound distinction and never shows missing as zero',()=>{
  assert.equal(data.rainText({mm:544.6,complete:false}),'≥ 544.6 mm');
  assert.equal(data.rainText({mm:0,complete:true}),'0.0 mm');
  assert.equal(data.rainText({mm:null,complete:false}),'Unavailable');
  assert.equal(data.coverage({mm:12,missing:1,complete:false}),'1 missing 15-min interval');
  assert.equal(data.coverage({mm:12,missing:0,complete:true}),'All intervals present');
});
