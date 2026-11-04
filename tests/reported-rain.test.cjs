const {test}=require('node:test');
const assert=require('node:assert/strict');
const {reportedRain}=require('../static/readings.js');

test('a measured dry zero is displayed as a rain report',()=>{
  assert.deepEqual(reportedRain({rainLastHourMm:0}),[{period:'past hour',mm:0}]);
});

test('missing or invalid rain values do not become reported zero',()=>{
  for(const value of [null,undefined,true,false,'0',NaN,Infinity,-1,3001])
    assert.deepEqual(reportedRain({rainLastHourMm:value}),[]);
  assert.deepEqual(reportedRain(null),[]);
});

test('hourly and rolling rain keep their reported period',()=>{
  const current={rainLastHourMm:1,rainLast24hMm:8,rainLast3hMm:2,rainLast6hMm:4};
  assert.deepEqual(reportedRain(current),[
    {period:'past hour',mm:1},{period:'past 24h',mm:8},
    {period:'past 3h',mm:2},{period:'past 6h',mm:4},
  ]);
  assert.equal(current.rainDayMm,undefined);
  assert.equal(current.rainYearMm,undefined);
});

test('the station midnight amount is labelled separately from rolling periods',()=>{
  assert.deepEqual(reportedRain({rainDayMm:3}),[{period:'since station midnight',mm:3}]);
  assert.deepEqual(reportedRain({rainYearMm:50}),[]);
});
