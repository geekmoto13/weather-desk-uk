/* Pure rules shared by the dashboard, copied report and exported weather card. */
((root) => {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  function bostonDay(seconds, zone = "Europe/London") {
    if (!finite(seconds)) return null;
    const date = new Date(seconds * 1000);
    if (!Number.isFinite(date.getTime())) return null;
    const p = Object.fromEntries(
      new Intl.DateTimeFormat("en-US", {
        timeZone: zone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      })
        .formatToParts(date)
        .map((p) => [p.type, p.value]),
    );
    return `${p.year}-${p.month}-${p.day}`;
  }
  function record(value, key) {
    if (
      !value ||
      !finite(value[key]) ||
      !Array.isArray(value.years) ||
      !value.years.length ||
      !value.years.every((y) => Number.isInteger(y) && y >= 1700 && y <= 3000)
    )
      return null;
    if (key === "valueIn" && value[key] < 0) return null;
    return value;
  }
  function compare(mm, normal) {
    if (!finite(mm) || mm < 0 || !finite(normal) || normal < 0)
      return {
        kind: "unknown",
        arrow: "",
        label: "Unavailable",
        difference: null,
      };
    const actualHundredths = Math.round((mm + 1e-9) * 10),
      normalHundredths = Math.round((normal * 25.4 + 1e-9) * 10);
    const difference = (actualHundredths - normalHundredths) / 254;
    return {
      kind: difference > 0 ? "above" : difference < 0 ? "below" : "at",
      arrow: difference > 0 ? "↑" : difference < 0 ? "↓" : "→",
      label: difference > 0 ? "Above" : difference < 0 ? "Below" : "At",
      difference,
    };
  }
  function reference(
    climate,
    observation,
    now = Date.now() / 1000,
    expectedStation = null,
  ) {
    const zone = climate?.timezone || "Europe/London";
    if (expectedStation && climate?.stationId !== expectedStation)
      climate = null;
    const today = bostonDay(now, zone),
      sameDate = climate?.date === today;
    const result = {
      date: sameDate ? today : null,
      high: null,
      low: null,
      rain: null,
      monthNormal: null,
      yearNormal: null,
      cached: !!climate?.stale,
      month: compare(null, null),
      year: compare(null, null),
    };
    if (!sameDate) return result;
    result.high = record(climate.records?.high, "valueF");
    result.low = record(climate.records?.low, "valueF");
    result.rain = record(climate.records?.rain, "valueIn");
    if (climate.schemaVersion !== 2 || climate.normal?.through !== today)
      return result;
    for (const [name, field] of [
      ["month", "rainMonthToDateIn"],
      ["year", "rainYearToDateIn"],
    ]) {
      const normal = climate.normal[field];
      result[name + "Normal"] = finite(normal) && normal >= 0 ? normal : null;
      const stamp =
        name === "year"
          ? (observation?.rainYearTimestamp ?? observation?.timestamp)
          : observation?.timestamp;
      if (
        bostonDay(stamp, zone) === today &&
        now - stamp <= 900 &&
        stamp <= now + 300
      )
        result[name] = compare(
          observation[name === "month" ? "rainMonthMm" : "rainYearMm"],
          result[name + "Normal"],
        );
    }
    return result;
  }
  function rainRecords(
    payload,
    now = Date.now() / 1000,
    expectedStation = "BOSthr",
    zone = "Europe/London",
  ) {
    const day = bostonDay(now, zone),
      year = Number(day?.slice(0, 4));
    const result = {
      date: day,
      monthName: new Intl.DateTimeFormat("en-US", {
        month: "long",
        timeZone: "UTC",
      }).format(new Date((day || "2000-01-01") + "T12:00:00Z")),
      monthWettest: null,
      monthDriest: null,
      yearWettest: null,
      yearDriest: null,
      cached: !!payload?.stale,
      startYear: null,
      throughYear: null,
    };
    if (
      payload?.schemaVersion !== 2 ||
      payload.stationId !== expectedStation ||
      payload.date !== day ||
      payload.through !== day ||
      payload.throughYear !== year - 1
    )
      return result;
    result.startYear = payload.startYear;
    result.throughYear = payload.throughYear;
    for (const [period, data] of [
      ["month", payload.month],
      ["year", payload.year],
    ])
      for (const name of ["wettest", "driest"]) {
        const value = record(data?.[name], "valueIn");
        if (value && value.years.every((y) => y <= payload.throughYear))
          result[period + name[0].toUpperCase() + name.slice(1)] = value;
      }
    return result;
  }
  function monthlyReference(
    climate,
    annual,
    month,
    observation,
    now = Date.now() / 1000,
    expectedStation = "BOSthr",
  ) {
    const zone = climate?.timezone || "Europe/London";
    const result = {
      normalIn: null,
      comparison: compare(null, null),
      cached: !!climate?.stale,
      through: null,
    };
    if (
      climate?.schemaVersion !== 2 ||
      climate.stationId !== expectedStation ||
      !Number.isInteger(annual?.year) ||
      annual.year < 1700 ||
      annual.year > 3000 ||
      climate.date?.slice(0, 4) !== String(annual.year) ||
      !Number.isInteger(month?.month) ||
      month.month < 1 ||
      month.month > 12 ||
      !Array.isArray(climate.normal?.rainMonths)
    )
      return result;
    const start = `${annual.year}-${String(month.month).padStart(2, "0")}-01`;
    const live = month.method === "live month to date";
    const through = live
      ? annual.through
      : new Date(Date.UTC(annual.year, month.month, 0))
          .toISOString()
          .slice(0, 10);
    const normal = climate.normal.rainMonths.find(
      (n) =>
        n &&
        n.month === month.month &&
        n.start === start &&
        n.through === through &&
        n.missingNormalDays === 0,
    );
    if (!normal || !finite(normal.normalIn) || normal.normalIn < 0)
      return result;
    result.normalIn = normal.normalIn;
    result.through = through;
    // Completed monthly observations remain historical facts when today's
    // station reading is stale. Live MTD follows the ledger's freshness rule.
    const stamp = month.timestamp ?? observation?.timestamp;
    if (
      !live ||
      (bostonDay(stamp, zone) === through &&
        now - stamp <= 900 &&
        stamp <= now + 300 &&
        bostonDay(observation?.timestamp, zone) === through &&
        now - observation.timestamp <= 900 &&
        observation.timestamp <= now + 300)
    )
      result.comparison = compare(
        finite(month.inches) ? month.inches * 25.4 : null,
        result.normalIn,
      );
    return result;
  }
  const api = { bostonDay, compare, reference, rainRecords, monthlyReference };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherClimate = api;
})(typeof window === "object" ? window : globalThis);
