/* Calendar-day comparisons use the selected date, independent of today's clock. */
(function (root, factory) {
  const value = factory(
    typeof module === "object" && module.exports
      ? require("./climate.js")
      : root.WeatherClimate,
  );
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.WeatherPast = value;
})(
  typeof globalThis !== "undefined" ? globalThis : this,
  function (climateRules) {
    "use strict";
    const finite = (n) => typeof n === "number" && Number.isFinite(n);
    const canonicalZone = (z) =>
      ({
        GB: "Europe/London",
        "GB-Eire": "Europe/London",
        "Europe/Belfast": "Europe/London",
        "US/Eastern": "America/New_York",
        EST5EDT: "America/New_York",
        "US/Central": "America/Chicago",
        CST6CDT: "America/Chicago",
        "US/Mountain": "America/Denver",
        MST7MDT: "America/Denver",
        "US/Pacific": "America/Los_Angeles",
        PST8PDT: "America/Los_Angeles",
      })[z] || z;
    function localDay(stamp, zone = "America/New_York") {
      if (!finite(stamp)) return null;
      if (!Number.isFinite(new Date(stamp).getTime())) return null;
      const parts = Object.fromEntries(
        new Intl.DateTimeFormat("en-US", {
          timeZone: zone,
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        })
          .formatToParts(new Date(stamp))
          .map((p) => [p.type, p.value]),
      );
      return `${parts.year}-${parts.month}-${parts.day}`;
    }
    function shiftDay(day, delta) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day || "")) return null;
      const date = new Date(day + "T12:00:00Z");
      if (
        !Number.isFinite(date.getTime()) ||
        date.toISOString().slice(0, 10) !== day
      )
        return null;
      date.setUTCDate(date.getUTCDate() + delta);
      return date.toISOString().slice(0, 10);
    }
    function model(payload, day, expectedStation = "BOSthr") {
      const p = payload?.date === day ? payload : {};
      const data =
        p.stationData?.date === day &&
        p.stationData.timezone === canonicalZone(p.timezone)
          ? p.stationData
          : null;
      const last = data?.last;
      const valid = last && localDay(last.timestamp * 1000, p.timezone) === day;
      const rainCalendar =
        valid &&
        (!last.stationTimezone ||
          canonicalZone(last.stationTimezone) === canonicalZone(p.timezone));
      const c =
        p.climate?.date === day && p.climate.stationId === expectedStation
          ? p.climate
          : null;
      const stamp = Date.parse(day + "T18:00:00Z") / 1000;
      const refs = climateRules.reference(c, null, stamp);
      const records = climateRules.rainRecords(
        p.records,
        stamp,
        expectedStation,
        p.timezone,
      );
      const normal =
        c?.schemaVersion === 2 && c.normal?.through === day ? c.normal : null;
      const observed = c?.observed?.date === day ? c.observed : null;
      const annual =
        rainCalendar &&
        p.annual?.through === day &&
        p.annual.start === day.slice(0, 4) + "-01-01" &&
        p.annual.complete &&
        localDay(p.annual.timestamp * 1000, p.timezone) === day
          ? p.annual
          : null;
      const period = (name, stationMm, normalIn) => {
        const row =
          c?.precipitation?.through === day ? c.precipitation[name] : null;
        const expectedStart =
          name === "month"
            ? day.slice(0, 7) + "-01"
            : day.slice(0, 4) + "-01-01";
        return {
          stationMm,
          normalIn,
          comparison: climateRules.compare(stationMm, normalIn),
          bostonIn:
            row?.start === expectedStart && row.through === day
              ? row.observedIn
              : null,
          bostonTrace: !!row?.trace,
          wettest: records[name + "Wettest"],
          driest: records[name + "Driest"],
        };
      };
      return {
        data: valid ? data : null,
        climate: c,
        observed,
        refs,
        records,
        normal,
        annual,
        rainCalendar,
        high: valid ? data.high : null,
        low: valid ? data.low : null,
        rainDayMm: rainCalendar ? data.rainDayMm : null,
        month: period(
          "month",
          rainCalendar ? last.rainMonthMm : null,
          refs.monthNormal,
        ),
        year: period("year", annual?.mm ?? null, refs.yearNormal),
      };
    }
    function create({
      api,
      getSettings,
      getStation,
      isVisible,
      onLive,
      onSettings,
    }) {
      const $ = (id) => document.getElementById(id);
      let selected = null,
        payload = null,
        revision = 0,
        timer = null,
        context = "",
        started = false;
      const settings = () => getSettings() || {};
      const zone = () =>
        canonicalZone(settings().timezone || "America/New_York");
      const today = () => localDay(Date.now(), zone());
      const identity = () =>
        `${getStation() || ""}|${zone()}|${settings().climateStation?.id || "BOSthr"}`;
      const climateName = () => settings().climateStation?.label || "Boston";
      const put = (id, value) => {
        $(id).textContent = value;
      };
      const value = (n, d = 1) => (finite(n) ? n.toFixed(d) : "—");
      const temp = (n) =>
        finite(n)
          ? `${value(n)}°F / ${value(((n - 32) * 5) / 9)}°C`
          : "Unavailable";
      const rain = (n, trace = false) =>
        finite(n)
          ? trace && n === 0
            ? "Trace"
            : `${value(n, 2)} in`
          : "Unavailable";
      const clock = (stamp) =>
        finite(stamp)
          ? new Intl.DateTimeFormat("en-US", {
              timeZone: zone(),
              hour: "numeric",
              minute: "2-digit",
            }).format(new Date(stamp * 1000))
          : "—";
      const dateLabel = (day) =>
        new Intl.DateTimeFormat("en-US", {
          timeZone: "UTC",
          weekday: "long",
          month: "long",
          day: "numeric",
          year: "numeric",
        }).format(new Date(day + "T12:00:00Z"));
      const years = (record) =>
        record?.years?.length
          ? record.years.slice(0, 3).join(", ") +
            (record.years.length > 3 ? ` +${record.years.length - 3}` : "")
          : "—";
      const recordRain = (r) =>
        r ? `${rain(r.valueIn, r.trace)} · ${years(r)}` : "Unavailable";
      function render() {
        if (!selected) return;
        const m = model(
            payload,
            selected,
            settings().climateStation?.id || "BOSthr",
          ),
          loading = !!payload?.loading || !payload;
        $("pastDate").value = selected;
        $("pastDate").max = today();
        $("pastPrevBtn").disabled = selected <= "1970-01-01";
        $("pastRetryBtn").disabled = loading;
        put("pastDayTitle", dateLabel(selected));
        put(
          "pastPlace",
          `${settings().location || "Your station"} · ${zone().replaceAll("_", " ")}`,
        );
        put(
          "pastMode",
          loading
            ? "LOADING DAY"
            : payload?.fromCache
              ? "SAVED DAY"
              : "HISTORICAL DAY",
        );
        put(
          "pastStatus",
          !payload
            ? "Loading the selected day…"
            : payload.stationError ||
                (!payload.connected
                  ? "Connect your station in Settings to add your own historical readings."
                  : loading
                    ? `Retrieving the selected day and matching ${climateName()} references…`
                    : m.data
                      ? `Your station readings and ${climateName()} comparisons for the same calendar date.`
                      : "No station readings are available for this day."),
        );
        $("pastConnectBtn").hidden = payload
          ? !!payload.connected
          : !!getStation();
        for (const name of ["high", "low"]) {
          const capital = name[0].toUpperCase() + name.slice(1),
            row = m[name],
            record = m.refs[name];
          put("past" + capital + "F", value(row?.value));
          put(
            "past" + capital + "C",
            finite(row?.value)
              ? `${value(((row.value - 32) * 5) / 9)} °C · ${clock(row.timestamp)}`
              : "No station temperature available",
          );
          put("pastBoston" + capital, temp(m.observed?.[name + "F"]));
          put("pastNormal" + capital, temp(m.normal?.[name + "F"]));
          put(
            "pastRecord" + capital,
            record
              ? `${temp(record.valueF)} · ${years(record)}`
              : "Unavailable",
          );
          $("pastRecord" + capital).title = record?.years?.join(", ") || "";
        }
        put(
          "pastRainDay",
          finite(m.rainDayMm) ? value(m.rainDayMm / 25.4, 2) : "—",
        );
        put(
          "pastRainDayMm",
          finite(m.rainDayMm)
            ? `${value(m.rainDayMm)} mm`
            : "No station daily counter available",
        );
        put("pastBostonRainDay", rain(m.observed?.precipIn, m.observed?.trace));
        put("pastNormalRainDay", rain(m.normal?.precipIn));
        put("pastRecordRainDay", recordRain(m.refs.rain));
        for (const period of ["month", "year"]) {
          const capital = period[0].toUpperCase() + period.slice(1),
            row = m[period],
            comp = row.comparison;
          put(
            "past" + capital + "Rain",
            finite(row.stationMm)
              ? `${value(row.stationMm / 25.4, 2)} in`
              : "Unavailable",
          );
          put(
            "past" + capital + "RainMm",
            finite(row.stationMm) ? `${value(row.stationMm)} mm` : "",
          );
          put("past" + capital + "Normal", rain(row.normalIn));
          put(
            "past" + capital + "Comparison",
            comp.kind === "unknown"
              ? "Comparison unavailable"
              : `${comp.arrow} ${comp.label}${comp.kind === "at" ? " normal" : ` · ${value(Math.abs(comp.difference), 2)} in`}`,
          );
          $("past" + capital + "Comparison").dataset.comparison = comp.kind;
          put(
            "pastBoston" + capital + "Rain",
            rain(row.bostonIn, row.bostonTrace),
          );
          put("past" + capital + "Wettest", recordRain(row.wettest));
          put("past" + capital + "Driest", recordRain(row.driest));
          put(
            "past" + capital + "Window",
            `${period === "month" ? selected.slice(0, 7) + "-01" : selected.slice(0, 4) + "-01-01"} through ${selected}`,
          );
        }
        const annual = payload?.annual;
        put(
          "pastYearMethod",
          m.annual
            ? m.annual.method === "reported"
              ? "Ambient yearly counter at the selected day’s last reading."
              : "Completed monthly station totals + the selected day’s month-to-date counter."
            : annual?.status === "building"
              ? `Loading saved months · ${annual.loadedMonths || 0}/${annual.expectedMonths || 0} available`
              : annual?.error ||
                "Year-to-date total will appear when matching station counters are available.",
        );
        const coverage = m.data?.coverage;
        put(
          "pastCoverage",
          coverage
            ? `${coverage.fullSpan ? "Day span available" : "Partial day"} · ${coverage.count} readings · ${clock(coverage.first)}–${clock(coverage.last)} · ${value(coverage.dayHours, 0)}-hour local day${payload.fromCache ? " · saved locally" : ""}`
            : "Station observations: waiting for the selected day.",
        );
        put(
          "pastStationNote",
          m.data
            ? !m.rainCalendar
              ? "Match Settings timezone to the station’s rain reset before comparing daily, monthly or yearly counters."
              : `Daily temperature extremes are from the returned readings. Rain uses the highest daily counter; MTD/YTD use the last available counters, as of ${clock(coverage.last)}.`
            : "Missing station readings are left blank.",
        );
        put(
          "pastGust",
          finite(m.data?.maxGustMph)
            ? `${value(m.data.maxGustMph)} mph`
            : "Unavailable",
        );
        put(
          "pastHumidity",
          finite(m.data?.meanHumidity)
            ? `${value(m.data.meanHumidity, 0)}%`
            : "Unavailable",
        );
        put(
          "pastPressure",
          finite(m.data?.pressureHpa)
            ? `${value(m.data.pressureHpa)} hPa`
            : "Unavailable",
        );
        const errors = [m.climate?.error, payload?.records?.error].filter(
          Boolean,
        );
        put(
          "pastClimateNote",
          errors.length
            ? "Some local references could not refresh. Saved references for this exact date are retained when available."
            : `${settings().climateStation?.name || "Boston Area"} / NOWData · RCC-ACIS · 1991–2020 normals · Records through the preceding year. Recent observations may be preliminary.`,
        );
      }
      async function request(force = false, token = revision) {
        if (!isVisible() || token !== revision) return;
        const day = selected,
          station = getStation() || "",
          key = identity();
        try {
          const result = await api(
            `/api/day?date=${encodeURIComponent(day)}${force ? "&refresh=1" : ""}`,
          );
          if (token !== revision || day !== selected || key !== identity())
            return;
          if (
            result.date !== day ||
            result.station !== station ||
            canonicalZone(result.timezone) !== zone()
          )
            throw new Error(
              "The historical response did not match this date and station.",
            );
          payload = result;
          render();
          if (result.loading)
            timer = setTimeout(() => request(false, token), 1500);
        } catch (error) {
          if (token !== revision) return;
          put(
            "pastStatus",
            error.message ||
              "The selected day could not load. Keep the local server open and retry.",
          );
          put("pastMode", payload ? "REFRESH FAILED" : "DAY UNAVAILABLE");
          if (!payload)
            put("pastCoverage", "No station readings loaded for this date.");
          $("pastRetryBtn").disabled = false;
        }
      }
      function select(day, force = false) {
        if (day === today()) {
          onLive();
          return;
        }
        if (!shiftDay(day, 0) || day < "1970-01-01" || day > today()) {
          put("pastStatus", "Choose today or an earlier valid date.");
          return;
        }
        clearTimeout(timer);
        revision++;
        selected = day;
        payload = null;
        render();
        const token = revision;
        timer = setTimeout(() => request(force, token), 160);
      }
      function open() {
        sync();
        select(
          selected && selected < today() ? selected : shiftDay(today(), -1),
        );
      }
      function close() {
        clearTimeout(timer);
        revision++;
      }
      function sync() {
        const current = identity();
        if (context !== current) {
          context = current;
          clearTimeout(timer);
          revision++;
          payload = null;
          if (started && isVisible())
            select(
              selected && selected < today() ? selected : shiftDay(today(), -1),
            );
        } else if (started && isVisible()) render();
      }
      function start() {
        if (started) return;
        started = true;
        context = identity();
        $("pastPrevBtn").addEventListener("click", () =>
          select(shiftDay(selected, -1)),
        );
        $("pastNextBtn").addEventListener("click", () =>
          select(shiftDay(selected, 1)),
        );
        $("pastDate").addEventListener("change", () =>
          select($("pastDate").value),
        );
        $("pastTodayBtn").addEventListener("click", onLive);
        $("pastRetryBtn").addEventListener("click", () =>
          select(selected, true),
        );
        $("pastConnectBtn").addEventListener("click", onSettings);
      }
      return { start, open, close, sync };
    }
    return { localDay, shiftDay, model, canonicalZone, create };
  },
);
