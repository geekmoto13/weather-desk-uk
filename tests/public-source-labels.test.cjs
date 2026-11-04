const { test } = require("node:test");
const assert = require("node:assert/strict");
const { publicLabels, rainLedgerNote } = require("../static/readings.js");

test("Irish government observations are labelled by provider rather than airport", () => {
  const value = publicLabels("met-eireann");
  assert.equal(value.observation, "MET ÉIREANN OBSERVATION");
  assert.equal(value.badge, "PUBLIC MET ÉIREANN STATION");
  assert.equal(value.delayed, "MET ÉIREANN · DELAYED");
});
test("CWOP and actual airport observations retain distinct labels", () => {
  assert.equal(publicLabels("cwop").observation, "CWOP OBSERVATION");
  assert.equal(publicLabels("metar").observation, "AIRPORT OBSERVATION");
});
test("Weather Underground observations retain their own provider labels", () => {
  assert.equal(publicLabels("wunderground").observation, "WEATHER UNDERGROUND OBSERVATION");
  assert.equal(publicLabels("wunderground").badge, "PUBLIC WEATHER UNDERGROUND STATION");
  assert.equal(publicLabels("wunderground").delayed, "WEATHER UNDERGROUND · DELAYED");
});
test("Irish CWOP captions identify the separate Met Éireann rain gauge", () => {
  const value = rainLedgerNote(
    {
      source: "cwop",
      rain: { source: "Met Éireann", stationName: "Valentia Observatory" },
    },
    "IE",
  );
  assert.match(value, /Met Éireann Valentia Observatory/);
  assert.match(value, /separate from the CWOP station/);
  assert.match(value, /Monthly\/YTD totals need complete history/);
  assert.doesNotMatch(value, /Environment Agency/);
  assert.match(rainLedgerNote({ source: "cwop" }, "GB"), /Environment Agency/);
});
