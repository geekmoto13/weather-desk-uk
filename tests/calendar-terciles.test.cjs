const test = require('node:test');
const assert = require('node:assert/strict');
const {create, comparisonLabel} = require('../static/calendar.js');

class Element {
  constructor() {
    this.children = []; this.dataset = {}; this.events = {}; this.attributes = {};
    const set = new Set();
    this.classList = {toggle(name,on) { if(on) set.add(name); else set.delete(name); }, contains(name) {return set.has(name);}};
  }
  append(...children) {this.children.push(...children);}
  replaceChildren(...children) {this.children = children;}
  setAttribute(key,value) {this.attributes[key] = value;}
  addEventListener(event,handler) {this.events[event] = handler;}
  showModal() {this.open = true;}
}

test('labels describe historical thirds and keep unavailable or pending dates explicit', () => {
  assert.match(comparisonLabel({status:'above'}), /Warmer.*middle third/);
  assert.match(comparisonLabel({status:'below'}), /Cooler.*middle third/);
  assert.match(comparisonLabel({status:'at'}), /Within.*middle third/);
  assert.equal(comparisonLabel({status:'missing',note:'Insufficient reference years'}), 'Insufficient reference years');
  assert.equal(comparisonLabel({status:'pending',note:'Day in progress'}), 'Day in progress');
  assert.doesNotMatch(comparisonLabel({status:'missing'}), /Within|normal/);
});

test('a rendered green day exposes both cutoffs, sample count and positive departure', async (t) => {
  const oldDocument = global.document, oldNow = Date.now;
  const elements = new Map();
  global.document = {getElementById(id) {if(!elements.has(id)) elements.set(id,new Element()); return elements.get(id);}, createElement() {return new Element();}};
  Date.now = () => Date.parse('2026-01-05T17:00:00Z');
  const settings = {timezone:'America/New_York',latitude:42,longitude:-71,climateStation:{id:'TEST',name:'Test reference'}};
  const payload = {month:'2026-01',stationId:'TEST',stationName:'Test reference',source:'ambient',today:'2026-01-05',days:[
    {day:1,date:'2026-01-01',status:'at',meanF:60,normalF:55,departureF:5,highF:70,lowF:50,lowerThirdF:56,upperThirdF:65,baselineYears:30,baselineExpectedYears:30,baselineRequiredYears:24},
    {day:2,date:'2026-01-02',status:'missing',meanF:60,normalF:55,departureF:5,note:'Historical thirds unavailable: 20 of 30 reference years',baselineYears:20,baselineExpectedYears:30,baselineRequiredYears:24},
    {day:5,date:'2026-01-05',status:'pending',note:'Day in progress'},
    {day:6,date:'2026-01-06',status:'future',note:'Future day'},
  ],completed:4,total:4,loading:false};
  const calendar = create({api:async()=>payload,getSettings:()=>settings,getSourceKey:()=> 'ambient',isVisible:()=>true});
  t.after(()=>{calendar.pause(); Date.now=oldNow; if(oldDocument===undefined)delete global.document;else global.document=oldDocument;});
  calendar.sync(); await new Promise(resolve=>setImmediate(resolve));
  const cells = elements.get('calendarDays').children.filter(e=>e.dataset.status);
  assert.equal(cells[0].dataset.status,'at'); assert.equal(cells[1].dataset.status,'missing');
  assert.equal(cells[4].dataset.status,'pending'); assert.equal(cells[5].disabled,true);
  assert.match(cells[0].attributes['aria-label'],/historical middle third.*departure 5.00/);
  assert.match(cells[0].title,/middle third/);
  cells[0].events.click();
  assert.equal(elements.get('calendarDayDialog').open,true);
  assert.match(elements.get('calendarDayBand').textContent,/56.00°F/);
  assert.match(elements.get('calendarDayBand').textContent,/65.00°F/);
  assert.match(elements.get('calendarDayCoverage').textContent,/30 of 30.*24 required/);
  assert.match(elements.get('calendarDayDeparture').textContent,/\+5.00°F/);
  assert.match(elements.get('calendarDaySource').textContent,/not official NOAA daily categories/);
  assert.doesNotMatch(elements.get('calendarDaySource').textContent,/within ±1/);
  cells[1].events.click();
  assert.match(elements.get('calendarDayStatus').textContent,/20 of 30/);
  assert.match(elements.get('calendarDayBand').textContent,/Unavailable/);
});
