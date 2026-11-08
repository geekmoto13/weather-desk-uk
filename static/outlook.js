/* Four twelve-hour summaries of the existing hourly forecast feed. */
(function (root) {
  "use strict";
  const finite = Number.isFinite;
  function windows(forecast, now = Date.now() / 1000) {
    const usable = !!forecast && !forecast.error && !forecast.stale &&
      finite(forecast.issuedAt) && now - forecast.issuedAt <= 43200 && forecast.issuedAt <= now + 600;
    const rows = usable && Array.isArray(forecast.periods) ? forecast.periods.filter(p =>
      p && finite(p.start) && finite(p.end) && p.end > p.start &&
      typeof p.summary === "string" && p.summary.trim()
    ).slice().sort((a, b) => a.start - b.start || a.end - b.end) : [];
    return Array.from({length: 4}, (_, i) => {
      const start = now + i * 43200, end = start + 43200;
      let cursor = start, coverage = 0, gap = false;
      const samples = [], summaries = new Map();
      for (const row of rows) {
        const left = Math.max(start, row.start), right = Math.min(end, row.end);
        if (right <= cursor || left >= end) continue;
        if (left > cursor + 1) gap = true;
        const duration = right - Math.max(left, cursor);
        if (duration <= 0) continue;
        coverage += duration;
        summaries.set(row.summary, (summaries.get(row.summary) || 0) + duration);
        samples.push(row);
        cursor = right;
      }
      const complete = usable && !gap && coverage >= 43200 - 1;
      const values = (key, low, high) => samples.map(p => p[key]).filter(v => finite(v) && v >= low && v <= high);
      const temps = values("tempF", -150, 180), winds = values("windMph", 0, 300), pops = values("pop", 0, 100);
      return {start, end, complete, coverage, status: !usable ? "unavailable" : complete ? "complete" : "partial",
        summary: complete ? [...summaries].sort((a,b) => b[1]-a[1])[0]?.[0] : "Forecast incomplete",
        tempMinF: complete && temps.length === samples.length ? Math.min(...temps) : null,
        tempMaxF: complete && temps.length === samples.length ? Math.max(...temps) : null,
        windMaxMph: complete && winds.length === samples.length ? Math.max(...winds) : null,
        popPeak: complete && pops.length === samples.length ? Math.max(...pops) : null};
    });
  }
  function create({getSettings, document: doc = root.document, clock = () => Date.now()} = {}) {
    const find = id => doc.getElementById(id), grid = find("outlookGrid");
    if (!grid) return null;
    function render(forecast) {
      const now = clock() / 1000, rows = windows(forecast, now);
      const zone = getSettings()?.timezone || "UTC";
      const stamp = (time, options) => new Intl.DateTimeFormat("en-US", {timeZone: zone, ...options}).format(new Date(time * 1000));
      const unavailable = rows.every(p => p.status === "unavailable");
      find("outlookStatus").textContent = unavailable ? "Forecast unavailable — waiting for a fresh hourly feed." :
        `${forecast.demo ? "SAMPLE FORECAST · " : ""}Local time · four 12-hour windows${rows.some(p => !p.complete) ? " · incomplete windows marked" : ""}`;
      grid.hidden = unavailable;
      grid.replaceChildren();
      for (const row of rows) {
        const card = doc.createElement("article"); card.className = "outlook-period"; card.dataset.status = row.status;
        const add = (tag, className, text) => {const el = doc.createElement(tag); el.className = className; el.textContent = text; card.append(el); return el;};
        const label = t => stamp(t, {weekday:"short", hour:"numeric", minute:"2-digit"});
        add("p", "outlook-time", `${label(row.start)} – ${label(row.end)}`);
        add("strong", "outlook-condition", row.summary || "Forecast unavailable");
        const c = f => Math.round((f - 32) * 5 / 9);
        add("p", "outlook-temperature", finite(row.tempMinF) ?
          `${Math.round(row.tempMinF)}–${Math.round(row.tempMaxF)}°F · ${c(row.tempMinF)}–${c(row.tempMaxF)}°C` : "Temperature —");
        const rain = finite(row.popPeak) ? `Precip peak ${Math.round(row.popPeak)}%` : "Precip chance —";
        const wind = finite(row.windMaxMph) ? `Wind ≤${Math.round(row.windMaxMph)} mph` : "Wind —";
        const detail = add("p", "outlook-rain-wind", `${rain} · ${wind}`);
        detail.title = `Precip peak is the highest hourly chance of rain, snow or other precipitation, not the probability for the entire 12 hours. Wind is the highest hourly forecast speed, not a gust.${finite(row.windMaxMph) ? ` ${Math.round(row.windMaxMph * 1.609344)} km/h.` : ""}`;
        card.title = `${label(row.start)} to ${label(row.end)} · ${row.complete ? row.summary : `${Math.round(row.coverage/3600)} of 12 hours available; incomplete values are withheld.`}`;
        grid.append(card);
      }
      const name = forecast?.demo ? "Sample guidance" : forecast?.source || "Hourly forecast";
      const checked = forecast?.timeBasis === "retrieved" ? "checked" : "issued";
      const time = finite(forecast?.issuedAt) ? ` · ${checked} ${stamp(forecast.issuedAt, {hour:"numeric", minute:"2-digit", timeZoneName:"short"})}` : "";
      find("outlookSource").textContent = `${name}${time} · hourly guidance.`;
    }
    return {render};
  }
  const api = {windows, create};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherOutlook = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
