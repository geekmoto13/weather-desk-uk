const test=require('node:test'), assert=require('node:assert/strict');
const radio=require('../static/radio.js'), plots=require('../static/radio-plots.js');
const NOW=Date.parse('2026-11-08T00:30:00Z')/1000;

test('NOAA levels retain six distinct colours and missing is never quiet',()=>{
  assert.deepEqual([0,1,2,3,4,5].map(radio.severityLevel),['0','1','2','3','4','5']);
  for(const n of [null,undefined,NaN,2.5,-1,6,true])assert.equal(radio.severityLevel(n),'unknown');
  assert.deepEqual([4.67,5,6,7,8,8.67,9].map(radio.kpLevel),['0','1','2','3','4','4','5']);
  assert.deepEqual([1e-6,1e-5,5e-5,1e-4,1e-3,2e-3].map(radio.flareLevel),['0','1','2','3','4','5']);
});
test('custom-grid validation applies only to the custom destination',()=>{
  assert.deepEqual(radio.customGridState('japan'),{required:false,disabled:true,hidden:true});
  assert.deepEqual(radio.customGridState('dxcc:SP'),{required:false,disabled:true,hidden:true});
  assert.deepEqual(radio.customGridState('custom'),{required:true,disabled:false,hidden:false});
});
test('Kp chart uses observations, excludes future forecasts, and preserves zero',()=>{
  const p=plots.model('kp',{points:[{timestamp:NOW-10800,kp:0,kind:'observed'},{timestamp:NOW-100,kp:4.67,kind:'estimated'},{timestamp:NOW+3600,kp:8,kind:'predicted'}]},NOW);
  assert.deepEqual(p.series[0].points.map(p=>p.value),[0,4.67]);assert.equal(p.max,9);
});
test('log plots sort real observations, reject zero on logarithmic axes and retain no-data gaps',()=>{
  const value={series:{long:[{timestamp:NOW-60,value:1e-6},{timestamp:NOW-3600,value:1e-8},{timestamp:NOW-300,value:0}],short:[]}};
  const p=plots.model('xray',value,NOW);
  assert.equal(p.series[0].points.length,2);assert.equal(p.series[1].points.length,0);
  assert.deepEqual(p.series[0].points.map(p=>p.timestamp),[NOW-3600,NOW-60]);
});

