((root) => {
  "use strict";
  function create({ api, getSettings }) {
    const $ = (id) => document.getElementById(id),
      fixed = (v, unit) =>
        typeof v === "number" ? v.toFixed(1) + unit : "Unavailable";
    let data = null;
    async function refresh() {
      const key = JSON.stringify([
        getSettings()?.latitude,
        getSettings()?.longitude,
      ]);
      try {
        const value = await api("/api/monthly");
        if (
          key !==
          JSON.stringify([getSettings()?.latitude, getSettings()?.longitude])
        )
          return;
        data = value;
        $("monthlyProvider").textContent =
          `${value.source || "Met Office"} · MONTHLY HISTORY`;
        const month = value.months.find((m) => m.month === value.month);
        $("monthlyStation").textContent =
          `${value.station} · ${value.distanceKm} km away`;
        $("monthlyPeriod").textContent = month.name + " · full-month average";
        $("monthlyTemp").textContent = fixed(month.meanC, "°C");
        $("monthlyRain").textContent = fixed(month.rainMm, " mm");
        $("monthlyLatest").textContent =
          `Calculated 1991–2020 · archive through ${value.latest}`;
        $("monthlyOfficialLink").href = value.sourceUrl;
      } catch (error) {
        $("monthlyStation").textContent = "Local monthly archive unavailable";
        $("monthlyLatest").textContent =
          "Retrying automatically. Open the official archive for the latest data.";
      }
    }
    $("monthlyDetailsBtn").addEventListener("click", () => {
      if (!data) return;
      const rows = $("monthlyRows");
      rows.replaceChildren();
      $("monthlyDialogNote").textContent =
        `${data.station} · ${data.note} Latest report: ${data.latest}. * Estimated, P provisional.`;
      for (const month of data.months) {
        const row = document.createElement("tr"),
          o = month.observed;
        const delta =
          o?.meanC != null && month.meanC != null
            ? o.meanC - month.meanC
            : null;
        const label =
          delta == null
            ? "Unavailable"
            : Math.abs(delta) <= 5 / 9
              ? "→ Near average"
              : delta > 0
                ? "↑ Above average"
                : "↓ Below average";
        for (const text of [
          month.name,
          fixed(month.meanC, "°C"),
          fixed(o?.meanC, "°C") +
            (o?.estimated ? " *" : "") +
            (o?.provisional ? " P" : ""),
          label,
          fixed(month.rainMm, " mm"),
          fixed(o?.rainMm, " mm"),
          month.wettest
            ? `${fixed(month.wettest.mm, " mm")} · ${month.wettest.years.join(", ")}`
            : "Unavailable",
          month.driest
            ? `${fixed(month.driest.mm, " mm")} · ${month.driest.years.join(", ")}`
            : "Unavailable",
        ]) {
          const cell = document.createElement("td");
          cell.textContent = text;
          row.append(cell);
        }
        rows.append(row);
      }
      $("monthlyDialog").showModal();
    });
    return { refresh };
  }
  root.WeatherUKMonthly = { create };
})(globalThis);
