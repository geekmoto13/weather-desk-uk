const test = require("node:test"),
  assert = require("node:assert/strict");
const lightning = require("../static/lightning.js");
const tick = () => new Promise((resolve) => setImmediate(resolve));
function environment(t) {
  const previous = {
    document: global.document,
    Worker: global.Worker,
    setInterval: global.setInterval,
    setTimeout: global.setTimeout,
  };
  const nodes = new Map(),
    workers = [];
  function node(id) {
    if (!nodes.has(id)) {
      const n = {
        dataset: {},
        textContent: "",
        handlers: {},
        open: false,
        value: "",
        checked: true,
        writes: 0,
        addEventListener(k, f) {
          this.handlers[k] = f;
        },
        showModal() {
          this.open = true;
        },
        focus() {
          this.focused = true;
        },
      };
      Object.defineProperty(n, "src", {
        get() {
          return this.source;
        },
        set(v) {
          this.source = v;
          this.writes++;
        },
      });
      nodes.set(id, n);
    }
    return nodes.get(id);
  }
  global.document = {
    hidden: false,
    getElementById: node,
    addEventListener() {},
  };
  global.setInterval = () => 1;
  global.setTimeout = () => 1;
  global.Worker = class {
    constructor() {
      this.messages = [];
      workers.push(this);
    }
    postMessage(message) {
      this.messages.push(message);
    }
    terminate() {
      this.terminated = true;
    }
  };
  t.after(() => Object.assign(global, previous));
  return { node, workers };
}
const point = {
  latitude: 42.4668,
  longitude: -70.9495,
  timezone: "America/New_York",
};
function catalogue(p) {
  const now = Date.now() / 1000;
  return {
    ...p,
    configured: true,
    provider: "noaa",
    source: "NOAA GOES-19 GLM",
    status: "ready",
    windowEnd: now - 30,
    windowStart: now - 2730,
    staleAfterSeconds: 300,
    coverageComplete: true,
    files: [
      { id: "a".repeat(32), provider: "noaa", start: now - 50, end: now - 30 },
    ],
  };
}
test("polls keep the streaming map alive rather than reloading it", async (t) => {
  const { node } = environment(t);
  const panel = lightning.create({
    api: async () => ({ ...catalogue(point), files: [] }),
    getSettings: () => point,
    onSettings() {},
  });
  panel.sync();
  panel.start();
  await tick();
  await panel.refresh();
  await panel.refresh();
  assert.equal(node("lightningFrame").writes, 1);
  assert.equal(node("eumetsatConnection").hidden, true);
});
test("old worker output cannot populate another saved location", async (t) => {
  const { node, workers } = environment(t);
  let settings = point;
  const panel = lightning.create({
    api: async () => catalogue(settings),
    getSettings: () => settings,
    onSettings() {},
  });
  panel.start();
  await tick();
  const old = workers[0],
    oldJob = old.messages[0];
  settings = { latitude: 52.14, longitude: -10.27, timezone: "Europe/Dublin" };
  panel.sync();
  await tick();
  assert.equal(old.terminated, true);
  const flash = {
    latitude: 42.47,
    longitude: -70.95,
    timestamp: Date.now() / 1000 - 60,
    distanceKm: 10,
    direction: "N",
  };
  old.onmessage({
    data: {
      epoch: oldJob.epoch,
      catalogueId: oldJob.catalogueId,
      complete: true,
      closest: flash,
    },
  });
  assert.doesNotMatch(node("lightningDistance").textContent, /10\.0 km/);
  const current = workers[1],
    job = current.messages[0];
  current.onmessage({
    data: {
      epoch: job.epoch,
      catalogueId: job.catalogueId,
      complete: true,
      decoded: 1,
      total: 1,
      flashCount: 1,
      closest: {
        ...flash,
        latitude: 52.15,
        longitude: -10.27,
        distanceKm: 1.11,
      },
    },
  });
  assert.match(node("lightningDistance").textContent, /1\.1 km/);
  assert.match(node("lightningFrame").src, /#7\/52.1400\/-10.2700/);
  assert.match(node("lightningAt").textContent, /Occurred/);
});
test("free EUMETSAT credentials clear after connect and stay out of the map URL", async (t) => {
  const { node } = environment(t),
    calls = [];
  let configured = false;
  const api = async (path, body) => {
    if (body) {
      calls.push(body);
      configured = true;
      return { configured: true };
    }
    return {
      latitude: 52.14,
      longitude: -10.27,
      configured,
      requiresAccount: true,
      files: [],
      status: configured ? "loading" : "unconfigured",
    };
  };
  const panel = lightning.create({
    api,
    getSettings: () => ({ latitude: 52.14, longitude: -10.27 }),
    onSettings() {},
    region: "UK",
  });
  panel.start();
  await tick();
  node("eumetsatKey").value = "synthetic-consumer-key";
  node("eumetsatSecret").value = "synthetic-consumer-secret";
  await node("lightningConnectBtn").handlers.click();
  assert.equal(calls[0].consumerSecret, "synthetic-consumer-secret");
  assert.equal(node("eumetsatKey").value, "");
  assert.equal(node("eumetsatSecret").value, "");
  assert.doesNotMatch(node("lightningFrame").src, /synthetic/);
  assert.equal(node("eumetsatConnection").hidden, false);
});