class Element {
  constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.events={};this.attrs={};this.style={setProperty(){}};this.textContent='';this.value='';const classes=new Set();this.classList={toggle(n,on){if(on)classes.add(n);else classes.delete(n);},contains(n){return classes.has(n);}};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=[...items];}
  setAttribute(n,v){this.attrs[n]=String(v);}
  addEventListener(n,fn){this.events[n]=fn;}
  closest(){if(!this.box)this.box=new Element();return this.box;}
  get options(){return this.children.flatMap(c=>c.tag==='option'?[c]:c.options||[]);}
  showModal(){this.open=true;} close(){this.open=false;} focus(){} select(){}
  getBoundingClientRect(){return {left:0,width:520};}
}
function harness(t){
  const oldDoc=global.document,oldNow=Date.now,oldPlots=global.WeatherRadioPlots,oldForm=global.FormData;
  const elements=new Map(), get=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id);};
  const prefs={target:'japan',customGrid:'',mode:'SSB',power:100,txGain:0,rxGain:0,noise:'RESIDENTIAL',reportCallsign:'',engine:'kc2g'};
  const form=get('radioSettingsForm');form.elements={};
  for(const k of Object.keys(prefs))form.elements[k]=new Element(k==='target'||k==='engine'?'select':'input');
  elements.set('radioDestinationSelect',form.elements.target);
  for(const [key,label]of [['japan','Japan'],['custom','Custom grid']]){const o=new Element('option');o.value=key;o.textContent=label;form.elements.target.append(o);}
  global.document={hidden:false,getElementById:get,createElement:tag=>new Element(tag),createElementNS:(_,tag)=>new Element(tag),addEventListener(){}};
  Date.now=()=>NOW*1000;global.WeatherRadioPlots=plots;
  global.FormData=class{constructor(f){this.values=Object.entries(f.elements).filter(([,e])=>!e.disabled).map(([k,e])=>[k,e.value]);} [Symbol.iterator](){return this.values[Symbol.iterator]();}};
  function payload(){
    const context={txLat:0,txLon:0,rxLat:35,rxLon:135,destination:'Japan',preferences:{...prefs}};
    const feed=value=>({value:{timestamp:NOW-60,...value},stale:false,loading:false});
    return {settings:{...prefs},context,loading:false,sources:{
      scales:feed({levels:{R:0,S:0,G:0},maximum24h:{R:1,S:0,G:2},outlook:[{date:'2026-11-08',g:1,minorBlackout:60,majorBlackout:20,radiation:15}]}),
      kp:feed({current:{kp:4.67,kind:'estimated'},points:[{timestamp:NOW-10800,kp:4.67,kind:'observed'},{timestamp:NOW+3600,kp:5,kind:'predicted'}]}),
      xray:feed({flux:1.6e-6,series:{long:[{timestamp:NOW-60,value:1.6e-6}],short:[]}}),
    },reports:{configured:true,callsign:'N1DQ',stale:false,value:{timestamp:NOW-60,reports:[{receiver:'JA1ABC',frequency:14074000,band:'20m',mode:'FT8',timestamp:NOW-60}],limited:false}},
    path:{stale:false,value:{timestamp:NOW-600,model:prefs.engine,modelMonth:'2026-11',sunspotNumber:93,sourceProvider:'www.hamclock.com',context,
      bands:radio.BANDS.map((band,i)=>({band,mhz:[28.85,24.94,21.225,18.118,14.175,10.12,7.15,5.33,3.65,1.9][i],hours:Array.from({length:24},()=>({bcr:prefs.engine==='voacap'&&band==='160m'?null:75,snr:prefs.engine==='voacap'?null:10}))}))}}};
  }
  const writes=[];
  let intercept=null;
  async function api(path,values){
    if(path==='/api/radio/settings'){writes.push(values);Object.assign(prefs,values);return {settings:{...prefs}};}
    if(path==='/api/radio/destinations')return {entities:[{id:'dxcc:SP',name:'Poland',prefix:'SP'}],cities:[{id:'city:tokyo',name:'Tokyo',entityName:'Japan',prefix:'JA'}]};
    if(intercept)return intercept();
    return payload();
  }
  const controller=radio.create({api,getSettings:()=>({latitude:0,longitude:0,timezone:'UTC',location:'Public sample',callsign:'N1DQ'}),isVisible:()=>true});
  controller.start();
  t.after(()=>{controller.close();global.document=oldDoc;Date.now=oldNow;global.WeatherRadioPlots=oldPlots;global.FormData=oldForm;});
  return {get,form,prefs,controller,payload,writes,setIntercept(fn){intercept=fn;},tick:()=>new Promise(r=>setImmediate(r))};
}
test('rendered screen separates current/max scales, plots NOAA data and shows actual reception',async t=>{
  const h=harness(t);h.controller.open();await h.tick();
  assert.equal(h.get('radioScaleR').dataset.level,'0');assert.equal(h.get('radioMaxR').dataset.level,'1');
  assert.equal(h.get('radioMaxG').dataset.level,'2');assert.equal(h.get('radioForecastG').dataset.level,'1');
  assert.match(h.get('radioBlackoutOutlook').textContent,/60%.*20%\nS1 or greater 15%/);
  assert.equal(h.get('radioBandRows').children.length,10);assert.equal(h.get('radioBandRows').children[0].children[1].textContent,'75%');
  assert.equal(h.get('radioTrendPlot').children[0].tag,'svg');
  assert.match(h.get('radioReportsSummary').textContent,/N1DQ.*1 reporting receivers/);
  assert.equal(h.get('radioReportsRows').children[0].children[0].textContent,'JA1ABC');
});
test('Japan saves with its grid input disabled; switching to a custom grid enables validation',async t=>{
  const h=harness(t);h.controller.open();await h.tick();await h.get('radioSetupBtn').events.click();
  assert.equal(h.form.elements.customGrid.required,false);assert.equal(h.form.elements.customGrid.disabled,true);
  assert.equal(h.get('radioCustomGridField').hidden,true);
  await h.form.events.submit({preventDefault(){}});await h.tick();
  assert.equal(h.writes[0].target,'japan');assert.equal('customGrid' in h.writes[0],false);
  h.form.elements.target.value='custom';h.form.elements.target.events.change();
  assert.equal(h.form.elements.customGrid.required,true);assert.equal(h.form.elements.customGrid.disabled,false);
});
test('VOACAP model switch shows monthly context and marks unsupported SNR/160m unavailable',async t=>{
  const h=harness(t);h.controller.open();await h.tick();await h.get('radioEnginevoacap').events.click();await h.tick();
  assert.equal(h.get('radioEnginevoacap').classList.contains('active'),true);
  assert.match(h.get('radioModelLabel').textContent,/VOACAP/);assert.match(h.get('radioPathStamp').textContent,/Monthly planning/);
  assert.match(h.get('radioQuickNote').textContent,/160m and SNR/);
  const cells=h.get('radioBandRows').children[9].children;
  assert.equal(cells[1].textContent,'—');assert.equal(cells[1].dataset.rating,'unknown');
  assert.equal(h.get('radioSnrHeading').textContent,'SNR · N/A');
  await h.get('radioSetupBtn').events.click();
  assert.equal(h.form.elements.power.min,'1');assert.equal(h.form.elements.power.step,'1');
  assert.equal(h.form.elements.txGain.disabled,true);
});
test('an older late KC2G response cannot replace the selected VOACAP model',async t=>{
  const h=harness(t);let resolveOld;const old=h.payload();let first=true;
  h.setIntercept(()=>{if(first){first=false;return new Promise(r=>resolveOld=r);}return h.payload();});
  h.controller.open();await h.tick();await h.get('radioEnginevoacap').events.click();await h.tick();
  resolveOld(old);await h.tick();
  assert.match(h.get('radioModelLabel').textContent,/VOACAP/);assert.equal(h.get('radioEnginevoacap').classList.contains('active'),true);
});

test('a failed reception query cannot appear as zero reporting receivers',async t=>{
  const h=harness(t);h.setIntercept(()=>({...h.payload(),reports:{configured:true,callsign:'N1DQ',value:null,stale:true,loading:false,error:'PSK Reporter unavailable'}}));
  h.controller.open();await h.tick();
  assert.match(h.get('radioReportsSummary').textContent,/reports unavailable/);
  assert.doesNotMatch(h.get('radioReportsSummary').textContent,/0 reporting/);
});
