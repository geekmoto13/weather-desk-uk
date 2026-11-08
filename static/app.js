/* Weather Desk UI: local API only, no frameworks, no analytics, no CDN scripts. */
(() => {
  "use strict";
  const $ = (id) => document.getElementById(id);
  const APP_BUILD = "UK20-test";
  const state = {
    settings: null,
    token: "",
    connected: false,
    weather: null,
    history: [],
    historyError: null,
    chart: "temp",
    share: false,
    transportError: null,
    refreshing: false,
    refreshPromise: null,
    astro: null,
    radarLoading: false,
    radarRequestedStation: null,
    radarLastAttempt: 0,
    theme: "night",
    forecast: null,
    beacon: null,
    climate: null,
    climatePromise: null,
    credentialsSaved: false,
    credentialStorage: "",
    credentialNotice: null,
    selectedStation: "",
    serverMismatch: false,
    historyLoaded: false,
    rainRecords: null,
    annual: null,
  };
  const climateName = () => state.settings?.climateStation?.label || "Watnall";
  const climateId = () => state.settings?.climateStation?.id || "UKE00105906";
  const climateZone = () =>
    state.settings?.climateStation?.timezone || state.settings?.timezone;
  const climateSource = () =>
    state.settings?.countryCode === "IE"
      ? "Met Éireann daily archive (00:00–24:00 UTC)"
      : "NOAA NCEI GHCN-Daily";
  const climateFullName = () =>
    state.settings?.climateStation?.name || "Nottingham: Watnall";
  const isPublicReading = () =>
    ["public", "cwop", "wunderground", "noaa", "metar", "met-eireann"].includes(
      state.weather?.source,
    );
  const isCWOP = () => state.weather?.source === "cwop";
  const observationSource = () =>
    state.weather?.source === "wunderground" ? "Weather Underground personal station" :
    state.weather?.source === "met-eireann"
      ? "Met Éireann measured observations"
      : isCWOP()
        ? "CWOP via NOAA MADIS"
        : isPublicReading()
          ? "airport METAR via NOAA AWC"
          : "personal Ambient Weather station";
  const pressureLabel = (c) =>
    isPublicReading()
      ? c.pressureKind === "station"
        ? "STATION PRESSURE"
        : c.pressureKind === "QNH"
          ? "ALTIMETER / QNH"
          : "SEA-LEVEL PRESSURE"
      : "RELATIVE PRESSURE";
  function publicCaption(w) {
    if (w?.source === "met-eireann") {
      const reports = WeatherReadings.reportedRain(w.current)
        .slice(0, 1)
        .map(({ period, mm }) => ` Rain (${period}): ${fmt(mm)} mm.`)
        .join("");
      return `${w.stationName ? `Met Éireann ${w.stationId} · ${w.stationName} · ${fmt(w.distanceKm)} km away. ` : "Finding a Met Éireann rain-reporting station. "}${w.publicSourceNote || ""}${reports}`;
    }
    const station = w.stationId
      ? `${w.source === "wunderground" ? "Weather Underground" : isCWOP() ? "CWOP" : w.source === "met-eireann" ? "Met Éireann" : w.source === "metar" ? "METAR" : "NOAA"} ${w.stationId} · ${w.stationName}${has(w.distanceKm) ? ` · ${fmt(w.distanceKm)} km away` : ""}. `
      : "";
    const cadence = w.source === "wunderground" ? "Weather Underground reports are checked every 10 minutes. " : isCWOP()
      ? "NOAA MADIS reports are checked every 10 minutes. "
      : "";
    const rain = WeatherReadings.rainLedgerNote(w, state.settings?.countryCode);
    const rolling = WeatherReadings.reportedRain(w.stationRain || w.current)
      .slice(0, 2)
      .map(
        ({ period, mm }) =>
          `Station rain (${period}): ${fmt(mm)} mm (${fmt(mm / 25.4, 2)} in). `,
      )
      .join("");
    return (
      station +
      cadence +
      rain +
      rolling +
      (w.publicSourceNote ||
        "The calendar uses the named daily climate record.")
    );
  }
  const has = (v) => typeof v === "number" && Number.isFinite(v);
  const fmt = (v, d = 1) => (has(v) ? v.toFixed(d) : "—");
  const celsius = (f) => (has(f) ? ((f - 32) * 5) / 9 : null);
  const inch = (mm) => (has(mm) ? mm / 25.4 : null);
  const currentReading = () =>
    WeatherReadings.withYearRain(
      state.weather?.current,
      state.history,
      state.settings?.timezone,
      state.annual,
    );
  const windMean = () => WeatherReadings.windMean10m(currentReading(), [
    ...(isPublicReading() ? state.weather?.points || state.weather?.history || [] : state.historyStationKey === state.selectedStation ? state.history : []),
    ...(state.weather?.windHistory || []),
  ]);
  const beaufortText = (mph) => {
    const b = WeatherReadings.beaufort(mph);
    return b ? `Beaufort ${b.force} · ${b.label}` : "Beaufort unavailable";
  };
  const put = (id, value) => {
    $(id).textContent = value;
  };
  const windRose = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
  ];
  const direction = (d) => (has(d) ? windRose[Math.round(d / 22.5) % 16] : "—");
  const localDate = (t, options = {}) =>
    new Intl.DateTimeFormat("en-GB", {
      timeZone: state.settings?.timezone || "Europe/London",
      ...options,
    }).format(new Date(t));
  const clock = (t) =>
    has(t)
      ? localDate(t, { hour: "numeric", minute: "2-digit" })
      : "No event today";
  const shortDate = (t) => localDate(t, { month: "short", day: "numeric" });
  const fullStamp = (t) =>
    localDate(t, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
      timeZoneName: "short",
    });
  const textUnits = (value, unit, d = 1) =>
    has(value) ? `${fmt(value, d)} ${unit}` : "Unavailable";
  const signed = (v) => (has(v) ? `${v >= 0 ? "+" : ""}${fmt(v)}` : "—");
  function toast(message) {
    put("toast", message);
    $("toast").hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => ($("toast").hidden = true), 4200);
  }
  async function api(path, body) {
    const controller = new AbortController(),
      timeout = setTimeout(
        () => controller.abort(),
        ["/api/location/resolve", "/api/settings"].includes(path)
          ? 90000
          : 35000,
      );
    try {
      const response = await fetch(path, {
        method: body === undefined ? "GET" : "POST",
        headers:
          body === undefined
            ? {}
            : {
                "Content-Type": "application/json",
                "X-Weather-Token": state.token,
              },
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(result.error || "The request could not be completed.");
      return result;
    } catch (error) {
      if (error.name === "AbortError")
        throw new Error("The weather service took too long. Please try again.");
      if (error instanceof TypeError)
        throw new Error(
          "Cannot reach the local dashboard. Keep its server window open.",
        );
      throw error;
    } finally {
      clearTimeout(timeout);
    }
  }
  const radarPanel = WeatherUKRadar.create({
    api,
    getSettings: () => state.settings,
  });
  const monthlyPanel = WeatherUKMonthly.create({
    api,
    getSettings: () => state.settings,
  });
  const tidePanel = WeatherUKTides.create({
    api,
    getSettings: () => state.settings,
  });
  const ukData = WeatherUKData.create({
    api,
    getSettings: () => state.settings,
    getWeather: () => state.weather,
    onRefresh: () => refresh(),
  });
  const outlookPanel = globalThis.WeatherOutlook?.create({getSettings: () => state.settings});
  const tokyoPanel = globalThis.WeatherTokyo?.create({api});
  const lightningPanel = globalThis.WeatherLightning?.create({
    region: "UK",
    api,
    getSettings: () => state.settings,
    onSettings: showSettings,
  });
  const alertPanel = globalThis.WeatherAlerts?.create({
    fetchAlerts: () => api("/api/alerts"),
    getSettings: () => state.settings,
    formatDate: fullStamp,
  });
  const pastPanel = globalThis.WeatherPast?.create({
    api,
    getSettings: () => state.settings,
    getStation: () =>
      state.settings?.weatherSource === "auto" && state.connected
        ? state.selectedStation
        : "",
    isVisible: () => document.body.classList.contains("past-mode"),
    onLive: () => setShare(false),
    onSettings: showSettings,
  });
  const radioPanel = globalThis.WeatherRadio?.create({
    api,
    getSettings: () => state.settings,
    isVisible: () => document.body.classList.contains("radio-mode"),
  });
  const locationPanel = globalThis.WeatherLocation?.create({
    api,
    getSettings: () => state.settings,
  });
  const calendarPanel = globalThis.WeatherCalendar?.create({
    api,
    getSettings: () => state.settings,
    getSourceKey: () =>
      `${state.settings?.weatherSource}|${state.connected ? state.selectedStation : ""}`,
    isVisible: () =>
      !["share-mode", "charts-mode", "past-mode", "radio-mode"].some((mode) =>
        document.body.classList.contains(mode),
      ),
  });
  const publicStations = globalThis.WeatherPublicStations?.create({
    api, onChange: () => { state.history = []; return refresh(); },
  });
  let publicPollTimer;
  function acceptStatus(result) {
    state.settings = result.settings;
    publicStations?.sync(result.publicStations);
    lightningPanel?.sync();
    state.connected = result.connected;
    state.token = result.csrfToken;
    state.serverMismatch = result.build !== APP_BUILD;
    state.credentialsSaved = !!result.credentialsSaved;
    state.credentialStorage = result.credentialStorage;
    state.credentialNotice = result.credentialNotice;
    state.selectedStation = result.selectedStation || "";
    pastPanel?.sync();
    radioPanel?.sync();
  }
  function connectionSummary() {
    return (
      state.credentialNotice ||
      (state.connected
        ? state.credentialsSaved
          ? "Station saved on this computer. It will reconnect when the dashboard starts."
          : "Station connected for this session. Check Remember and click Connect station to save it."
        : "No personal station is connected. Automatic mode searches nearby CWOP and configured Weather Underground stations, then official observations; choose your location above.")
    );
  }
  function setError(message) {
    if (state.serverMismatch)
      message =
        "Finish this update by closing the weather server window and starting START-WEATHER-DESK-UK.bat again, then reload this page.";
    $("errorNotice").hidden = !message;
    if (message) put("errorNotice", message);
  }
  function isStale() {
    const w = state.weather,
      c = w?.current;
    return !!(
      state.transportError ||
      w?.stale ||
      !has(c?.timestamp) ||
      Date.now() / 1000 - c.timestamp > (w?.staleAfterSeconds || 900) ||
      c.timestamp > Date.now() / 1000 + 300
    );
  }
  function mode() {
    return state.weather?.demo
      ? "SAMPLE DATA"
      : isStale()
        ? isPublicReading()
          ? WeatherReadings.publicLabels(state.weather.source).delayed
          : "STALE READING"
        : isPublicReading()
          ? WeatherReadings.publicLabels(state.weather.source).observation
          : "STATION OBSERVATION";
  }
  function trendText() {
    const t = state.weather?.trend;
    if (!t || !has(t.delta))
      return t?.state === "unavailable"
        ? "Trend unavailable"
        : state.weather?.pressureHistoryLoading ? "Retrieving 3-hour history…" : "Building 3-hour history";
    const arrow =
      t.state === "rising" ? "↑" : t.state === "falling" ? "↓" : "→";
    return `${arrow} ${t.state.charAt(0).toUpperCase() + t.state.slice(1)} · ${t.approximate ? "≈" : ""}${signed(t.delta)} hPa / ${fmt(t.hours)}h`;
  }
  function phaseName(p) {
    if (p < 0.03 || p > 0.97) return "New moon";
    if (p < 0.22) return "Waxing crescent";
    if (p < 0.28) return "First quarter";
    if (p < 0.47) return "Waxing gibbous";
    if (p < 0.53) return "Full moon";
    if (p < 0.72) return "Waning gibbous";
    if (p < 0.78) return "Last quarter";
    return "Waning crescent";
  }
  function renderRainAge() {
    const value = state.weather?.rainSince;
    put("rainSince", WeatherReadings.rainSinceText(value));
    put("rainSinceSource", value?.source || "Waiting for rainfall reports");
    $("rainSinceRow").title = `${value?.source || "Rain observations"}. ${value?.lastRainAt ? "Last recorded " + (value.approximate ? "wet interval ended " : "rain at ") + fullStamp(value.lastRainAt * 1000) + ". " : ""}${value?.asOf ? "Coverage through " + fullStamp(value.asOf * 1000) + ". " : ""}Elapsed 24-hour days. ≥ means a minimum supported by continuous dry observations; earlier rain or missing history is unknown.`;
    $("rainSinceRow").setAttribute("aria-label", $("rainSinceRow").title);
  }
  function updateAstro() {
    if (!state.settings) return;
    try {
      const s = state.settings;
      state.astro = WeatherAstro.calculate(
        Date.now(),
        s.latitude,
        s.longitude,
        s.timezone,
      );
      const a = state.astro;
      put("twilightStage", a.twilight.stage + (a.twilight.phase ? " · " + a.twilight.phase : ""));
      $("twilightStage").title = `Sun centre ${fmt(a.twilight.sunAltitude)}°. Civil: sunset to −6°; nautical: −6° to −12°; astronomical: −12° to −18°. Calculated for your saved location at the current time.`;
      put("sunrise", clock(a.sun.rise));
      put("sunset", clock(a.sun.set));
      put("moonrise", clock(a.moon.rise));
      put("moonset", clock(a.moon.set));
      put("moonPhase", phaseName(a.illumination.phase));
      put(
        "moonIllumination",
        `${Math.round(a.illumination.fraction * 100)}% illuminated`,
      );
      if (has(a.sun.rise) && has(a.sun.set) && a.sun.set > a.sun.rise) {
        const minutes = Math.round((a.sun.set - a.sun.rise) / 60000);
        put("dayLength", `${Math.floor(minutes / 60)}h ${minutes % 60}m`);
        const progress = Math.max(
          0,
          Math.min(1, (Date.now() - a.sun.rise) / (a.sun.set - a.sun.rise)),
        );
        $("sunArcDot").setAttribute("cx", 25 + 250 * progress);
        $("sunArcDot").setAttribute("cy", 94 - 144 * progress * (1 - progress));
      } else {
        put(
          "dayLength",
          a.sun.alwaysUp
            ? "Sun stays up"
            : a.sun.alwaysDown
              ? "Sun stays down"
              : "Varies today",
        );
        $("sunArcDot").setAttribute("cx", 150);
        $("sunArcDot").setAttribute("cy", a.sun.alwaysUp ? 22 : 94);
      }
      const illum = a.illumination.fraction,
        wax = a.illumination.phase < 0.5,
        x = 50,
        r = 38,
        rx = Math.abs(1 - 2 * illum) * r;
      const side = wax ? 1 : 0;
      const start = `M ${x} ${50 - r} A ${r} ${r} 0 0 ${side} ${x} ${50 + r}`;
      const terminatorSweep = illum < 0.5 ? 1 - side : side;
      $("moonLit").setAttribute(
        "d",
        `${start} A ${rx || 0.001} ${r} 0 0 ${terminatorSweep} ${x} ${50 - r} Z`,
      );
      put(
        "astroNote",
        `Approximate sky times · ${s.timezone.replaceAll("_", " ")} · local date`,
      );
    } catch (error) {
      state.astro = null;
      put("astroNote", "Check your timezone and coordinates in Settings.");
    }
  }
  function observationIsNight() {
    const c = state.weather?.current,
      s = state.settings;
    return has(c?.timestamp) && s
      ? WeatherAstro.altitude(
          c.timestamp * 1000,
          s.latitude,
          s.longitude,
          WeatherAstro.sun,
        ) < -0.833
      : state.astro?.sunAltitude < -0.833;
  }
  function updateAppearance() {
    if (!state.settings) return;
    const altitude = state.astro?.sunAltitude;
    const rising =
      WeatherAstro.altitude(
        Date.now() + 60000,
        state.settings.latitude,
        state.settings.longitude,
        WeatherAstro.sun,
      ) > (altitude ?? 0);
    const mode = state.settings.skyTheme || "auto";
    const theme = WeatherDesign.chooseTheme(
      mode,
      altitude,
      rising,
      state.weather?.current?.solar,
      !isStale(),
    );
    state.theme = theme;
    const p = WeatherDesign.themes[theme],
      style = document.body.style;
    for (const [key, value] of Object.entries({
      "--sky-top": p.top,
      "--sky-bottom": p.bottom,
      "--sky-panel": p.panel,
      "--card-top": p.cardTop,
      "--card-bottom": p.cardBottom,
      "--mint": p.accent,
      "--sky-glow": p.glow,
    }))
      style.setProperty(key, value);
    document.body.dataset.sky = theme;
    $("skyThemeSelect").value = mode;
    put("skyStatus", `${mode === "auto" ? "Auto · " : ""}${p.label}`);
    document
      .querySelector('meta[name="theme-color"]')
      .setAttribute("content", p.bottom);
  }
  function renderBeacon() {
    const b = WeatherDesign.beacon(state.forecast, Date.now() / 1000);
    state.beacon = b;
    const panel = $("beaconPanel");
    panel.dataset.beacon = b.kind;
    panel.dataset.flash = String(b.flash);
    put("beaconSignal", b.signal);
    put("beaconTitle", b.title);
    put("beaconDetail", b.detail);
    const source = b.demo
      ? "SAMPLE FORECAST"
      : state.forecast?.source ||
        (state.settings?.countryCode === "IE"
          ? "Met Éireann"
          : "Open-Meteo / DWD ICON");
    put(
      "beaconSource",
      b.kind === "unknown"
        ? `${source} · indicator unlit`
        : `${source} · retrieved ${clock(state.forecast.fetchedAt * 1000)}`,
    );
    const link = $("beaconForecastLink");
    link.hidden = false;
    link.href =
      state.forecast?.sourceUrl ||
      (state.settings?.countryCode === "IE"
        ? "https://www.met.ie/forecasts"
        : "https://open-meteo.com/en/docs/dwd-api");
    link.textContent = "Forecast source ↗";
    link.title = `Hourly forecast from ${source}; separate from station observations.`;
    panel.setAttribute(
      "aria-label",
      `${b.demo ? "Sample forecast. " : ""}${b.signal}. ${b.title}. ${b.detail}`,
    );
  }
  async function loadForecast() {
    const identity = `${state.settings?.latitude}|${state.settings?.longitude}`;
    let forecast;
    try {
      forecast = await api("/api/forecast");
    } catch (error) {
      forecast = {error: "The forecast feed could not be reached. The beacon is unlit."};
    }
    if (identity !== `${state.settings?.latitude}|${state.settings?.longitude}`) return;
    state.forecast = forecast;
    renderBeacon();
    outlookPanel.render(state.forecast);
  }

  function condition() {
    const c = state.weather?.current;
    if (!c) return "Waiting for your station";
    const night = observationIsNight();
    const first = night ? "Night at the station" : "Day at the station";
    if (has(c.rainRateMmHr) && c.rainRateMmHr > 0)
      return `${first} · rain reported`;
    if (night) return first;
    if (has(c.solar))
      return `${first} · ${c.solar >= 400 ? "bright light" : c.solar >= 120 ? "moderate light" : "low light"}`;
    return first;
  }
  function displayWeather() {
    const place = state.settings;
    if (place) {
      document.title = place.title;
      put("deskTitle", place.title);
      put(
        "dataSourceCredit",
        `Observations: ${observationSource()} · Radar: RainViewer · ${place.countryCode === "IE" ? "Forecast & climate: Met Éireann" : "Forecast: Open-Meteo/DWD · Climate: NOAA NCEI / Met Office"} · Sky: calculated`,
      );
      put("locationLabel", place.location);
      put("callsignLabel", place.callsign);
      put(
        "dateLine",
        localDate(Date.now(), {
          weekday: "long",
          month: "long",
          day: "numeric",
        }).toUpperCase(),
      );
      updateAstro();
      updateAppearance();
    }
    const w = state.weather,
      c = currentReading();
    $("weatherSourceNote").hidden = !isPublicReading();
    if (isPublicReading()) put("weatherSourceNote", publicCaption(w));
    renderRainAge();
    if (!c) {
      for (const id of [
        "tempF",
        "tempC",
        "feelsF",
        "feelsC",
        "windSpeed",
        "windDirection",
        "windDegrees",
        "dewPoint",
        "dewPointC",
        "humidity",
        "pressure",
        "pressureTrend",
        "dailyRain",
        "dailyRainMm",
        "rainHour",
        "rainEvent",
        "rainEventMm",
        "rainWeek",
        "rainWeekMm",
        "rainMonth",
        "rainMonthMm",
        "rainYear",
        "rainYearMm",
        "dailyMaxGust",
        "dailyMaxGustKmh",
        "windGust",
        "windKmh",
        "solarValue",
        "uvValue",
      ])
        put(id, "—");
      $("windNeedle").style.opacity = "0";
      put("beaufortWind", "Waiting for wind");
      put("windMean10m", "10-min mean unavailable");
      put("conditionLabel", "Waiting for observations");
      put("cardDate", "");
      put(
        "cardCredit",
        [place?.location, place?.callsign]
          .filter(Boolean)
          .join(" · ")
          .toUpperCase(),
      );
      put("cardStamp", "Waiting for observations at this location");
      if (w) {
        setError(
          w.loading
            ? null
            : w.error || "Waiting for the first station observation.",
        );
        put(
          "statusBadge",
          w.loading ? "FINDING PUBLIC STATION" : "WAITING FOR STATION",
        );
        $("statusBadge").className = "status-badge stale";
        put("cardMode", "NO OBSERVATION");
        put(
          "updatedLabel",
          w.loading
            ? "Checking nearby public reports…"
            : "Waiting for a usable observation…",
        );
        $("notice").hidden = !!state.settings?.locationConfigured;
        put(
          "noticeText",
          "Choose your location in Settings. No station or API key is needed for public weather.",
        );
      }
      renderClimate();
      ukData.renderRain(w);
      return;
    }
    updateAstro();
    updateAppearance();
    renderBeacon();
    const s = state.settings,
      stamp = c.timestamp * 1000,
      demo = w.demo,
      stale = isStale();
    document.title = s.title;
    put("deskTitle", s.title);
    const dot = document.createElement("span");
    dot.className = "title-dot";
    dot.textContent = ".";
    $("deskTitle").append(dot);
    put("locationLabel", s.location);
    put("callsignLabel", s.callsign);
    put(
      "dateLine",
      localDate(Date.now(), {
        weekday: "long",
        month: "long",
        day: "numeric",
      }).toUpperCase(),
    );
    put(
      "statusBadge",
      demo
        ? "SAMPLE DATA"
        : stale
          ? isPublicReading()
            ? WeatherReadings.publicLabels(w.source).delayed
            : "STALE READING"
          : isPublicReading()
            ? WeatherReadings.publicLabels(w.source).badge
            : "STATION CONNECTED",
    );
    $("statusBadge").className =
      `status-badge ${demo ? "demo" : stale ? "stale" : ""}`;
    put("updatedLabel", `${demo ? "Sample time" : "Observed"} ${clock(stamp)}`);
    put("cardMode", mode());
    put("cardDate", `${shortDate(stamp)} · ${clock(stamp)}`);
    $("notice").hidden = !demo && !!s.locationConfigured;
    put(
      "noticeText",
      demo
        ? "These are sample readings. Choose Automatic or Official public station in Settings for real observations."
        : "Choose your location in Settings to make this weather desk your own.",
    );
    setError(
      state.transportError ||
        w.error ||
        (stale && !demo
          ? `The ${isPublicReading() ? "public observation is more than 90" : "station observation is more than 15"} minutes old. The card shows its original observation time.`
          : null),
    );
    put("tempF", fmt(celsius(c.tempF)));
    put("tempC", fmt(c.tempF));
    put("feelsF", fmt(celsius(c.feelsF)));
    put("feelsC", fmt(c.feelsF));
    $("feelsF").parentElement.title = `Feels like: ${c.feelsSource}`;
    put("conditionLabel", condition());
    put(
      "observationHeading",
      stale && !demo ? "LAST STATION OBSERVATION" : "OUTSIDE RIGHT NOW",
    );
    $("weatherCard").classList.toggle("night", observationIsNight());
    put(
      "solarPill",
      has(c.solar)
        ? `${fmt(c.solar, 0)} W/m² · SOLAR`
        : "SOLAR SENSOR UNAVAILABLE",
    );
    put("windSpeed", fmt(c.windMph));
    const mean = windMean();
    put("beaufortWind", has(mean.mph) ? beaufortText(mean.mph) : "Beaufort unavailable");
    put("windMean10m", has(mean.mph) ? `10-min ${mean.estimated ? "estimate ≈" : "mean "}${fmt(mean.mph)} mph` : state.weather?.windHistoryLoading ? "Retrieving 10-min history…" : "10-min mean unavailable");
    $("beaufortWind").title = (mean.source || mean.reason || "") + (mean.timestamp ? " · mean ending " + fullStamp(mean.timestamp * 1000) : "") + ". Scalar wind speed averaged over ten minutes; gusts are excluded. Sensor height has not been corrected to ten metres.";
    put(
      "windDirection",
      has(c.windMph) && c.windMph < 0.5 ? "CALM" : direction(c.windDeg),
    );
    put(
      "windDegrees",
      has(c.windDeg) && !(has(c.windMph) && c.windMph < 0.5)
        ? `${fmt(c.windDeg, 0)}°`
        : "",
    );
    $("windNeedle").style.transform =
      `rotate(${has(c.windDeg) ? c.windDeg : 0}deg)`;
    $("windNeedle").style.opacity =
      has(c.windDeg) && has(c.windMph) && c.windMph >= 0.5 ? "1" : "0";
    put("windGust", textUnits(c.gustMph, "mph"));
    put("dailyMaxGust", textUnits(c.maxGustMph, "mph"));
    put(
      "dailyMaxGustKmh",
      textUnits(has(c.maxGustMph) ? c.maxGustMph * 1.609344 : null, "km/h"),
    );
    put(
      "windKmh",
      textUnits(has(c.windMph) ? c.windMph * 1.609344 : null, "km/h"),
    );
    put("dewPoint", has(c.dewF) ? `${fmt(celsius(c.dewF))}°C` : "Unavailable");
    put("dewPointC", has(c.dewF) ? `${fmt(c.dewF)}°F` : "No sensor data");
    $("dewPoint").title = c.dewSource;
    put("humidity", has(c.humidity) ? `${fmt(c.humidity, 0)}%` : "Unavailable");
    put("pressureLabel", pressureLabel(c));
    put("pressure", textUnits(c.pressureHpa, "hPa"));
    put("pressureTrend", trendText());
    $("pressureTrend").title = state.weather.pressureHistoryNote || (state.weather.trend?.approximate
      ? "Approximate change from the earlier reporting interval's pressure range; uncertain means the range crosses a trend threshold."
      : "Compared with the same station and pressure type about three hours earlier.");
    put("dailyRain", textUnits(c.rainDayMm, "mm"));
    put("dailyRainMm", textUnits(inch(c.rainDayMm), "in", 2));
    for (const [name, key] of [
      ["Event", "rainEventMm"],
      ["Week", "rainWeekMm"],
      ["Month", "rainMonthMm"],
      ["Year", "rainYearMm"],
    ]) {
      put("rain" + name, textUnits(c[key], "mm"));
      $("rain" + name).dataset.missing = String(!has(c[key]));
      put("rain" + name + "Mm", textUnits(inch(c[key]), "in", 2));
    }
    renderYearRain(c);
    put("rainHour", textUnits(c.rainRateMmHr, "mm/h"));
    put(
      "shareEvent",
      `Event ${textUnits(inch(c.rainEventMm), "in", 2)} / ${textUnits(c.rainEventMm, "mm")}`,
    );
    put(
      "shareWeek",
      `Week ${textUnits(inch(c.rainWeekMm), "in", 2)} / ${textUnits(c.rainWeekMm, "mm")}`,
    );
    put(
      "shareMonth",
      `Month ${textUnits(inch(c.rainMonthMm), "in", 2)} / ${textUnits(c.rainMonthMm, "mm")}`,
    );
    put(
      "shareYear",
      `Year ${textUnits(inch(c.rainYearMm), "in", 2)} / ${textUnits(c.rainYearMm, "mm")}`,
    );
    put("solarValue", textUnits(c.solar, "W/m²", 0));
    put("uvValue", has(c.uv) ? fmt(c.uv) : "Unavailable");
    put(
      "cardCredit",
      [s.location, s.callsign].filter(Boolean).join(" · ").toUpperCase(),
    );
    put(
      "cardStamp",
      `${demo ? "Sample readings" : stale ? "Last observation" : isPublicReading() ? `${w.source === "wunderground" ? "Weather Underground" : w.source === "met-eireann" ? "Met Éireann" : isCWOP() ? "CWOP" : "METAR"} ${w.stationId || "station"}` : "Ambient station"} · ${fullStamp(stamp)}`,
    );
    $("radarLink").href =
      "https://weather.metoffice.gov.uk/maps-and-charts/rainfall-radar-forecast-map";
    put("radarStationLabel", "UK RAIN RADAR");
    renderChart();
    renderClimate();
    ukData.renderRain(state.weather);
  }
  function renderChart() {
    const target = $("historyChart"),
      key = state.chart === "temp" ? "tempF" : "pressureHpa",
      unit = state.chart === "temp" ? "°F" : "hPa";
    const end = Date.now() / 1000,
      start = end - 86400;
    const rows = state.history.filter(
      (p) =>
        has(p.timestamp) && p.timestamp >= start && p.timestamp <= end + 300,
    );
    const usable = rows.filter((p) => has(p[key]));
    $("chartTempBtn").classList.toggle("active", state.chart === "temp");
    $("chartPressureBtn").classList.toggle(
      "active",
      state.chart === "pressure",
    );
    $("chartTempBtn").setAttribute(
      "aria-pressed",
      String(state.chart === "temp"),
    );
    $("chartPressureBtn").setAttribute(
      "aria-pressed",
      String(state.chart === "pressure"),
    );
    if (!usable.length) {
      target.replaceChildren();
      const message = document.createElement("p");
      message.className = "muted";
      message.textContent =
        state.historyError ||
        "Waiting for historical observations. No history has been invented.";
      target.append(message);
      put("chartValue", "—");
      put("chartSummary", "Historical data unavailable");
      put(
        "historyNote",
        state.historyError ||
          "Connect your station to load the available history.",
      );
      return;
    }
    const values = usable.map((p) => p[key]),
      low = Math.min(...values),
      high = Math.max(...values),
      pad = Math.max(state.chart === "temp" ? 1 : 0.4, (high - low) * 0.2),
      min = low - pad,
      max = high + pad;
    const W = 600,
      H = 150,
      left = 43,
      right = 10,
      top = 8,
      bottom = 25;
    const px = (t) => left + ((t - start) / 86400) * (W - left - right),
      py = (v) => top + ((max - v) / (max - min)) * (H - top - bottom);
    let segments = [],
      current = [],
      previous = null;
    for (const p of rows) {
      if (!has(p[key])) {
        if (current.length) segments.push(current);
        current = [];
        previous = null;
        continue;
      }
      if (
        previous &&
        p.timestamp - previous.timestamp > (isPublicReading() ? 5400 : 2700)
      ) {
        if (current.length) segments.push(current);
        current = [];
      }
      current.push([px(p.timestamp), py(p[key])]);
      previous = p;
    }
    if (current.length) segments.push(current);
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><defs><linearGradient id="chartFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8bdfd0" stop-opacity=".17"/><stop offset="1" stop-color="#8bdfd0" stop-opacity="0"/></linearGradient></defs>`;
    for (let i = 0; i < 3; i++) {
      const value = min + ((max - min) * i) / 2,
        y = py(value);
      svg += `<path d="M${left} ${y}H${W - right}" stroke="#304452" stroke-width=".8" stroke-dasharray="3 5"/><text x="${left - 7}" y="${y + 3}" text-anchor="end" font-size="9" fill="#8fa7b6">${value.toFixed(state.chart === "temp" ? 0 : 1)}</text>`;
    }
    for (let i = 0; i <= 4; i++) {
      const t = start + i * 21600;
      svg += `<text x="${px(t)}" y="${H - 3}" text-anchor="${i === 0 ? "start" : i === 4 ? "end" : "middle"}" font-size="9" fill="#8fa7b6">${localDate(t * 1000, { hour: "numeric" })}</text>`;
    }
    for (const points of segments) {
      if (points.length === 1) {
        svg += `<circle cx="${points[0][0]}" cy="${points[0][1]}" r="2.5" fill="#8bdfd0"/>`;
        continue;
      }
      const path = points
        .map((p, i) => `${i ? "L" : "M"}${p[0].toFixed(2)} ${p[1].toFixed(2)}`)
        .join(" ");
      svg += `<path d="${path} L${points.at(-1)[0]} ${H - bottom} L${points[0][0]} ${H - bottom}Z" fill="url(#chartFill)"/><path d="${path}" fill="none" stroke="#8bdfd0" stroke-width="2" stroke-linejoin="round"/>`;
    }
    svg += "</svg>";
    target.innerHTML = svg;
    target.setAttribute(
      "aria-label",
      `${state.chart === "temp" ? "Temperature" : pressureLabel(currentReading() || {})} over available observations: minimum ${fmt(low)} ${unit}, maximum ${fmt(high)} ${unit}.`,
    );
    put("chartValue", `${fmt(state.weather?.current?.[key])} ${unit}`);
    put(
      "chartSummary",
      `Sampled low ${fmt(low)}°${state.chart === "temp" ? "F" : ""} · high ${fmt(high)}${state.chart === "temp" ? "°F" : " hPa"}`,
    );
    if (state.chart !== "temp")
      put("chartSummary", `Sampled low ${fmt(low)} · high ${fmt(high)} hPa`);
    const hours = (usable.at(-1).timestamp - usable[0].timestamp) / 3600;
    put(
      "historyNote",
      `${state.weather?.demo ? "Sample readings" : `${usable.length} observations`} · ${fmt(hours)} hours available · gaps over 45 minutes left blank${state.historyError ? " · History refresh unavailable" : ""}`,
    );
  }
  function calendarLabel(value, options = { month: "short", day: "numeric" }) {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return "awaiting data";
    return new Intl.DateTimeFormat("en-GB", {
      timeZone: "UTC",
      ...options,
    }).format(new Date(value + "T12:00:00Z"));
  }
  const climateTemp = (v) => (has(v) ? `${fmt(celsius(v))}°C` : "Unavailable");
  const climateTempC = (v) => (has(v) ? `${fmt(v)}°F` : "Unavailable");
  const shortYears = (record) =>
    record
      ? record.years.slice(0, 3).join(", ") +
        (record.years.length > 3 ? ` +${record.years.length - 3}` : "")
      : "—";
  const climateRefs = () =>
    WeatherClimate.reference(
      state.climate,
      currentReading(),
      Date.now() / 1000,
      climateId(),
    );
  function yearRainExplanation(c) {
    if (isPublicReading())
      return (
        state.weather.rain?.note ||
        "Rainfall uses a separate named Environment Agency gauge where available."
      );
    const a = state.annual;
    if (c.rainYearSource === "calculated")
      return `January 1–${calendarLabel(a.through)}: sum of ${a.expectedMonths - 1} completed monthly station counters plus the live month-to-date counter. Today's rain is already included. Click Year to date for the monthly breakdown. Completed months are saved for the next startup.`;
    if (c.rainYearSource === "history")
      return `Ambient annual counter from recent history, observed ${fullStamp(c.rainYearTimestamp * 1000)}. Its default period starts January 1.`;
    if (has(c.rainYearMm))
      return "Ambient’s reported Year counter; normally January 1 through today.";
    if (a?.status === "building")
      return `Adding monthly station totals from January 1: ${a.loadedMonths} / ${a.expectedMonths} months available. Only missing month-end windows are requested. Completed months remain saved after a restart.`;
    if (a?.error) return a.error;
    return "Ambient did not supply a usable annual counter. Adding completed monthly station totals plus the current month to date.";
  }
  function renderYearRain(c) {
    const available = has(c.rainYearMm),
      a = state.annual;
    if (isPublicReading()) {
      $("yearRainPeriod").title = yearRainExplanation(c);
      return;
    }
    $("rainYear").classList.toggle("missing-reading", !available);
    if (!available) {
      put(
        "rainYear",
        a?.status === "building"
          ? "Adding months…"
          : a?.status === "paused"
            ? "Waiting for months"
            : a?.status === "incomplete"
              ? "Check timezone"
              : "Not reported",
      );
      put(
        "rainYearMm",
        a?.expectedMonths
          ? `${a.loadedMonths} / ${a.expectedMonths} monthly totals`
          : "Checking monthly totals…",
      );
    } else if (c.rainYearSource === "calculated")
      put(
        "rainYearMm",
        `${textUnits(inch(c.rainYearMm), "in", 2)} · month sum`,
      );
    else if (c.rainYearSource === "history")
      put(
        "rainYearMm",
        `${textUnits(inch(c.rainYearMm), "in", 2)} · as of ${clock(c.rainYearTimestamp * 1000)}`,
      );
    $("yearRainPeriod").title = yearRainExplanation(c);
    if ($("rainBreakdownDialog").open) renderRainBreakdown();
  }
  function renderRainBreakdown() {
    const c = currentReading(),
      a = state.annual,
      body = $("rainBreakdownRows");
    body.replaceChildren();
    put(
      "rainBreakdownNote",
      c?.rainYearSource === "calculated"
        ? `January 1–${calendarLabel(a.through)}, ${a.year} · ${a.expectedMonths - 1} completed monthly totals plus the current month to date.`
        : c
          ? yearRainExplanation(c)
          : "Waiting for a station observation.",
    );
    const hasNormals = Array.isArray(state.climate?.normal?.rainMonths);
    put(
      "rainBreakdownClimate",
      hasNormals
        ? `${state.climate.stale ? "Cached · " : ""}${climateName()} ${state.climate.normalPeriod} normals. Completed months use their full-month average; the current month uses the normal through ${calendarLabel(a?.through || state.climate.date)}.`
        : state.climate
          ? "Local monthly normals are unavailable; they will retry automatically."
          : "Loading local monthly normals…",
    );
    $("rainBreakdownTable").hidden = !Array.isArray(a?.months);
    for (const m of a?.months || []) {
      const row = document.createElement("tr"),
        ref = WeatherClimate.monthlyReference(
          state.climate,
          a,
          m,
          c,
          Date.now() / 1000,
          climateId(),
        );
      const label = new Intl.DateTimeFormat("en-GB", {
        month: "long",
        timeZone: "UTC",
      }).format(new Date(Date.UTC(a.year, m.month - 1, 1)));
      row.title = `${m.method}${m.timestamp ? " · " + fullStamp(m.timestamp * 1000) : ""}`;
      const monthCell = document.createElement("th");
      monthCell.scope = "row";
      monthCell.textContent =
        label + (m.method === "live month to date" ? " · to date" : "");
      row.append(monthCell);
      for (const text of [
        has(m.inches) ? `${fmt(m.inches, 2)} in` : "Waiting…",
        has(ref.normalIn) ? `${fmt(ref.normalIn, 2)} in` : "Unavailable",
      ]) {
        const cell = document.createElement("td");
        cell.textContent = text;
        row.append(cell);
      }
      const comparison = document.createElement("td");
      comparison.className = "monthly-rain-comparison";
      comparison.dataset.comparison = ref.comparison.kind;
      comparison.textContent = comparisonText(ref.comparison);
      comparison.title = ref.through
        ? `Your station compared with ${climateName()} ${state.climate.normalPeriod} normal for ${calendarLabel(`${a.year}-${String(m.month).padStart(2, "0")}-01`)}–${calendarLabel(ref.through)}. Equal displayed 0.01-inch amounts are at normal.`
        : "A matching normal or usable station total is unavailable.";
      row.append(comparison);
      const stamp = document.createElement("td");
      stamp.className = "rain-breakdown-stamp";
      stamp.textContent = m.timestamp
        ? fullStamp(m.timestamp * 1000)
        : "No month-end reading";
      row.append(stamp);
      body.append(row);
    }
    put(
      "rainBreakdownTotal",
      c && has(c.rainYearMm)
        ? `Year to date: ${textUnits(inch(c.rainYearMm), "in", 2)} / ${textUnits(c.rainYearMm, "mm")}`
        : "Year to date: waiting for the missing monthly total(s).",
    );
  }
  let annualPollTimer;
  async function loadAnnual() {
    clearTimeout(annualPollTimer);
    try {
      const result = await api("/api/annual");
      if (!result.station || result.station === state.selectedStation)
        state.annual = result;
    } catch (error) {
      state.annual = {
        complete: false,
        status: "paused",
        error:
          "Station history could not refresh. It will retry automatically.",
      };
    }
    if (state.annual?.status === "building")
      annualPollTimer = setTimeout(loadAnnual, 3000);
    if (state.weather?.current) displayWeather();
  }
  function comparisonText(comparison) {
    return comparison.kind === "unknown"
      ? "— Comparison unavailable"
      : comparison.kind === "at"
        ? "→ At normal"
        : `${comparison.arrow} ${comparison.label} · ${fmt(Math.abs(comparison.difference) * 25.4)} mm`;
  }
  function renderClimate() {
    const r = climateRefs(),
      c = state.climate;
    if ($("climateOfficialLink"))
      $("climateOfficialLink").href =
        state.settings?.climateStation?.sourceUrl || "https://www.weather.gov/";
    put(
      "tempRecordLabel",
      `${climateName().toUpperCase()} ARCHIVE RECORDS · ${calendarLabel(WeatherClimate.bostonDay(Date.now() / 1000, climateZone())).toUpperCase()}${r.cached && r.date ? " · CACHED" : ""}`,
    );
    for (const [name, value] of [
      ["High", r.high],
      ["Low", r.low],
    ]) {
      put(
        "record" + name,
        value
          ? `${climateTemp(value.valueF)} / ${climateTempC(value.valueF)}`
          : c
            ? "Unavailable"
            : "Loading…",
      );
      put("record" + name + "Year", value ? `in ${shortYears(value)}` : "");
      $("record" + name + "Year").title = value ? value.years.join(", ") : "";
    }
    $("temperatureRecords").title =
      `${climateFullName()} records for this calendar date across this station’s available period of record. Source ${climateSource()}. Your observation station is a separate site.${c?.error ? " Climate refresh unavailable; saved same-date references are labeled cached." : ""}`;
    put(
      "dailyRainRecord",
      r.rain
        ? `${climateName()} record ${fmt(r.rain.valueIn * 25.4)} mm · ${shortYears(r.rain)}${r.cached ? " (cached)" : ""}`
        : c
          ? "Local record unavailable"
          : "Local record loading…",
    );
    $("dailyRainRecord").title = r.rain
      ? `Highest daily ${climateName()} precipitation for ${calendarLabel(r.date)}: ${fmt(r.rain.valueIn, 2)} in / ${fmt(r.rain.valueIn * 25.4)} mm, recorded in ${r.rain.years.join(", ")}. Includes rain and melted snow.`
      : "Local daily precipitation record unavailable; retries automatically.";
    for (const name of ["month", "year"]) {
      put(
        name + "Normal",
        has(r[name + "Normal"])
          ? textUnits(r[name + "Normal"] * 25.4, "mm")
          : "Unavailable",
      );
      put(name + "Comparison", comparisonText(r[name]));
      $(name + "Comparison").dataset.comparison = r[name].kind;
      $(name + "Comparison").title =
        `Your station’s total compared with ${climateName()} calculated 1991–2020 average through today, including the full-day expectation. At normal means equal at 0.1-mm display precision. A stale observation, missing counter or mismatched date leaves the comparison unavailable.`;
    }
    renderRainRecords();
    put(
      "rainReferenceNote",
      r.date && has(r.monthNormal) && has(r.yearNormal)
        ? `${r.cached ? "CACHED · " : ""}${climateName().toUpperCase()} CALC. AVG · TO ${calendarLabel(r.date).toUpperCase()}`
        : c
          ? "LOCAL NORMALS UNAVAILABLE"
          : "LOCAL NORMALS LOADING…",
    );
    $("rainReferenceNote").title =
      `Your station totals versus ${climateName()} climate normals. Normals accumulate from the first day of the month/year through the end of today. A partial day can change the arrow as precipitation falls. Station counter reset periods apply.`;
    if ($("rainBreakdownDialog").open) renderRainBreakdown();
  }
  const rainRecordRefs = () =>
    WeatherClimate.rainRecords(
      state.rainRecords,
      Date.now() / 1000,
      climateId(),
      climateZone(),
    );
  const rainRecordText = (value) =>
    value
      ? `${value.trace ? "Trace" : fmt(value.valueIn * 25.4) + " mm"} · ${shortYears(value)}`
      : state.rainRecords
        ? "Unavailable"
        : "Loading…";
  function recordWindow(period, r) {
    if (!r.date) return period === "month" ? "MONTH TO DATE" : "YEAR TO DATE";
    return period === "month"
      ? `${calendarLabel(r.date, { month: "short" })} 1–${Number(r.date.slice(-2))}`
      : `Jan 1–${calendarLabel(r.date)}`;
  }
  function renderRainRecords() {
    const r = rainRecordRefs();
    for (const period of ["month", "year"]) {
      put(
        period + "RecordCaption",
        `${climateName().toUpperCase()} · ${recordWindow(period, r).toUpperCase()}${r.cached && r.throughYear ? " · CACHED" : ""}`,
      );
      for (const extreme of ["Wettest", "Driest"]) {
        const value = r[period + extreme];
        put(period + extreme, rainRecordText(value));
        $(period + extreme).title = value
          ? `${value.trace ? "Trace precipitation" : fmt(value.valueIn, 2) + " in / " + fmt(value.valueIn * 25.4) + " mm"} · ${value.years.join(", ")}`
          : "Local historical record unavailable; retries automatically.";
      }
      const description = `${recordWindow(period, r)} in each historical year`;
      $(period + "RainRecords").title =
        `${climateFullName()} ${description}, ${r.startYear || "start of record"}–${r.throughYear || "last completed year"}. Source ${climateSource()}. The maximum, minimum and normal cover the same calendar-date labels. Observation-day conventions can differ between sources. Historical windows with missing daily data are excluded.`;
    }
  }
  async function loadRainRecords() {
    try {
      state.rainRecords = await api("/api/rain-records");
    } catch (error) {
      state.rainRecords = state.rainRecords
        ? {
            ...state.rainRecords,
            stale: true,
            error: "Local rain records could not refresh.",
          }
        : { error: "Local rain records could not load." };
    }
    renderRainRecords();
  }
  function loadClimate() {
    if (state.climatePromise) return state.climatePromise;
    state.climatePromise = (async () => {
      try {
        state.climate = await api("/api/climate");
      } catch (error) {
        state.climate = state.climate
          ? {
              ...state.climate,
              stale: true,
              error: "Local climate could not refresh.",
            }
          : {
              date: WeatherClimate.bostonDay(Date.now() / 1000, climateZone()),
              error:
                "Local climate could not load. It will retry automatically.",
              stale: true,
            };
      }
      renderClimate();
    })().finally(() => {
      state.climatePromise = null;
    });
    return state.climatePromise;
  }
  async function loadHistory() {
    const stationKey = state.selectedStation;
    try {
      const result = await api("/api/history");
      if (stationKey !== state.selectedStation) return;
      state.historyStationKey = stationKey;
      state.history = result.points || [];
      state.historyError = result.error || null;
      if (result.trend && state.weather) state.weather.trend = result.trend;
    } catch (error) {
      state.historyError = error.message;
    } finally {
      state.historyLoaded = true;
      if (state.weather?.current) displayWeather();
      else renderChart();
    }
  }
  async function loadTides() {
    return Promise.allSettled([tidePanel.refresh(), monthlyPanel.refresh()]);
  }
  function refresh() {
    alertPanel?.refresh();
    if (state.refreshPromise) return state.refreshPromise;
    state.refreshPromise = refreshOnce().finally(() => {
      state.refreshPromise = null;
    });
    return state.refreshPromise;
  }
  async function refreshOnce() {
    state.refreshing = true;
    $("refreshBtn").disabled = true;
    put("refreshBtn", "↻ Updating…");
    try {
      state.weather = await api("/api/weather");
      state.transportError = null;
      displayWeather();
      schedulePublicPoll();
      calendarPanel?.refresh();
      await Promise.allSettled([
        lightningPanel?.refresh(),
        loadHistory(),
        loadTides(),
        loadForecast(),
        loadClimate(),
        loadRainRecords(),
        loadAnnual(),
      ]);
    } catch (error) {
      state.transportError = error.message;
      setError(error.message);
      if (state.weather) displayWeather();
      else {
        put("updatedLabel", "Waiting for the local server");
        put("statusBadge", "CONNECTION UNAVAILABLE");
        $("statusBadge").className = "status-badge stale";
      }
    } finally {
      state.refreshing = false;
      $("refreshBtn").disabled = false;
      put("refreshBtn", "↻ Refresh");
    }
  }
  function schedulePublicPoll() {
    clearTimeout(publicPollTimer);
    const key = `${state.settings?.latitude}|${state.settings?.longitude}|${state.settings?.weatherSource}|${state.selectedStation}`;
    if (
      isPublicReading() &&
      (state.weather.loading ||
        state.weather.publicSearchLoading ||
        state.weather.rain?.loading)
    )
      publicPollTimer = setTimeout(async () => {
        try {
          const result = await api("/api/weather");
          if (
            key !==
            `${state.settings?.latitude}|${state.settings?.longitude}|${state.settings?.weatherSource}|${state.selectedStation}`
          )
            return;
          state.weather = result;
          displayWeather();
          if (!state.weather.loading) await loadHistory();
        } catch (error) {
          setError(error.message);
        }
        schedulePublicPoll();
      }, 2500);
  }
  function showSettings() {
    publicStations?.refresh();
    const f = $("settingsForm");
    for (const [key, value] of Object.entries(state.settings || {}))
      if (f.elements[key]) f.elements[key].value = value;
    put("connectStatus", connectionSummary());
    $("rememberStation").checked = state.connected
      ? state.credentialsSaved
      : true;
    $("macAddress").value = state.selectedStation;
    put(
      "credentialHint",
      state.credentialStorage === "windows-dpapi"
        ? "Windows encrypts saved keys for your Windows account in the dashboard’s private folder."
        : "Saved keys stay in a local file with owner-only permissions. Keep the dashboard’s private folder to yourself.",
    );
    put("settingsStatus", "");
    $("stationPicker").hidden = true;
    locationPanel?.reset();
    $("settingsDialog").showModal();
  }
  function setShare(on) {
    state.share = on;
    pastPanel?.close();
    radioPanel?.close();
    document.body.classList.remove("charts-mode", "past-mode", "radio-mode");
    document.body.classList.toggle("share-mode", on);
    for (const [id, active] of [
      ["overviewBtn", !on],
      ["shareBtn", on],
      ["chartsBtn", false],
      ["pastDaysBtn", false],
      ["radioBtn", false],
    ]) {
      $(id).classList.toggle("active", active);
      $(id).setAttribute("aria-pressed", String(active));
    }
    $("shareHelp").hidden = !on;
    if (on) calendarPanel?.pause();
    else calendarPanel?.refresh();
  }
  function showCharts() {
    setShare(false);
    document.body.classList.add("charts-mode");
    $("overviewBtn").classList.remove("active");
    $("overviewBtn").setAttribute("aria-pressed", "false");
    $("chartsBtn").classList.add("active");
    $("chartsBtn").setAttribute("aria-pressed", "true");
    calendarPanel?.pause();
    renderChart();
  }
  function showPast() {
    if (!pastPanel || !state.settings) return;
    setShare(false);
    document.body.classList.add("past-mode");
    $("overviewBtn").classList.remove("active");
    $("overviewBtn").setAttribute("aria-pressed", "false");
    $("pastDaysBtn").classList.add("active");
    $("pastDaysBtn").setAttribute("aria-pressed", "true");
    calendarPanel?.pause();
    pastPanel.open();
  }
  function showRadio() {
    if (!radioPanel || !state.settings) return;
    setShare(false);
    document.body.classList.add("radio-mode");
    $("overviewBtn").classList.remove("active");
    $("overviewBtn").setAttribute("aria-pressed", "false");
    $("radioBtn").classList.add("active");
    $("radioBtn").setAttribute("aria-pressed", "true");
    calendarPanel?.pause();
    radioPanel.open();
  }
  function weatherText() {
    const w = state.weather,
      c = currentReading(),
      s = state.settings,
      r = climateRefs();
    const rain = (key) =>
      `${textUnits(inch(c[key]), "in", 2)} / ${textUnits(c[key], "mm")}`;
    const lines = [
      `${s.title} · ${s.location}${s.callsign ? " · " + s.callsign : ""}`,
      `${mode()} · ${fullStamp(c.timestamp * 1000)}`,
      `Temperature: ${fmt(c.tempF)}°F / ${fmt(celsius(c.tempF))}°C`,
      `Feels like: ${fmt(c.feelsF)}°F / ${fmt(celsius(c.feelsF))}°C`,
    ];
    if (r.high && r.low)
      lines.push(
        `${climateName()} records for ${calendarLabel(r.date)}: high ${climateTemp(r.high.valueF)} / ${climateTempC(r.high.valueF)} (${shortYears(r.high)}); low ${climateTemp(r.low.valueF)} / ${climateTempC(r.low.valueF)} (${shortYears(r.low)})${r.cached ? " · cached" : ""}`,
      );
    lines.push(
      `Dew point: ${fmt(c.dewF)}°F / ${fmt(celsius(c.dewF))}°C · Humidity: ${fmt(c.humidity, 0)}%`,
      `Wind: ${textUnits(c.windMph, "mph")} / ${textUnits(has(c.windMph) ? c.windMph * 1.609344 : null, "km/h")} from ${direction(c.windDeg)}${has(c.windDeg) ? " (" + fmt(c.windDeg, 0) + "°)" : ""}; ${beaufortText(windMean().mph)}; gusts ${textUnits(c.gustMph, "mph")}`,
      `Daily max wind gust: ${textUnits(c.maxGustMph, "mph")} / ${textUnits(has(c.maxGustMph) ? c.maxGustMph * 1.609344 : null, "km/h")}`,
      `Pressure: ${textUnits(c.pressureHpa, "hPa")} · ${trendText()}`,
      `Beaufort basis: ${has(windMean().mph) ? textUnits(windMean().mph, "mph") + " ten-minute " + (windMean().estimated ? "estimated mean" : "mean") : "ten-minute mean unavailable"}; sensor height uncorrected`,
      `Since recorded rain: ${WeatherReadings.rainSinceText(state.weather.rainSince)} · ${state.weather.rainSince?.source || "source unavailable"}`,
      `Rain — today: ${rain("rainDayMm")}; rate: ${textUnits(inch(c.rainRateMmHr), "in/h", 2)}`,
    );
    if (r.rain)
      lines.push(
        `${climateName()} record precipitation for this date: ${fmt(r.rain.valueIn, 2)} in / ${fmt(r.rain.valueIn * 25.4)} mm (${shortYears(r.rain)})${r.cached ? " · cached" : ""}`,
      );
    if (isPublicReading()) {
      const r = state.weather.rain;
      lines.push(
        `Rain gauge: ${r?.stationName || "unavailable"} · Environment Agency${r?.distanceKm != null ? " · " + r.distanceKm + " km away" : ""}${r?.timestamp ? " · through " + fullStamp(r.timestamp * 1000) : ""}`,
      );
      for (const name of ["day", "event", "week", "month", "year"]) {
        const p = r?.periods?.[name];
        lines.push(
          `${name} rainfall: ${WeatherUKData.rainText(p)} · ${WeatherUKData.coverage(p)}`,
        );
      }
      lines.push(r?.note || "");
    }
    if (!isPublicReading())
      lines.push(
        `Rain — event: ${rain("rainEventMm")}; week: ${rain("rainWeekMm")}; month: ${rain("rainMonthMm")}; year: ${rain("rainYearMm")}`,
      );
    if (
      c.rainYearSource === "history" ||
      c.rainYearSource === "calculated" ||
      !has(c.rainYearMm)
    )
      lines.push(`Year-to-date note: ${yearRainExplanation(c)}`);
    for (const name of ["month", "year"])
      if (has(r[name + "Normal"]))
        lines.push(
          `${name === "month" ? "Month" : "Year"} normal to date (${climateName()}): ${textUnits(r[name + "Normal"], "in", 2)} · ${comparisonText(r[name])}`,
        );
    const rainRecords = rainRecordRefs();
    for (const period of ["month", "year"]) {
      const wet = rainRecords[period + "Wettest"],
        dry = rainRecords[period + "Driest"];
      if (wet || dry)
        lines.push(
          `${climateName()} ${period === "month" ? "MTD" : "YTD"} records (${recordWindow(period, rainRecords)}): maximum ${rainRecordText(wet)}; minimum ${rainRecordText(dry)}${rainRecords.cached ? " (cached)" : ""}`,
        );
    }
    if (has(r.monthNormal) || has(r.yearNormal))
      lines.push(
        `${climateName()} calculated 1991–2020 averages through ${calendarLabel(r.date)}, including today’s full-day expectation; comparison with your local station.`,
      );
    if (isPublicReading()) lines.push(publicCaption(state.weather));
    lines.push(
      `Solar radiation: ${textUnits(c.solar, "W/m²", 0)} · UV: ${has(c.uv) ? fmt(c.uv) : "unavailable"}`,
      w.demo
        ? "Station readings are synthetic samples. Climate averages are calculated from NOAA NCEI GHCN-Daily UK data."
        : isStale()
          ? "Old reading: check observation time."
          : `Sources: ${observationSource()}; ${climateFullName()} climate references from ${climateSource()}.`,
    );
    return lines.join("\n");
  }
  async function copyWeather() {
    if (!state.weather?.current)
      return toast("Wait for weather readings first.");
    displayWeather();
    const report = weatherText();
    try {
      await navigator.clipboard.writeText(report);
      toast("Weather text copied. Ready to paste.");
    } catch (error) {
      const area = $("copyReport");
      area.value = report;
      $("copyDialog").showModal();
      area.focus();
      area.select();
    }
  }

  function exportCard() {
    if (!state.weather?.current)
      return toast("Wait for weather readings first.");
    displayWeather();
    const c = currentReading(),
      s = state.settings,
      night = observationIsNight(),
      references = climateRefs(),
      rainRecords = rainRecordRefs(),
      canvas = document.createElement("canvas");
    canvas.width = 1600;
    canvas.height = 1240;
    const ctx = canvas.getContext("2d");
    const bg = ctx.createLinearGradient(0, 0, 1600, 1240);
    const palette = WeatherDesign.themes[state.theme || "night"];
    bg.addColorStop(0, palette.cardTop);
    bg.addColorStop(1, palette.cardBottom);
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, 1600, 1240);
    const glow = ctx.createRadialGradient(1080, 280, 10, 1080, 280, 500);
    glow.addColorStop(0, palette.glow);
    glow.addColorStop(1, "#ffffff00");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, 1600, 1240);
    function text(
      t,
      x,
      y,
      size = 24,
      color = "#eaf3f4",
      weight = 400,
      maxWidth,
    ) {
      ctx.font = `${weight} ${size}px "Segoe UI", Arial, sans-serif`;
      ctx.fillStyle = color;
      if (maxWidth) {
        while (ctx.measureText(t).width > maxWidth && size > 12) {
          size--;
          ctx.font = `${weight} ${size}px "Segoe UI", Arial, sans-serif`;
        }
      }
      ctx.fillText(t, x, y);
    }
    function line(y) {
      ctx.strokeStyle = "#80b1b344";
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(65, y);
      ctx.lineTo(1535, y);
      ctx.stroke();
    }
    function label(t, x, y) {
      text(t, x, y, 18, "#a5c6ce", 550);
    }
    text(s.title, 65, 85, 43, "#eef6f3", 400, 1050);
    text(
      [s.location, s.callsign].filter(Boolean).join("  ·  "),
      65,
      128,
      23,
      "#b9d0d7",
    );
    const tag = mode();
    ctx.fillStyle = state.weather.demo
      ? "#54463d"
      : isStale()
        ? "#683b44"
        : "#244e4d";
    ctx.beginPath();
    ctx.roundRect(1170, 48, 365, 48, 8);
    ctx.fill();
    text(tag, 1190, 80, 18, "#e2e9df", 550, 325);
    line(161);
    label(
      !state.weather.demo && isStale()
        ? "LAST STATION OBSERVATION"
        : "OUTSIDE RIGHT NOW",
      65,
      211,
    );
    text(condition(), 65, 258, 25, "#c4dbe0");
    text(`${fmt(celsius(c.tempF))}°C`, 55, 455, 174, "#f2f7f1", 300, 750);
    text(`${fmt(c.tempF)}°F`, 73, 516, 45, "#b5dbdc", 300);
    text(
      `Feels like ${fmt(c.feelsF)}°F / ${fmt(celsius(c.feelsF))}°C`,
      73,
      568,
      28,
      "#c8dce0",
    );
    label(
      `${climateName().toUpperCase()} ARCHIVE RECORDS · ${calendarLabel(WeatherClimate.bostonDay(Date.now() / 1000, climateZone())).toUpperCase()}${references.cached && references.date ? " · CACHED" : ""}`,
      800,
      211,
    );
    text(
      references.high
        ? `High ${climateTemp(references.high.valueF)} / ${climateTempC(references.high.valueF)} · ${shortYears(references.high)}`
        : "Record high unavailable",
      800,
      251,
      23,
      "#d8e6e9",
      400,
      700,
    );
    text(
      references.low
        ? `Low ${climateTemp(references.low.valueF)} / ${climateTempC(references.low.valueF)} · ${shortYears(references.low)}`
        : "Record low unavailable",
      800,
      283,
      23,
      "#c2dbeb",
      400,
      700,
    );
    ctx.save();
    ctx.translate(1240, 397);
    ctx.strokeStyle = night ? "#bfd1ef" : "#e8dbb3";
    ctx.lineWidth = 3;
    if (night) {
      ctx.beginPath();
      ctx.arc(0, 0, 72, 0.65 * Math.PI, 1.65 * Math.PI);
      ctx.bezierCurveTo(-26, -26, -11, 30, 58, 59);
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(0, 0, 62, 0, Math.PI * 2);
      ctx.stroke();
      for (let i = 0; i < 8; i++) {
        const a = (i * Math.PI) / 4;
        ctx.beginPath();
        ctx.moveTo(Math.cos(a) * 83, Math.sin(a) * 83);
        ctx.lineTo(Math.cos(a) * 101, Math.sin(a) * 101);
        ctx.stroke();
      }
    }
    ctx.restore();
    text(textUnits(c.solar, "W/m²", 0) + " solar", 1110, 540, 23, "#b6ccd5");
    text(
      `UV ${has(c.uv) ? fmt(c.uv) : "unavailable"}`,
      1110,
      576,
      23,
      "#b6ccd5",
    );
    line(614);
    const cols = [65, 440, 815, 1190];
    const metrics = [
      [
        "DEW POINT",
        has(c.dewF) ? `${fmt(c.dewF)}°F` : "Unavailable",
        has(c.dewF) ? `${fmt(celsius(c.dewF))}°C` : "",
      ],
      [
        "HUMIDITY",
        has(c.humidity) ? `${fmt(c.humidity, 0)}%` : "Unavailable",
        "relative humidity",
      ],
      [
        WeatherReadings.beaufort(windMean().mph)
          ? `WIND · ${WeatherReadings.beaufort(windMean().mph).label.toUpperCase()}`
          : "WIND",
        `${textUnits(c.windMph, "mph")}${WeatherReadings.beaufort(windMean().mph) ? " · Bft " + WeatherReadings.beaufort(windMean().mph).force : ""}`,
        `10-min ${windMean().estimated ? "est." : "mean"} ${textUnits(windMean().mph, "mph")} · Gusts ${textUnits(c.gustMph, "mph")}`,
      ],
      [pressureLabel(c), textUnits(c.pressureHpa, "hPa"), trendText()],
    ];
    metrics.forEach((m, i) => {
      label(m[0], cols[i], 667);
      text(m[1], cols[i], 723, 37, "#e9f4f4", 400, 330);
      text(m[2], cols[i], 764, 18, "#afcbd2", 400, 335);
      if (i === 2)
        text(
          `Daily max gust ${textUnits(c.maxGustMph, "mph")}`,
          cols[i],
          790,
          18,
          "#8bdfd0",
          500,
          335,
        );
    });
    line(803);
    const rainCols = [65, 359, 653, 947, 1241],
      rains = [
        ["TODAY’S RAIN", "rainDayMm"],
        ["THIS EVENT", "rainEventMm"],
        ["THIS WEEK", "rainWeekMm"],
        ["THIS MONTH", "rainMonthMm"],
        ["YEAR TO DATE", "rainYearMm"],
      ];
    rains.forEach((m, i) => {
      label(m[0], rainCols[i], 844);
      text(
        isPublicReading()
          ? WeatherUKData.rainText(
              state.weather.rain?.periods?.[
                ["day", "event", "week", "month", "year"][i]
              ],
            )
          : i === 4 && !has(c.rainYearMm)
            ? $("rainYear").textContent
            : textUnits(c[m[1]], "mm"),
        rainCols[i],
        893,
        32,
        "#e9f4f4",
        400,
        265,
      );
      text(
        isPublicReading()
          ? (() => {
              const p =
                state.weather.rain?.periods?.[
                  ["day", "event", "week", "month", "year"][i]
                ];
              return has(p?.mm)
                ? `${(p.mm / 25.4).toFixed(2)} in${p.complete ? "" : " · gaps"}`
                : "Unavailable";
            })()
          : i === 4
            ? $("rainYearMm").textContent
            : textUnits(inch(c[m[1]]), "in", 2),
        rainCols[i],
        927,
        21,
        "#afcbd2",
        400,
        265,
      );
      if (i === 0) {
        text(
          references.rain
            ? `${climateName()} record ${fmt(references.rain.valueIn, 2)} in · ${shortYears(references.rain)}`
            : "Local record unavailable",
          rainCols[i],
          952,
          16,
          "#bcd2dd",
          400,
          270,
        );
        text(
          `${isPublicReading() ? "15-min equivalent rate" : "Rate"} ${textUnits(inch(c.rainRateMmHr), "in/h", 2)}`,
          rainCols[i],
          975,
          16,
          "#bcd2dd",
          400,
          270,
        );
      }
      if (i >= 3) {
        const period = i === 3 ? "month" : "year";
        text(
          `Calc. average ${textUnits(has(references[period + "Normal"]) ? references[period + "Normal"] * 25.4 : null, "mm")}`,
          rainCols[i],
          952,
          16,
          "#bcd2dd",
          400,
          270,
        );
        text(
          comparisonText(references[period]),
          rainCols[i],
          975,
          16,
          references[period].kind === "below" ? "#e4c99e" : "#bcd2dd",
          400,
          270,
        );
        text(
          `${climateName().toUpperCase()} · ${recordWindow(period, rainRecords).toUpperCase()}${rainRecords.cached && rainRecords.throughYear ? " · CACHED" : ""}`,
          rainCols[i],
          1010,
          14,
          "#afcbd2",
          500,
          270,
        );
        text(
          `${i === 3 ? "MTD" : "YTD"} max ${rainRecordText(rainRecords[period + "Wettest"])}`,
          rainCols[i],
          1037,
          16,
          "#c6dce3",
          400,
          270,
        );
        text(
          `${i === 3 ? "MTD" : "YTD"} min ${rainRecordText(rainRecords[period + "Driest"])}`,
          rainCols[i],
          1064,
          16,
          "#c6dce3",
          400,
          270,
        );
      }
    });
    line(1100);
    if (isPublicReading() && state.weather.rain?.stationName)
      text(
        `RAIN: EA ${state.weather.rain.stationName} · ${state.weather.rain.distanceKm} km · through ${fullStamp(state.weather.rain.timestamp * 1000)} · ≥ means gaps; unchecked data provisional`,
        65,
        1090,
        15,
        "#b5d4dc",
        400,
        1470,
      );
    text(
      `${state.weather.demo ? "SAMPLE READINGS" : isStale() ? "LAST OBSERVATION" : "OBSERVED"}  ·  ${fullStamp(c.timestamp * 1000)}${isPublicReading() ? ` · ${state.weather.source === "wunderground" ? "Weather Underground" : isCWOP() ? "CWOP" : state.weather.source === "met-eireann" ? "Met Éireann" : state.weather.source === "metar" ? "METAR" : "NOAA"} ${state.weather.stationId || ""} · ${state.weather.stationName || ""}${has(state.weather.distanceKm) ? ` · ${fmt(state.weather.distanceKm)} km away` : ""}` : ""}`,
      65,
      1148,
      21,
      "#c0d3dc",
      400,
      1470,
    );
    text(
      state.weather.demo
        ? "Sample station readings · Local references: NCEI GHCN-Daily, 1991–2020 normals to date."
        : `${observationSource()} · ${climateName()} climate: ${climateSource()}, calculated 1991–2020 averages${isStale() ? " · Old station reading." : ""}`,
      65,
      1190,
      20,
      "#91b1c0",
    );
    canvas.toBlob((blob) => {
      if (!blob) return toast("Your browser could not create the image.");
      const link = document.createElement("a"),
        url = URL.createObjectURL(blob);
      link.href = url;
      const stamp = new Date(c.timestamp * 1000)
        .toISOString()
        .slice(0, 16)
        .replaceAll(":", "-");
      link.download = `weather-${state.weather.demo ? "SAMPLE-" : ""}${s.callsign.replace(/[^a-z0-9]/gi, "") || "station"}-${stamp}.png`;
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10000);
      toast("Weather card saved as a PNG.");
    }, "image/png");
  }
  function loadRadar() {
    state.radarLastAttempt = Date.now();
    return radarPanel.refresh();
  }
  async function connectStation() {
    const btn = $("connectBtn");
    btn.disabled = true;
    put("connectBtn", "Connecting…");
    $("connectStatus").classList.remove("failed");
    put("connectStatus", "Checking your Ambient station…");
    try {
      if (state.refreshPromise) await state.refreshPromise;
      const result = await api("/api/connect", {
        apiKey: $("apiKey").value,
        applicationKey: $("applicationKey").value,
        macAddress: $("macAddress").value,
        remember: $("rememberStation").checked,
      });
      if (result.needsSelection) {
        const select = $("stationSelect");
        select.replaceChildren();
        for (const device of result.devices) {
          const option = document.createElement("option");
          option.value = device.macAddress;
          option.textContent = `${device.name} · ${device.macAddress}`;
          select.append(option);
        }
        $("stationPicker").hidden = false;
        $("macAddress").value = select.value;
        put(
          "connectStatus",
          "More than one station was found. Choose one, then click Connect station again.",
        );
        return;
      }
      if (state.refreshPromise) await state.refreshPromise;
      await api("/api/settings", { weatherSource: "auto" });
      const status = await api("/api/status");
      calendarPanel?.sync();
      acceptStatus(status);
      state.history = [];
      state.historyLoaded = false;
      state.annual = null;
      $("apiKey").value = "";
      $("applicationKey").value = "";
      $("stationPicker").hidden = true;
      put(
        "connectStatus",
        `Connected to ${result.stationName}. ${state.credentialsSaved ? "Saved for automatic reconnection." : "Session only; keys will be forgotten on shutdown."}`,
      );
      await refresh();
      toast("Your station is connected.");
    } catch (error) {
      put("connectStatus", error.message);
      $("connectStatus").classList.add("failed");
    } finally {
      btn.disabled = false;
      put("connectBtn", "Connect station");
    }
  }
  async function saveSettings(event) {
    event.preventDefault();
    const data = Object.fromEntries(new FormData($("settingsForm")).entries());
    try {
      new Intl.DateTimeFormat("en-GB", { timeZone: data.timezone }).format(
        new Date(),
      );
    } catch (error) {
      put(
        "settingsStatus",
        "Enter a valid timezone, for example America/New_York.",
      );
      $("settingsStatus").classList.add("failed");
      return;
    }
    data.latitude = Number(data.latitude);
    data.longitude = Number(data.longitude);
    try {
      if (state.refreshPromise) await state.refreshPromise;
      clearTimeout(publicPollTimer);
      const result = await api("/api/settings", data);
      state.settings = result.settings;
      lightningPanel?.sync();
      state.weather = null;
      state.history = [];
      state.historyLoaded = false;
      state.forecast = null;
      state.climate = null;
      state.rainRecords = null;
      state.annual = null;
      pastPanel?.sync();
      radioPanel?.sync();
      calendarPanel?.sync();
      $("settingsDialog").close();
      displayWeather();
      loadRadar();
      alertPanel.refresh();
      await refresh();
      toast("Preferences saved.");
    } catch (error) {
      put("settingsStatus", error.message);
      $("settingsStatus").classList.add("failed");
    }
  }
  async function start() {
    try {
      const result = await api("/api/status");
      acceptStatus(result);
      if (state.serverMismatch) {
        setError();
        put("statusBadge", "RESTART REQUIRED");
        $("statusBadge").className = "status-badge stale";
        put("updatedLabel", "Restart the server to finish updating.");
        return;
      }
      if (
        typeof WeatherClimate === "undefined" ||
        typeof WeatherReadings === "undefined" ||
        !alertPanel ||
        !pastPanel ||
        !radioPanel ||
        !calendarPanel ||
        !locationPanel ||
        !lightningPanel ||
        !tokyoPanel ||
        !outlookPanel
      ) {
        setError(
          "A dashboard file is missing. Copy all files from the new ZIP, restart the server, then reload this page.",
        );
        return;
      }
      if (state.credentialNotice) toast(state.credentialNotice);
      $("skyThemeSelect").disabled = false;
      lightningPanel.start();
      tokyoPanel.start();
      alertPanel.start();
      pastPanel.start();
      radioPanel.start();
      calendarPanel.sync();
      if (!state.settings.locationConfigured && !state.connected)
        showSettings();
      loadRadar();
      setInterval(loadRadar, 300000);
      await refresh();
      setInterval(refresh, 60000);
    } catch (error) {
      setError(error.message);
      put("updatedLabel", "Keep the local server window open.");
    }
  }
  $("rainBreakdownBtn").addEventListener("click", () => {
    if (isPublicReading()) {
      ukData.openRain();
      return;
    }
    renderRainBreakdown();
    $("rainBreakdownDialog").showModal();
  });
  $("settingsBtn").addEventListener("click", showSettings);
  $("noticeConnect").addEventListener("click", showSettings);
  $("overviewBtn").addEventListener("click", () => setShare(false));
  $("shareBtn").addEventListener("click", () => setShare(true));
  $("chartsBtn").addEventListener("click", showCharts);
  $("pastDaysBtn").addEventListener("click", showPast);
  $("radioBtn").addEventListener("click", showRadio);
  $("skyThemeSelect").addEventListener("change", async () => {
    state.settings.skyTheme = $("skyThemeSelect").value;
    updateAppearance();
    try {
      await api("/api/settings", { skyTheme: state.settings.skyTheme });
    } catch (error) {
      toast("The colors changed, but the preference could not be saved.");
    }
  });
  $("refreshBtn").addEventListener("click", refresh);
  $("saveCardBtn").addEventListener("click", exportCard);
  $("copyBtn").addEventListener("click", copyWeather);
  $("chartTempBtn").addEventListener("click", () => {
    state.chart = "temp";
    renderChart();
  });
  $("chartPressureBtn").addEventListener("click", () => {
    state.chart = "pressure";
    renderChart();
  });
  $("connectBtn").addEventListener("click", connectStation);
  $("stationSelect").addEventListener("change", () => {
    $("macAddress").value = $("stationSelect").value;
  });
  $("settingsForm").addEventListener("submit", saveSettings);
  $("disconnectBtn").addEventListener("click", async () => {
    try {
      if (state.refreshPromise) await state.refreshPromise;
      await api("/api/disconnect", {});
      state.history = [];
      state.historyLoaded = false;
      state.annual = null;
      state.connected = false;
      state.credentialsSaved = false;
      state.credentialNotice = null;
      state.selectedStation = "";
      state.weather = null;
      calendarPanel?.sync();
      pastPanel?.sync();
      $("macAddress").value = "";
      $("rememberStation").checked = true;
      put(
        "connectStatus",
        "Saved station removed. Automatic mode uses the public provider for your selected location.",
      );
      $("apiKey").value = "";
      $("applicationKey").value = "";
      await refresh();
    } catch (error) {
      put("connectStatus", error.message);
    }
  });
  $("aboutBtn").addEventListener("click", () => $("aboutDialog").showModal());
  $("loadRadarBtn").addEventListener("click", loadRadar);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) {
      if (Date.now() - state.radarLastAttempt >= 300000) loadRadar();
      alertPanel?.refresh();
    }
  });
  document
    .querySelectorAll("[data-close]")
    .forEach((btn) =>
      btn.addEventListener("click", () => $(btn.dataset.close).close()),
    );
  document.querySelectorAll("dialog").forEach((dialog) =>
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) {
        const rect = dialog.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          dialog.close();
      }
    }),
  );
  start();
})();
