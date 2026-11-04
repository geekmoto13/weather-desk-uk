/* Measured UK coastal water levels. Future tide predictions remain external. */
((root) => {
  "use strict";
  const finite = (v) => typeof v === "number" && Number.isFinite(v);
  function chart(rows, now) {
    const points = (rows || [])
      .filter(
        (p) =>
          finite(p.timestamp) &&
          finite(p.heightM) &&
          p.timestamp >= now - 86400 &&
          p.timestamp <= now + 300,
      )
      .sort((a, b) => a.timestamp - b.timestamp);
    if (!points.length) return "";
    const W = 400,
      H = 100,
      left = 34,
      right = 6,
      top = 8,
      bottom = 21;
    const heights = points.map((p) => p.heightM),
      low = Math.min(...heights),
      high = Math.max(...heights);
    const padding = Math.max(0.15, (high - low) * 0.12),
      min = low - padding,
      max = high + padding;
    const px = (t) => left + ((t - (now - 86400)) / 86400) * (W - left - right),
      py = (v) => top + ((max - v) / (max - min)) * (H - top - bottom);
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" aria-hidden="true">`;
    for (const v of [min, (min + max) / 2, max])
      svg += `<path d="M${left} ${py(v).toFixed(2)}H${W - right}" stroke="currentColor" opacity=".15"/><text x="${left - 5}" y="${(py(v) + 3).toFixed(2)}" text-anchor="end" fill="currentColor" font-size="9">${v.toFixed(1)}</text>`;
    let segments = [],
      segment = [],
      previous = null;
    for (const p of points) {
      if (previous && p.timestamp - previous.timestamp > 1800) {
        segments.push(segment);
        segment = [];
      }
      segment.push(p);
      previous = p;
    }
    if (segment.length) segments.push(segment);
    for (const part of segments) {
      if (part.length === 1) {
        svg += `<circle cx="${px(part[0].timestamp).toFixed(2)}" cy="${py(part[0].heightM).toFixed(2)}" r="2" fill="var(--mint)"/>`;
        continue;
      }
      const path = part
        .map(
          (p, i) =>
            `${i ? "L" : "M"}${px(p.timestamp).toFixed(2)} ${py(p.heightM).toFixed(2)}`,
        )
        .join(" ");
      svg += `<path d="${path}" fill="none" stroke="var(--mint)" stroke-width="2" stroke-linejoin="round"/>`;
    }
    svg += `<text x="${left}" y="${H - 3}" fill="currentColor" font-size="9">24 hours ago</text><text x="${W - right}" y="${H - 3}" text-anchor="end" fill="currentColor" font-size="9">Now</text></svg>`;
    return svg;
  }
  function create({ api, getSettings }) {
    const $ = (id) => document.getElementById(id),
      put = (id, text) => {
        $(id).textContent = text;
      };
    const local = (stamp) =>
      new Intl.DateTimeFormat("en-GB", {
        timeZone: getSettings()?.timezone || "Europe/London",
        day: "numeric",
        month: "short",
        hour: "2-digit",
        minute: "2-digit",
        timeZoneName: "short",
      }).format(stamp * 1000);
    let timer;
    const locationKey = () =>
      `${getSettings()?.latitude}|${getSettings()?.longitude}`;
    async function refresh() {
      clearTimeout(timer);
      const key = locationKey();
      try {
        const value = await api("/api/tides");
        if (key !== locationKey()) return;
        $("ukTideAttribution").textContent =
          value.note ||
          "This uses Environment Agency tide gauge data from the real-time data API (Beta).";
        $("ukTideDatumNote").textContent =
          value.source === "Marine Institute"
            ? "The curve shows measured sea levels over the past 24 hours in metres relative to Ordnance Datum Malin Head (OSGM15). Gaps over 30 minutes are blank; quality-unknown readings are provisional. These are not chart-datum heights or future tide predictions."
            : "The curve shows measured, 15-minute mean sea levels over the past 24 hours in metres relative to Ordnance Datum Newlyn. Gaps over 30 minutes are blank; these are not chart-datum heights or future tide predictions.";
        $("ukTideLicenseNote").textContent =
          value.source === "Marine Institute"
            ? "Marine Institute · CC BY 4.0 · displayed as a time series."
            : "Environment Agency · Open Government Licence v3.0.";
        $("tideOfficialLink").href =
          value.sourceUrl || "https://easytide.admiralty.co.uk/";
        $("tideOfficialLink").textContent =
          value.source === "Marine Institute"
            ? "Marine Institute tides ↗"
            : "Official tide service ↗";
        put(
          "ukTideStation",
          value.stationName
            ? `${value.stationName} · ${value.distanceKm} km from you`
            : value.loading
              ? "Finding the nearest reporting coast…"
              : "Coastal feed unavailable",
        );
        put(
          "ukTideDetailsStation",
          value.stationName
            ? `${value.stationName} · ${value.distanceKm} km from the selected location. Observation: ${local(value.latest.timestamp)}.`
            : "No recent coastal observations available.",
        );
        put(
          "ukTideHeight",
          finite(value.latest?.heightM)
            ? `${value.latest.heightM.toFixed(2)} m ${value.datumLabel || "AOD"}`
            : "—",
        );
        put(
          "ukTideTime",
          value.latest
            ? `${local(value.latest.timestamp)}${value.stale ? " · DELAYED" : ""}${value.latest.provisional ? " · PROVISIONAL" : ""}`
            : "Observed sea level",
        );
        $("ukTideChart").innerHTML = chart(
          value.observations,
          Date.now() / 1000,
        );
        $("ukTideChart").setAttribute(
          "aria-label",
          value.observations?.length
            ? `${value.stationName}: ${value.observations.length} observed sea-level readings over the past 24 hours in metres relative to ${value.datumName || "Ordnance Datum Newlyn"}. Gaps over 30 minutes are blank.`
            : "No observed tide curve available.",
        );
        put(
          "ukTideNote",
          value.error ||
            (value.loading && !value.latest
              ? "Checking official coastal gauges…"
              : "Past 24 hours · measured, not predicted"),
        );
        $("ukTideNote").classList.toggle("tide-error", !!value.error);
        if (value.loading) timer = setTimeout(refresh, 2500);
      } catch (error) {
        put(
          "ukTideNote",
          "Coastal readings could not refresh. Retrying on the next update.",
        );
      }
    }
    $("ukTideDetailsBtn").addEventListener("click", () =>
      $("ukTideDetails").showModal(),
    );
    $("climateArchiveBtn").addEventListener("click", () =>
      $("climateArchiveDialog").showModal(),
    );
    return { refresh };
  }
  root.WeatherUKTides = { create, chart };
  if (typeof module === "object" && module.exports) module.exports = { chart };
})(globalThis);
