const test = require("node:test"),
  assert = require("node:assert/strict");
const lightning = require("../static/lightning.js"),
  data = require("../static/lightning-data.js");
const now = Date.UTC(2026, 9, 25, 22, 30) / 1000;
const base = {
  configured: true,
  status: "ready",
  source: "NOAA GOES-19 GLM",
  windowEnd: now - 60,
  windowStart: now - 2760,
  staleAfterSeconds: 300,
  files: [{ id: "a" }],
  complete: true,
  decoded: 1,
  flashCount: 1,
};
const flash = {
  latitude: 42.47,
  longitude: -70.95,
  timestamp: now - 120,
  distanceKm: 16.09344,
  direction: "NE",
};
test("Blitzortung map stays centred with cookies and audio disabled", () => {
  const u = new URL(lightning.mapURL({ latitude: 52.14, longitude: -10.27 }));
  assert.equal(u.hash, "#7/52.1400/-10.2700");
  assert.equal(u.searchParams.get("Cookies"), "0");
  assert.equal(u.searchParams.get("AudioCheckboxChecked"), "0");
  assert.equal(lightning.mapURL({ latitude: NaN, longitude: 0 }), null);
});
test("free EUMETSAT account requirement is explicit", () => {
  const v = lightning.describe({ configured: false }, now);
  assert.match(v.detail, /free EUMETSAT/);
  assert.doesNotMatch(v.note, /Weatherbit/);
});
test("a complete fresh window displays both units and actual occurrence age", () => {
  const v = lightning.describe({ ...base, closest: flash }, now);
  assert.equal(v.value, "10.0 mi · 16.1 km");
  assert.equal(v.detail, "NE · 2 min ago");
});
test("partial closest is explicitly provisional", () => {
  const v = lightning.describe(
    { ...base, closest: flash, complete: false },
    now,
  );
  assert.match(v.detail, /PROVISIONAL/);
  assert.match(v.note, /Nearest loaded flash/);
});
test("missing files cannot be called no detections", () => {
  assert.equal(
    lightning.describe({ ...base, complete: false, closest: null }, now).value,
    "Loading flashes…",
  );
});
test("only complete fresh empty coverage says no detections", () => {
  assert.equal(
    lightning.describe({ ...base, closest: null }, now).value,
    "No detections",
  );
  assert.equal(
    lightning.describe({ ...base, closest: null, stale: true }, now).value,
    "Update delayed",
  );
});
test("data freshness uses observation time, not retrieval time", () => {
  const v = lightning.describe(
    { ...base, windowEnd: now - 301, checkedAt: now, closest: flash },
    now,
  );
  assert.match(v.detail, /OLD REPORT/);
});
test("decoder failure stays distinct from no lightning", () => {
  assert.equal(
    lightning.describe({ ...base, decoderError: "Failed" }, now).value,
    "Decoder unavailable",
  );
});
function fakeFile(columns) {
  return { keys: () => Object.keys(columns), get: (name) => columns[name] };
}
function col(values, attrs = {}) {
  return {
    value: values,
    attrs: Object.fromEntries(
      Object.entries(attrs).map(([k, v]) => [k, { value: v }]),
    ),
  };
}
function glm(extra = {}) {
  return fakeFile({
    flash_lat: col([42.47, 42.5]),
    flash_lon: col([-70.95, -70.9]),
    flash_time_offset_of_first_event: col([0, 100], {
      units: "seconds since 2026-10-25 22:29:00",
      scale_factor: [0.01],
      add_offset: [0],
    }),
    flash_quality_flag: col([0, 0]),
    ...extra,
  });
}
const meta = { provider: "noaa", start: now - 60, end: now - 40 },
  point = { latitude: 42.4668, longitude: -70.9495 };
test("CF scalar-array attributes and UTC time units decode without local DST", () => {
  const v = data.decode(glm(), meta, point);
  assert.equal(v.flashes[0].timestamp, now - 60);
  assert.equal(v.flashes[1].timestamp, now - 59);
});
test("quality-rejected and fill rows never become zero-distance strikes", () => {
  const v = data.decode(
    glm({
      flash_lat: col([-999, 42.5], { _FillValue: -999 }),
      flash_quality_flag: col([0, 0]),
    }),
    meta,
    point,
  );
  assert.equal(v.flashes.length, 1);
  assert.equal(v.rejected, 1);
});
test("mismatched arrays and absent variables fail explicitly", () => {
  assert.throws(() => data.decode(glm({ flash_lon: col([]) }), meta, point));
  assert.throws(() => data.decode(fakeFile({}), meta, point));
});
test("valid empty NetCDF arrays are an empty observation", () => {
  const f = glm({
    flash_lat: col([]),
    flash_lon: col([]),
    flash_time_offset_of_first_event: col([], {
      units: "seconds since 2000-01-01 00:00:00",
    }),
    flash_quality_flag: col([]),
  });
  assert.equal(data.decode(f, meta, point).total, 0);
});
test("EUMETSAT packed geolocation, seconds since 2000, and reversed confidence scale", () => {
  const f = fakeFile({
    latitude: col([19000, 19010], {
      scale_factor: [0.00275],
      add_offset: [0],
      _FillValue: [-32767],
    }),
    longitude: col([-3700, -3700], { scale_factor: [0.00275] }),
    flash_time: col([now - 946684800 - 60, now - 946684800 - 40], {
      units: "seconds since 2000-01-01 00:00:00.0",
    }),
    flash_filter_confidence: col([0, 250], { scale_factor: [0.004] }),
    l1b_missing_warning: col([1]),
  });
  const v = data.decode(
    f,
    { ...meta, provider: "eumetsat" },
    { latitude: 52.25, longitude: -10.175 },
  );
  assert.equal(v.flashes.length, 1);
  assert.equal(v.flashes[0].timestamp, now - 60);
  assert.equal(v.qualityWarning, true);
  assert.equal(v.rejected, 1);
});
test("distance and compass bearing use the saved location", () => {
  const v = data.distanceBearing(42, -71, 42.1, -71);
  assert.equal(v.direction, "N");
  assert.ok(Math.abs(v.distanceKm - 11.1195) < 0.001);
  assert.equal(data.distanceBearing(0, 0, 0, 0).direction, "At location");
});
test("nearest is separate from newest; repeated flash entries are deduplicated", () => {
  const near = { ...flash, distanceKm: 1, timestamp: now - 500 },
    far = { ...flash, distanceKm: 10, timestamp: now - 70 };
  const rows = new Map([
    ["a", { flashes: [far, near, near], qualityWarning: false }],
  ]);
  const v = data.summarize(rows, { ...base, coverageComplete: true });
  assert.equal(v.closest.distanceKm, 1);
  assert.equal(v.flashCount, 2);
  assert.equal(v.complete, true);
});
test("gaps, file input warnings, and undecoded files prevent completeness", () => {
  const row = { flashes: [], qualityWarning: true };
  assert.equal(
    data.summarize(new Map([["a", row]]), { ...base, coverageComplete: true })
      .complete,
    false,
  );
  assert.equal(
    data.summarize(new Map(), { ...base, coverageComplete: true }).complete,
    false,
  );
  assert.equal(
    data.summarize(new Map([["a", { ...row, qualityWarning: false }]]), {
      ...base,
      coverageComplete: false,
    }).complete,
    false,
  );
});
