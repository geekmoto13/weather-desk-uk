const { test } = require("node:test");
const assert = require("node:assert/strict");
const { create } = require("../static/public-stations.js");

function setup(api, onChange = async () => {}) {
  const nodes = new Map();
  global.document = { getElementById(id) {
    if (!nodes.has(id)) nodes.set(id, {
      value: "", checked: true, disabled: false, textContent: "", events: {},
      addEventListener(name, fn) { this.events[name] = fn; },
    });
    return nodes.get(id);
  }};
  const panel = create({ api, onChange });
  return { panel, get: (id) => document.getElementById(id) };
}

test("saving a separate WU key clears the password and refreshes readings", async () => {
  const requests = [];
  let refreshed = false;
  const { panel, get } = setup(async (url, body) => {
    requests.push([url, body]); return { configured: true, saved: false };
  }, async () => { refreshed = true; });
  panel.sync({ configured: false });
  get("wundergroundKey").value = "fixture-key-not-real";
  get("rememberWunderground").checked = false;
  await get("wundergroundSaveBtn").events.click();
  assert.equal(requests[0][0], "/api/public-stations/settings");
  assert.equal(requests[0][1].remember, false);
  assert.equal(get("wundergroundKey").value, "");
  assert.ok(refreshed);
  assert.match(get("wundergroundStatus").textContent, /this session/);
  assert.doesNotMatch(get("wundergroundStatus").textContent, /fixture-key/);
});

test("forget clears credentials and updates the fallback explanation", async () => {
  const requests = [];
  const { panel, get } = setup(async (url, body) => {
    requests.push(body); return { configured: false, saved: false };
  });
  panel.sync({ configured: true, saved: true });
  get("wundergroundKey").value = "unsaved-input";
  await get("wundergroundForgetBtn").events.click();
  assert.equal(requests[0].action, "forget");
  assert.equal(requests[0].apiKey, "");
  assert.equal(get("wundergroundKey").value, "");
  assert.ok(get("wundergroundForgetBtn").disabled);
  assert.match(get("wundergroundStatus").textContent, /CWOP and official/);
});

test("a failed save shows the error and keeps controls usable", async () => {
  const { panel, get } = setup(async () => { throw Error("Key could not be saved"); });
  panel.sync({ configured: true, saved: true });
  await get("wundergroundSaveBtn").events.click();
  assert.equal(get("wundergroundStatus").textContent, "Key could not be saved");
  assert.equal(get("wundergroundSaveBtn").disabled, false);
  assert.equal(get("wundergroundForgetBtn").disabled, false);
});
