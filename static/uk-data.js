/* Imported MIDAS observations and the separately identified public rain gauge. */
((root) => {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  const rainText = (p) =>
    finite(p?.mm)
      ? `${p.complete ? "" : "≥ "}${p.mm.toFixed(1)} mm`
      : "Unavailable";
  const coverage = (p) =>
    !p
      ? "Not available"
      : p.missing > 0
        ? `${p.missing} missing ${p.intervalMinutes || 15}-min interval${p.missing === 1 ? "" : "s"}`
        : !finite(p.mm)
          ? "No usable intervals"
          : p.complete
            ? "All intervals present"
            : `${p.missing} missing ${p.intervalMinutes || 15}-min interval${p.missing === 1 ? "" : "s"}`;
  function create({ api, getSettings, getWeather, onRefresh }) {
    const $ = (id) => document.getElementById(id),
      put = (id, value) => {
        $(id).textContent = value;
      };
    const time = (stamp) =>
      finite(stamp)
        ? new Intl.DateTimeFormat("en-GB", {
            timeZone: getSettings()?.timezone || "Europe/London",
            day: "numeric",
            month: "short",
            hour: "2-digit",
            minute: "2-digit",
            timeZoneName: "short",
          }).format(new Date(stamp * 1000))
        : "Not yet reported";
    let archive = null;
    const years = (r) =>
      r.years.slice(0, 4).join(", ") +
      (r.years.length > 4 ? ` +${r.years.length - 4} more` : "");
    function renderRain(weather) {
      const active = ["metar", "cwop", "wunderground", "public", "met-eireann"].includes(
          weather?.source,
        ),
        r =
          weather?.rain ||
          (weather?.source === "met-eireann"
            ? { providerLabel: "Met Éireann", loading: weather.loading }
            : null);
      const agency = r?.providerLabel || "EA";
      $("publicRainDetailsBtn").hidden = !active;
      $("rainGaugeSource").hidden = !active;
      if (!active) {
        put("rainRateLabel", "Rate");
        return;
      }
      put(
        "rainGaugeSource",
        r?.stationName
          ? `${agency} ${r.stationName} · ${r.distanceKm} km away · through ${time(r.timestamp)}${r.stale ? " · delayed" : ""}${r.error ? " · refresh failed" : ""}${agency === "Met Éireann" ? " · UTC totals" : ""}`
          : r?.loading
            ? "Finding a measured rain gauge…"
            : r?.error || "No nearby rain gauge available",
      );
      $("rainGaugeSource").title = (r?.note || "") + " " + (r?.error || "");
      const ps = r?.periods || {};
      for (const [name, id] of [
        ["day", "dailyRain"],
        ["event", "rainEvent"],
        ["week", "rainWeek"],
        ["month", "rainMonth"],
        ["year", "rainYear"],
      ]) {
        put(id, rainText(ps[name]));
        put(
          id === "dailyRain" ? "dailyRainMm" : id + "Mm",
          finite(ps[name]?.mm)
            ? `${(ps[name].mm / 25.4).toFixed(2)} in${ps[name].complete ? "" : " · gaps"}`
            : r?.loading
              ? "Loading gauge history…"
              : ps[name]?.missing > 0
                ? "Incomplete history"
                : agency === "Met Éireann" && name === "event"
                  ? "Not supplied by feed"
                  : "No gauge total",
        );
        $(id).title = coverage(ps[name]);
      }
      if (r?.stationName)
        put(
          "dailyRainMm",
          `${finite(ps.day?.mm) ? (ps.day.mm / 25.4).toFixed(2) + " in" + (ps.day.complete ? "" : " · gaps") : "No total"} · ${agency} ${r.stationName}`,
        );
      for (const [name, id] of [
        ["event", "shareEvent"],
        ["week", "shareWeek"],
        ["month", "shareMonth"],
        ["year", "shareYear"],
      ]) {
        put(
          id,
          `${name} ${rainText(ps[name])} · ${agency} ${r?.stationName || "gauge"}`,
        );
      }
      put(
        "rainHour",
        finite(r?.rateMmHr) ? `${r.rateMmHr.toFixed(1)} mm/h` : "Unavailable",
      );
      $("rainHour").title =
        agency === "Met Éireann"
          ? "An instantaneous rain rate is not supplied by this hourly observation feed."
          : "Equivalent rate from the latest 15-minute gauge amount; " +
            time(r?.timestamp);
      put("rainRateLabel", agency === "Met Éireann" ? "Rate" : "15-min rate");
    }
    async function openRain() {
      $("publicRainDialog").showModal();
      put("publicRainStatus", "Loading gauge details…");
      try {
        const r = getWeather()?.rain || (await api("/api/rain"));
        put(
          "publicRainTitle",
          r.stationName
            ? `Rainfall at ${r.stationName}`
            : "Public rainfall gauge",
        );
        put(
          "publicRainStatus",
          r.stationName
            ? `${r.providerLabel || "Environment Agency"} · ${r.distanceKm} km from your selected location · through ${time(r.timestamp)}${r.loading ? " · refreshing" : ""}${r.stale ? " · delayed" : ""}`
            : r.error || "Finding a gauge…",
        );
        put("publicRainNote", r.note || "");
        put(
          "publicRainLicense",
          r.providerLabel === "Met Éireann"
            ? "Copyright Met Éireann · www.met.ie · CC BY 4.0. Provisional hourly observations; totals and units calculated for display. Gaps remain missing."
            : "Contains Environment Agency information © Environment Agency and/or database right. Open Government Licence v3.0. Provisional observations may be revised.",
        );
        put("publicRainError", r.error || "");
        const target = $("publicRainRows");
        target.replaceChildren();
        for (const m of r.months || []) {
          const tr = document.createElement("tr");
          for (const value of [
            new Intl.DateTimeFormat("en-GB", {
              month: "long",
              timeZone: "UTC",
            }).format(new Date(Date.UTC(r.year, m.month - 1, 1))),
            rainText(m),
            coverage(m),
            m.unchecked ? `${m.unchecked} provisional` : "Quality checked",
          ]) {
            const td = document.createElement("td");
            td.textContent = value;
            tr.append(td);
          }
          target.append(tr);
        }
        put(
          "publicRainTotal",
          `Year to date: ${rainText(r.periods?.year)} · ${coverage(r.periods?.year)}. ≥ means a measured lower bound; missing rainfall is not assumed to be zero.`,
        );
        $("publicRainLink").href =
          r.sourceUrl ||
          "https://environment.data.gov.uk/hydrology/doc/reference";
        $("publicRainLink").textContent =
          r.providerLabel === "Met Éireann"
            ? "Met Éireann source data ↗"
            : "EA source data ↗";
      } catch (e) {
        put("publicRainStatus", e.message);
      }
    }
    async function midasStatus() {
      archive = await api("/api/midas");
      const select = $("midasStation"),
        old = select.value;
      select.replaceChildren();
      for (const s of archive.stations) {
        const option = document.createElement("option");
        option.value = s.id;
        option.textContent = `${s.name} · ${s.first}–${s.latest}`;
        select.append(option);
      }
      if (archive.stations.some((s) => s.id === old)) select.value = old;
      $("midasExplorer").hidden = !archive.stations.length;
      $("midasImportDetails").open = !archive.stations.length;
      $("midasEmpty").hidden = !!archive.stations.length;
      put(
        "midasImportStatus",
        archive.stations.length
          ? `${archive.stations.length} saved station${archive.stations.length === 1 ? "" : "s"}. Imported files stay available after restarting.`
          : "No MIDAS files imported yet. NOAA and Met Office monthly history continue to work.",
      );
      if (archive.stations.length) await midasView();
    }
    async function midasView() {
      const sid = $("midasStation").value,
        date = $("midasDate").value;
      if (!sid || !date) return;
      put("midasViewStatus", "Loading saved observations…");
      try {
        const v = await api(
          "/api/midas?station=" +
            encodeURIComponent(sid) +
            "&date=" +
            encodeURIComponent(date),
        );
        if (sid !== $("midasStation").value || date !== $("midasDate").value)
          return;
        put(
          "midasViewStatus",
          `${v.station} · archive ${v.first}–${v.latest} · 24 hours ending 09:00 UTC on ${v.date}`,
        );
        const fixed = (n, unit) =>
          finite(n) ? n.toFixed(1) + unit : "Unavailable";
        const rows = $("midasDailyRows");
        rows.replaceChildren();
        for (const [label, key, unit] of [
          ["Maximum temperature", "highC", "°C"],
          ["Minimum temperature", "lowC", "°C"],
          ["Rainfall", "rainMm", " mm"],
        ]) {
          const record = v.records[key],
            tr = document.createElement("tr");
          for (const text of [
            label,
            fixed(v.observed[key], unit),
            fixed(v.normal[key], unit),
            record
              ? `${fixed(record.value, unit)} · ${years(record)}`
              : "Unavailable",
          ]) {
            const td = document.createElement("td");
            td.textContent = text;
            if (record) td.title = "Record years: " + record.years.join(", ");
            tr.append(td);
          }
          rows.append(tr);
        }
        put(
          "midasFlags",
          v.flags.length
            ? "Selected observations: " +
                v.flags.join(", ") +
                ". Trace rain is retained as a trace flag and contributes zero to sums."
            : "No supported observations imported for the selected day. Records and averages use the available imported history.",
        );
        const months = $("midasMonthRows");
        months.replaceChildren();
        const record = (r) =>
          r ? `${fixed(r.value, " mm")} · ${years(r)}` : "Unavailable";
        for (const m of v.months) {
          const tr = document.createElement("tr");
          for (const text of [
            m.month,
            fixed(m.meanC, "°C"),
            fixed(m.normalMeanC, "°C"),
            fixed(m.rainMm, " mm"),
            fixed(m.normalRainMm, " mm"),
            record(m.wettest),
            record(m.driest),
            `${m.days}/${m.expected} days have at least one observation`,
          ]) {
            const td = document.createElement("td");
            td.textContent = text;
            tr.append(td);
          }
          months.append(tr);
        }
        put(
          "midasYearRain",
          `Rainfall for observation-ending dates January 1–${v.date}: ${fixed(v.yearRainMm, " mm")}. Prior-year maximum: ${record(v.yearRainWettest)}. Prior-year minimum: ${record(v.yearRainDriest)}. Totals require every daily rainfall observation; missing days stay unavailable.`,
        );
        put("midasMethod", v.note);
      } catch (e) {
        put("midasViewStatus", e.message);
      }
    }
    async function openMidas() {
      $("midasDialog").showModal();
      if (!$("midasDate").value)
        $("midasDate").value = new Intl.DateTimeFormat("en-CA", {
          timeZone: "Europe/London",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
        }).format(new Date());
      try {
        await midasStatus();
      } catch (e) {
        put("midasImportStatus", e.message);
      }
    }
    async function importMidas() {
      const files = [...$("midasFiles").files];
      if (!files.length) {
        put(
          "midasImportStatus",
          "Choose MIDAS observation CSVs or a ZIP first.",
        );
        return;
      }
      if (
        files.length > 300 ||
        files.reduce((n, f) => n + f.size, 0) > 12000000
      ) {
        put(
          "midasImportStatus",
          "Choose at most 300 files and 12 MB per import.",
        );
        return;
      }
      $("midasImportBtn").disabled = true;
      put(
        "midasImportStatus",
        "Importing and checking observation periods and quality flags…",
      );
      try {
        const encoded = [];
        for (const f of files) {
          const bytes = new Uint8Array(await f.arrayBuffer());
          let binary = "";
          for (let i = 0; i < bytes.length; i += 16384)
            binary += String.fromCharCode(...bytes.subarray(i, i + 16384));
          encoded.push({ name: f.name, data: btoa(binary) });
        }
        const result = await api("/api/midas/import", { files: encoded });
        await midasStatus();
        put(
          "midasImportStatus",
          `Saved ${result.importedValues} temperature/rainfall values. ${result.excludedRows} rows excluded by the period/quality rules. Re-importing the same files does not duplicate observations.`,
        );
        $("midasFiles").value = "";
      } catch (e) {
        put("midasImportStatus", e.message);
      } finally {
        $("midasImportBtn").disabled = false;
      }
    }
    $("publicRainDetailsBtn").addEventListener("click", openRain);
    $("midasOpenBtn").addEventListener("click", openMidas);
    $("midasSettingsBtn").addEventListener("click", () => {
      $("settingsDialog").close();
      openMidas();
    });
    $("midasImportBtn").addEventListener("click", importMidas);
    $("midasStation").addEventListener("change", midasView);
    $("midasDate").addEventListener("change", midasView);
    return { renderRain, openRain };
  }
  const api = { rainText, coverage, create };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherUKData = api;
})(globalThis);
