const assert = require("node:assert/strict");
const test = require("node:test");
const alerts = require("../static/alerts.js");
const { beacon } = require("../static/design.js");

test("Irish warning notices and official links retain the issuing agency", () => {
  const now = 1792956600;
  const quiet = alerts.view(
    { source: "Met Éireann", checkedAt: now, alerts: [] },
    now,
  );
  assert.equal(quiet.title, "No active Met Éireann warnings");
  assert.equal(
    alerts.officialUrl({ countryCode: "IE" }),
    "https://www.met.ie/warnings",
  );
  const failed = alerts.view(
    { source: "Met Éireann", alerts: [], stale: true },
    now,
  );
  assert.equal(failed.status, "unavailable");
  assert.equal(failed.title, "Met Éireann warnings unavailable");
});

test("Met Éireann sky descriptions light all four beacon signals", () => {
  const now = 1792956600;
  for (const [summary, kind, flash] of [
    ["Fair", "clear", false],
    ["Sun / Clear sky", "clear", false],
    ["Partly Cloudy", "cloud", true],
    ["Rain showers", "rain", false],
    ["Sleet showers", "snow", true],
  ]) {
    const result = beacon(
      {
        issuedAt: now,
        source: "Met Éireann",
        periods: [{ start: now - 1800, end: now + 43200, summary, pop: null }],
      },
      now,
    );
    assert.equal(result.kind, kind);
    assert.equal(result.flash, flash);
  }
});
