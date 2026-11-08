/* Experimental historical thirds. Missing history never becomes a green cell. */
((root) => {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  const comparisonLabel = (day) =>
    day.status === "above"
      ? "↑ Warmer than the historical middle third"
      : day.status === "below"
        ? "↓ Cooler than the historical middle third"
        : day.status === "at"
          ? "→ Within the historical middle third"
          : day.note || "Daily comparison unavailable";
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
      dayInZone(
        Date.now(),
        getSettings()?.timezone || "America/New_York",
      ).slice(0, 7);
    const identity = () => {
      const s = getSettings() || {};
      return `${s.latitude}|${s.longitude}|${s.timezone}|${s.climateStation?.id}|${getSourceKey()}`;
    };
    const mean = (value) =>
      finite(value)
        ? `${(((value - 32) * 5) / 9).toFixed(1)}°C / ${value.toFixed(1)}°F`
        : "Unavailable";
    const cutoff = (value) =>
      `${(((value - 32) * 5) / 9).toFixed(2)}°C / ${value.toFixed(2)}°F`;
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
      $("calendarDayStatus").textContent = comparisonLabel(day);
      $("calendarDayMean").textContent = mean(day.meanF);
      $("calendarDayNormal").textContent = mean(day.normalF);
      $("calendarDayBand").textContent = finite(day.lowerThirdF) && finite(day.upperThirdF)
        ? `${cutoff(day.lowerThirdF)} to ${cutoff(day.upperThirdF)}`
        : "Unavailable — no substitute range";
      $("calendarDayCoverage").textContent = Number.isInteger(day.baselineYears)
        ? `${day.baselineYears} of ${day.baselineExpectedYears} reference years (at least ${day.baselineRequiredYears} required)${day.baselineExpectedYears === 8 ? "; leap day has a smaller sample" : ""}`
        : "Historical reference not loaded";
      $("calendarDayRange").textContent =
        `High ${mean(day.highF)} · Low ${mean(day.lowF)}`;
      $("calendarDayDeparture").textContent = finite(day.departureF)
        ? `${day.departureF >= 0 ? "+" : ""}${day.departureF.toFixed(2)}°F / ${((day.departureF * 5) / 9).toFixed(1)}°C`
        : "Unavailable";
      $("calendarDaySource").textContent =
        `Observed: ${payload?.source === "ambient" ? "your Ambient station" : payload?.stationName || "local climate station"}. Calculated average and historical reference: ${payload?.stationName || "local climate station"} · 1991–2020. These calculated references are not published agency normals. Mean = (daily high + daily low) ÷ 2. Green is between this date’s historical 33⅓ and 66⅔ percentiles, including both endpoints; it need not have zero departure from normal. Cutoffs use linear interpolation of the paired daily means. Colours use full precision; displayed cutoffs are rounded. Experimental daily adaptation of CPC’s historical thirds, not official NOAA daily categories. At least 80% of the possible years are required; this coverage rule is our test choice. Today remains uncoloured.`;
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
        const label = comparisonLabel(day);
        cell.setAttribute(
          "aria-label",
          `${shortDate(day.date)}: ${label}${finite(day.departureF) ? `, departure ${day.departureF.toFixed(2)} degrees Fahrenheit` : ""}`,
        );
        cell.title = `${shortDate(day.date)} · ${label}${finite(day.meanF) ? ` · mean ${mean(day.meanF)}, normal ${mean(day.normalF)}` : ""}${finite(day.lowerThirdF) ? ` · middle third ${cutoff(day.lowerThirdF)} to ${cutoff(day.upperThirdF)}` : ""}`;
        cell.disabled = day.status === "future";
        cell.addEventListener("click", () => details(day));
        target.append(cell);
      }
      const s = getSettings() || {};
      $("calendarSource").textContent =
        `${payload?.source === "ambient" ? "Your station" : "Archive daily"} vs ${payload?.stationName || s.climateStation?.name || "local climate"} · 1991–2020 · test`;
      $("calendarStatus").textContent =
        payload?.error ||
        payload?.baselineError ||
        (!payload
          ? "Loading daily temperatures…"
          : payload.loading
            ? `${payload.phase || "Loading days"} ${payload.completed}/${payload.total}`
            : month === currentMonth()
              ? "Historical thirds · Today is in progress"
              : "Historical thirds · Select a day for cutoffs");
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
  const api = { create, shiftMonth, dayInZone, comparisonLabel };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherCalendar = api;
})(typeof window === "object" ? window : globalThis);
