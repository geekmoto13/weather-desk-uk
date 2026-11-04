/* Completed daily means; missing data never uses the green 'near normal' cell. */
((root) => {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  const dayInZone = (stamp, zone) => {
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
  };
  function shiftMonth(month, delta) {
    const [y, m] = month.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1 + delta, 1)).toISOString().slice(0, 7);
  }
  function create({ api, getSettings, getSourceKey, isVisible }) {
    const $ = (id) => document.getElementById(id);
    let month = null,
      context = "",
      payload = null,
      revision = 0,
      timer = null;
    const currentMonth = () =>
      dayInZone(Date.now(), getSettings()?.timezone || "Europe/London").slice(
        0,
        7,
      );
    const identity = () => {
      const s = getSettings() || {};
      return `${s.latitude}|${s.longitude}|${s.timezone}|${s.climateStation?.id}|${getSourceKey()}`;
    };
    const mean = (value) =>
      finite(value)
        ? `${(((value - 32) * 5) / 9).toFixed(1)}°C / ${value.toFixed(1)}°F`
        : "Unavailable";
    const shortDate = (date) =>
      new Intl.DateTimeFormat("en-US", {
        month: "short",
        day: "numeric",
        timeZone: "UTC",
      }).format(new Date(date + "T12:00:00Z"));
    function details(day) {
      $("calendarDayTitle").textContent = new Intl.DateTimeFormat("en-US", {
        weekday: "long",
        month: "long",
        day: "numeric",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(day.date + "T12:00:00Z"));
      const status =
        day.status === "above"
          ? "↑ Above normal"
          : day.status === "below"
            ? "↓ Below normal"
            : day.status === "at"
              ? "→ Near normal (within ±1°F / ±0.6°C)"
              : day.note || "Daily observation unavailable";
      $("calendarDayStatus").textContent = status;
      $("calendarDayMean").textContent = mean(day.meanF);
      $("calendarDayNormal").textContent = mean(day.normalF);
      $("calendarDayRange").textContent =
        `High ${mean(day.highF)} · Low ${mean(day.lowF)}`;
      $("calendarDayDeparture").textContent = finite(day.departureF)
        ? `${day.departureF >= 0 ? "+" : ""}${day.departureF.toFixed(2)}°F / ${((day.departureF * 5) / 9).toFixed(1)}°C`
        : "Unavailable";
      $("calendarDaySource").textContent =
        `Observed: ${payload?.source === "ambient" ? "your Ambient station" : payload?.stationName || "local climate station"}. Calculated average: ${payload?.stationName || "local climate station"} · calculated 1991–2020. These are calculated references, not published agency normals. Mean temperature = (daily high + daily low) ÷ 2. Green means within ±1°F (±0.6°C) of normal, including both endpoints. This near-normal band is a dashboard display choice, not an official meteorological tolerance.`;
      $("calendarDayDialog").showModal();
    }
    function render() {
      if (!month) return;
      $("calendarMonth").textContent = new Intl.DateTimeFormat("en-US", {
        month: "long",
        year: "numeric",
        timeZone: "UTC",
      }).format(new Date(month + "-01T12:00:00Z"));
      $("calendarNext").disabled = month >= currentMonth();
      $("calendarPrev").disabled = month <= "1970-01";
      const target = $("calendarDays");
      target.replaceChildren();
      const offset = new Date(month + "-01T12:00:00Z").getUTCDay();
      for (let i = 0; i < offset; i++) {
        const spacer = document.createElement("span");
        spacer.className = "calendar-spacer";
        spacer.setAttribute("aria-hidden", "true");
        target.append(spacer);
      }
      const days = payload?.month === month ? payload.days : [];
      const count = new Date(
        Number(month.slice(0, 4)),
        Number(month.slice(5, 7)),
        0,
      ).getDate();
      target.classList.toggle("six-weeks", offset + count > 35);
      for (let n = 1; n <= count; n++) {
        const day = days?.find((d) => d.day === n) || {
          date: month + "-" + String(n).padStart(2, "0"),
          day: n,
          status: "missing",
          note: "Loading daily observations",
        };
        const cell = document.createElement("button");
        cell.type = "button";
        cell.className = "calendar-day";
        cell.dataset.status = day.status;
        const number = document.createElement("span");
        number.textContent = String(n);
        const symbol = document.createElement("small");
        symbol.textContent =
          day.status === "above"
            ? "↑"
            : day.status === "below"
              ? "↓"
              : day.status === "at"
                ? "="
                : day.status === "pending"
                  ? "·"
                  : "";
        cell.append(number, symbol);
        cell.classList.toggle("is-today", day.date === payload?.today);
        const label =
          day.status === "above"
            ? "above normal"
            : day.status === "below"
              ? "below normal"
              : day.status === "at"
                ? "near normal, within one degree Fahrenheit"
                : day.note || "unavailable";
        cell.setAttribute(
          "aria-label",
          `${shortDate(day.date)}: ${label}${finite(day.departureF) ? `, departure ${day.departureF.toFixed(2)} degrees Fahrenheit` : ""}`,
        );
        cell.title = `${shortDate(day.date)} · ${label}${finite(day.meanF) ? ` · mean ${day.meanF.toFixed(1)}°F, normal ${day.normalF?.toFixed(1)}°F` : ""}`;
        cell.disabled = day.status === "future";
        cell.addEventListener("click", () => details(day));
        target.append(cell);
      }
      const s = getSettings() || {};
      $("calendarSource").textContent =
        `${payload?.source === "ambient" ? "Your station" : "UK archive"} vs ${payload?.stationName || s.climateStation?.name || "local climate"} · calc. 1991–2020${payload?.archiveThrough ? ` · archive to ${payload.archiveThrough}` : ""}`;
      $("calendarStatus").textContent =
        payload?.error ||
        (!payload
          ? "Loading daily temperatures…"
          : payload.loading
            ? `Loading days · ${payload.completed}/${payload.total} · saved days stay available`
            : month === currentMonth()
              ? "Daily mean vs normal · Today is in progress"
              : "Completed daily means · Select a day for details");
    }
    async function request(token = revision) {
      clearTimeout(timer);
      if (!isVisible() || token !== revision) return;
      const key = identity(),
        selected = month;
      try {
        const result = await api(
          "/api/calendar?month=" + encodeURIComponent(selected),
        );
        if (token !== revision || key !== identity() || selected !== month)
          return;
        if (result.stationId !== getSettings()?.climateStation?.id)
          throw new Error(
            "The climate location changed. Refreshing the calendar.",
          );
        payload = result;
        render();
        timer = setTimeout(
          () => request(token),
          result.loading ? 3000 : 1800000,
        );
      } catch (error) {
        if (token !== revision) return;
        $("calendarStatus").textContent = error.message;
        timer = setTimeout(() => request(token), 60000);
      }
    }
    function sync() {
      const key = identity();
      if (key !== context) {
        context = key;
        revision++;
        clearTimeout(timer);
        payload = null;
        month = currentMonth();
        render();
        request();
      }
    }
    function navigate(delta) {
      revision++;
      clearTimeout(timer);
      month = shiftMonth(month, delta);
      payload = null;
      render();
      request();
    }
    $("calendarPrev").addEventListener("click", () => navigate(-1));
    $("calendarNext").addEventListener("click", () => navigate(1));
    $("calendarToday").addEventListener("click", () => {
      revision++;
      month = currentMonth();
      payload = null;
      render();
      request();
    });
    return {
      sync,
      refresh: () => {
        sync();
        request();
      },
      pause: () => {
        clearTimeout(timer);
        revision++;
      },
    };
  }
  const api = { create, shiftMonth, dayInZone };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherCalendar = api;
})(typeof window === "object" ? window : globalThis);
