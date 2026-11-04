/* Station reading rules used by the screen, copied report and PNG export. */
((root) => {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  // Traditional Beaufort 0–12, using the lower limits in metres/second.
  // Source: NOAA NODC weather code table, code 1861 (WMO wind force).
  const limits = [
    0.3, 1.6, 3.4, 5.5, 8, 10.8, 13.9, 17.2, 20.8, 24.5, 28.5, 32.7,
  ];
  const labels = [
    "Calm",
    "Light air",
    "Light breeze",
    "Gentle breeze",
    "Moderate breeze",
    "Fresh breeze",
    "Strong breeze",
    "Near gale",
    "Gale",
    "Strong gale",
    "Storm",
    "Violent storm",
    "Hurricane force",
  ];
  function beaufort(mph) {
    if (!finite(mph) || mph < 0) return null;
    const ms = mph * 0.44704;
    const force = limits.filter((limit) => ms + 1e-12 >= limit).length;
    return { force, label: labels[force] };
  }
  function localDay(stamp, timezone) {
    if (!finite(stamp)) return null;
    try {
      const p = Object.fromEntries(
        new Intl.DateTimeFormat("en-US", {
          timeZone: timezone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        })
          .formatToParts(new Date(stamp * 1000))
          .map((p) => [p.type, p.value]),
      );
      return `${p.year}-${p.month}-${p.day}`;
    } catch {
      return null;
    }
  }
  const localYear = (stamp, timezone) =>
    localDay(stamp, timezone)?.slice(0, 4) || null;
  function withYearRain(
    current,
    history = [],
    timezone = "America/New_York",
    annual = null,
  ) {
    if (!current) return null;
    const result = {
      ...current,
      rainYearSource: null,
      rainYearTimestamp: null,
    };
    const year = localYear(current.timestamp, timezone);
    // The provider's current annual counter is the primary source. A history
    // calculation must never replace or hide an available reported annual total.
    if (finite(current.rainYearMm) && current.rainYearMm >= 0) {
      return {
        ...result,
        rainYearSource: "live",
        rainYearTimestamp: current.timestamp,
      };
    }
    if (
      annual?.complete === true &&
      annual.through === localDay(current.timestamp, timezone) &&
      annual.through === localDay(annual.timestamp, timezone) &&
      annual.timezone === timezone &&
      String(annual.year) === year &&
      annual.start === year + "-01-01" &&
      finite(annual.mm) &&
      annual.mm >= 0 &&
      finite(annual.timestamp) &&
      annual.timestamp <= current.timestamp &&
      current.timestamp - annual.timestamp <= 900
    ) {
      return {
        ...result,
        rainYearMm: annual.mm,
        rainYearSource: "calculated",
        rainYearTimestamp: annual.timestamp,
      };
    }
    result.rainYearMm = null;
    // An invalid current counter may signal a reset or fault; do not hide it.
    if (current.rainYearStatus === "invalid") return result;
    if (!year) return result;
    const candidates = history.filter(
      (row) =>
        finite(row.timestamp) &&
        row.timestamp <= current.timestamp &&
        current.timestamp - row.timestamp <= 900 &&
        localYear(row.timestamp, timezone) === year &&
        finite(row.rainYearMm) &&
        row.rainYearMm >= 0 &&
        row.rainYearStatus !== "invalid",
    );
    const latest = candidates.reduce(
      (best, row) => (!best || row.timestamp > best.timestamp ? row : best),
      null,
    );
    // This is an actual reported yearly counter, never a sum of partial history
    // or the lifetime totalrainin counter. Retain its own observation time.
    return latest
      ? {
          ...result,
          rainYearMm: latest.rainYearMm,
          rainYearSource: "history",
          rainYearTimestamp: latest.timestamp,
        }
      : result;
  }
  function reportedRain(current) {
    return [
      ["rainLastHourMm", "past hour"],
      ["rainLast24hMm", "past 24h"],
      ["rainLast3hMm", "past 3h"],
      ["rainLast6hMm", "past 6h"],
      ["rainDayMm", "since station midnight"],
    ]
      .filter(
        ([key]) =>
          finite(current?.[key]) && current[key] >= 0 && current[key] <= 3000,
      )
      .map(([key, period]) => ({ period, mm: current[key] }));
  }
  function publicLabels(source) {
    const name =
      source === "wunderground" ? "WEATHER UNDERGROUND" : source === "met-eireann"
        ? "MET ÉIREANN"
        : source === "cwop"
          ? "CWOP"
          : source === "metar"
            ? "AIRPORT"
            : "WEATHER";
    return {
      observation: `${name} OBSERVATION`,
      badge: `PUBLIC ${name} STATION`,
      delayed: `${name} · DELAYED`,
    };
  }
  function rainLedgerNote(weather, countryCode) {
    if (weather?.rain?.source === "Met Éireann" || countryCode === "IE") {
      const gauge = weather?.rain?.stationName
        ? `Met Éireann ${weather.rain.stationName}`
        : "a separate Met Éireann gauge";
      return `Rain ledger: ${gauge}${["cwop", "wunderground"].includes(weather?.source) ? "; separate from the " + (weather.source === "cwop" ? "CWOP" : "Weather Underground") + " station" : ""}. Monthly/YTD totals need complete history. `;
    }
    return "Rain-reporting station required. The ledger uses a separate named Environment Agency gauge where available. ";
  }
  const api = {
    beaufort,
    withYearRain,
    reportedRain,
    publicLabels,
    rainLedgerNote,
  };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherReadings = api;
})(typeof window === "object" ? window : globalThis);
