/* Pure display rules: sky palettes and a Berkeley-inspired forecast beacon. */
(function (root) {
  "use strict";
  const themes = {
    dawn: {
      label: "Daybreak",
      top: "#59352a",
      bottom: "#182a3d",
      panel: "#28303b",
      cardTop: "#9a512b",
      cardBottom: "#3c3449",
      accent: "#ffda9b",
      glow: "#ffcc881e",
    },
    day: {
      label: "Daylight",
      top: "#225675",
      bottom: "#102b42",
      panel: "#15374c",
      cardTop: "#28769d",
      cardBottom: "#153f5a",
      accent: "#ffe0a2",
      glow: "#fff0b51c",
    },
    soft: {
      label: "Soft daylight",
      top: "#344e65",
      bottom: "#162a40",
      panel: "#20374b",
      cardTop: "#4a6c80",
      cardBottom: "#254355",
      accent: "#b5dce9",
      glow: "#d3e9f018",
    },
    dusk: {
      label: "Sunset",
      top: "#64384b",
      bottom: "#19243b",
      panel: "#2c3045",
      cardTop: "#9b504a",
      cardBottom: "#3d3153",
      accent: "#ffd3ac",
      glow: "#ffbc8a20",
    },
    night: {
      label: "Nightfall",
      top: "#14283c",
      bottom: "#091522",
      panel: "#142535",
      cardTop: "#263f5d",
      cardBottom: "#102838",
      accent: "#8bdfd0",
      glow: "#b8d3ff16",
    },
  };
  function chooseTheme(mode, altitude, rising, solar, fresh) {
    if (mode && mode !== "auto" && themes[mode]) return mode;
    if (!Number.isFinite(altitude) || altitude < -6) return "night";
    if (altitude <= 6) return rising ? "dawn" : "dusk";
    return fresh && Number.isFinite(solar) && solar < 120 ? "soft" : "day";
  }
  function beaconCategory(period) {
    const summary = period.summary;
    const wet =
      period.pop === null || period.pop === undefined
        ? !/slight chance|chance|possible/i.test(summary)
        : Number.isFinite(period.pop) && period.pop >= 30;
    if (/snow|sleet|freezing rain|ice pellets|wintry/i.test(summary) && wet)
      return "snow";
    if (/rain|shower|thunder|drizzle/i.test(summary) && wet) return "rain";
    // Partly sunny belongs with partly cloudy; mostly sunny belongs with mostly clear.
    if (
      /cloud|overcast|fog|haze|smoke|rain|shower|thunder|drizzle|snow|sleet|wintry|partly sunny|ice pellets|freezing/i.test(
        summary,
      )
    )
      return "cloud";
    if (/\b(sunny|clear|fair)\b/i.test(summary)) return "clear";
    // NWS may replace the sky wording with "Areas Of Frost", "Cold", or wind.
    // Use its verified sky icon only when the summary supplied no category.
    if (period.sky === "clear" || period.sky === "cloud") return period.sky;
    return null;
  }
  function beacon(forecast, now) {
    const unavailable = (detail) => ({
      kind: "unknown",
      color: "gray",
      flash: false,
      title: "Forecast unavailable",
      signal: "Beacon unlit",
      detail: detail || "Waiting for a usable forecast.",
      demo: !!forecast?.demo,
    });
    if (!forecast) return unavailable("Loading the local forecast…");
    if (forecast.error || forecast.stale)
      return unavailable(forecast.error || "The forecast is too old to use.");
    if (
      !Number.isFinite(now) ||
      !Number.isFinite(forecast.issuedAt) ||
      now - forecast.issuedAt > 43200 ||
      forecast.issuedAt > now + 600
    )
      return unavailable("The forecast issue time could not be verified.");
    const end = now + 43200;
    const rows = (Array.isArray(forecast.periods) ? forecast.periods : [])
      .filter(
        (p) =>
          p &&
          Number.isFinite(p.start) &&
          Number.isFinite(p.end) &&
          p.end > p.start &&
          p.end > now &&
          p.start < end &&
          typeof p.summary === "string" &&
          p.summary.trim(),
      )
      .sort((a, b) => a.start - b.start || a.end - b.end);
    const totals = { clear: 0, cloud: 0, rain: 0, snow: 0 };
    let cursor = now,
      frost = false;
    for (const period of rows) {
      const start = Math.max(now, period.start),
        stop = Math.min(end, period.end);
      if (stop <= cursor) continue; // Overlapping or duplicate periods never get extra votes.
      if (start > cursor + 1)
        return unavailable(
          "Not enough hourly forecast to cover the next 12 hours.",
        );
      const kind = beaconCategory(period);
      if (!kind)
        return unavailable(
          `Unrecognized forecast condition: “${period.summary}”.`,
        );
      if (/\bfrost\b/i.test(period.summary)) frost = true;
      totals[kind] += stop - Math.max(start, cursor);
      cursor = stop;
    }
    if (cursor < end - 1)
      return unavailable(
        "Not enough hourly forecast to cover the next 12 hours.",
      );
    // The greatest duration wins; only exact ties favor wintry, rain, cloud, then clear.
    const priority = ["snow", "rain", "cloud", "clear"];
    const kind = priority.reduce(
      (best, key) => (totals[key] > totals[best] ? key : best),
      priority[0],
    );
    const labels = {
      clear: "Clear",
      cloud: "Clouds",
      rain: "Rain",
      snow: "Snow/ice",
    };
    const hours = (value) => {
      const h = value / 3600;
      return h < 0.1
        ? "<0.1"
        : Math.abs(h - Math.round(h)) < 0.05
          ? String(Math.round(h))
          : h.toFixed(1);
    };
    const detail =
      Object.entries(totals)
        .filter(([, seconds]) => seconds >= 1)
        .sort(
          (a, b) =>
            b[1] - a[1] || priority.indexOf(a[0]) - priority.indexOf(b[0]),
        )
        .map(([key, seconds]) => `${labels[key]} ${hours(seconds)}h`)
        .join(" · ") + (frost ? " · Frost in outlook" : "");
    const signals = {
      clear: ["blue", false, "Clear / mostly clear", "Steady blue"],
      cloud: ["blue", true, "Clouds prevail", "Flashing blue"],
      rain: ["red", false, "Rain leads the outlook", "Steady red"],
      snow: ["red", true, "Wintry weather leads", "Flashing red"],
    };
    const [color, flash, title, signal] = signals[kind];
    return {
      kind,
      color,
      flash,
      title,
      signal,
      detail,
      demo: !!forecast.demo,
      start: now,
      end,
      issuedAt: forecast.issuedAt,
      hours: Object.fromEntries(
        Object.entries(totals).map(([key, value]) => [key, value / 3600]),
      ),
    };
  }

  const api = { themes, chooseTheme, beacon };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.WeatherDesign = api;
})(typeof window !== "undefined" ? window : this);
