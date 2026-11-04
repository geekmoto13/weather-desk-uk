/* Public radio data is independent of Ambient station connectivity. */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.WeatherRadio = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const finite = (n) => typeof n === "number" && Number.isFinite(n);
  const BANDS = [
    "10m",
    "12m",
    "15m",
    "17m",
    "20m",
    "30m",
    "40m",
    "60m",
    "80m",
    "160m",
  ];
  const MAPS = {
    muf: {
      url: "https://prop.kc2g.com/renders/current/mufd-normal-now.svg",
      link: "https://prop.kc2g.com/",
      name: "MUF · 3000 km hops",
      interval: 900,
      note: "KC2G · MUF for 3000 km hops, with grey line. Shorter paths have lower usable limits.",
    },
    fof2: {
      url: "https://prop.kc2g.com/renders/current/fof2-normal-now.svg",
      link: "https://prop.kc2g.com/fof2/",
      name: "foF2 · NVIS guidance",
      interval: 900,
      note: "KC2G · critical frequency for near-vertical paths, useful for NVIS.",
    },
    absorption: {
      url: "https://services.swpc.noaa.gov/images/animations/d-rap/global/latest.png",
      link: "https://www.spaceweather.gov/products/d-region-absorption-predictions-d-rap",
      name: "D-region absorption",
      interval: 300,
      note: "NOAA D-RAP · highest frequency affected by 1 dB absorption. This absorption limit is not a full LUF.",
    },
  };
  const MODES = {
    SSB: { bandwidth: 3000, requiredSnr: 6 },
    CW: { bandwidth: 500, requiredSnr: 0 },
    FT8: { bandwidth: 50, requiredSnr: -3 },
  };
  function grid(lat, lon) {
    if (
      !finite(lat) ||
      !finite(lon) ||
      lat < -90 ||
      lat > 90 ||
      lon < -180 ||
      lon > 180
    )
      return "—";
    const x = Math.min(lon + 180, 359.999999),
      y = Math.min(lat + 90, 179.999999);
    return (
      String.fromCharCode(65 + Math.floor(x / 20), 65 + Math.floor(y / 10)) +
      Math.floor((x % 20) / 2) +
      String(Math.floor(y % 10)) +
      String.fromCharCode(
        97 + Math.floor((x % 2) * 12),
        97 + Math.floor((y % 1) * 24),
      )
    );
  }
  function xrayClass(flux) {
    if (!finite(flux) || flux < 0) return "—";
    for (const [label, base] of [
      ["X", 1e-4],
      ["M", 1e-5],
      ["C", 1e-6],
      ["B", 1e-7],
      ["A", 1e-8],
    ])
      if (flux >= base || label === "A")
        return label + (flux / base).toFixed(1);
  }
  function category(bcr) {
    return !finite(bcr)
      ? "unknown"
      : bcr >= 75
        ? "good"
        : bcr >= 50
          ? "fair"
          : "poor";
  }
  function source(data, name, now = Date.now() / 1000) {
    const raw = data?.sources?.[name];
    if (!raw) return { value: null, stale: true };
    const age = finite(raw.value?.timestamp)
      ? now - raw.value.timestamp
      : Infinity;
    const limits = {
      daily: 129600,
      scales: 900,
      kp: 21600,
      mag: 1200,
      wind: 1200,
      xray: 1200,
      wwv: 43200,
      bulletins: 900,
    };
    return {
      ...raw,
      stale:
        !!raw.stale ||
        !!raw.error ||
        age > (limits[name] || 3600) ||
        age < -300,
    };
  }
  function noaaDaily(data, now = Date.now() / 1000) {
    const daily = source(data, "daily", now),
      wwv = source(data, "wwv", now),
      choices = [daily, wwv];
    const solar =
      choices.find((s) => !s.stale && finite(s.value?.sfi)) ||
      choices.find((s) => finite(s.value?.sfi)) ||
      daily;
    return { solar, spots: daily, aIndex: wwv };
  }
  function geomagnetic(level) {
    return Number.isInteger(level) && level >= 0 && level <= 5
      ? [
          "Below G1",
          "G1 · Minor",
          "G2 · Moderate",
          "G3 · Strong",
          "G4 · Severe",
          "G5 · Extreme",
        ][level]
      : "—";
  }
  function pathModel(data, now = Date.now() / 1000) {
    const envelope = data?.path,
      p = envelope?.value;
    const prefs = data?.settings,
      context = data?.context;
    const valid =
      !!p &&
      Array.isArray(p.bands) &&
      !envelope.stale &&
      !envelope.error &&
      finite(p.timestamp) &&
      now - p.timestamp <= 10800 &&
      p.timestamp <= now + 3600 &&
      p.context?.txLat === context?.txLat &&
      p.context?.txLon === context?.txLon &&
      p.context?.destination === context?.destination &&
      JSON.stringify(p.context?.preferences) === JSON.stringify(prefs);
    if (!valid)
      return {
        valid: false,
        rows: [],
        lowest: null,
        highest: null,
        best: null,
      };
    const modelStart = Math.ceil(p.timestamp / 3600) * 3600;
    const time = Math.max(Math.floor(now / 3600) * 3600, modelStart),
      hour = new Date(time * 1000).getUTCHours();
    const count = Math.max(
      0,
      Math.min(24, Math.floor((modelStart + 86400 - time) / 3600)),
    );
    const rows = BANDS.map((band) => {
      const row = p.bands?.find((r) => r.band === band),
        cell = row?.hours?.[hour];
      return {
        band,
        mhz: row?.mhz,
        bcr: cell?.bcr ?? null,
        snr: cell?.snr ?? null,
        hours: row?.hours || [],
      };
    });
    const usable = rows
      .filter((r) => finite(r.bcr) && r.bcr >= 50 && finite(r.mhz))
      .sort((a, b) => a.mhz - b.mhz);
    const best = rows
      .filter((r) => finite(r.bcr))
      .sort((a, b) => b.bcr - a.bcr || (b.snr ?? -999) - (a.snr ?? -999))[0];
    return {
      valid: true,
      rows,
      lowest: usable[0] || null,
      highest: usable.at(-1) || null,
      best: best?.bcr >= 50 ? best : null,
      time,
      hour,
      count,
    };
  }
  function futureKp(data, now = Date.now() / 1000) {
    const s = source(data, "kp", now);
    if (s.stale) return [];
    return (Array.isArray(s.value?.points) ? s.value.points : [])
      .filter(
        (p) =>
          p.kind === "predicted" &&
          finite(p.kp) &&
          p.timestamp >= now &&
          p.timestamp <= now + 86400,
      )
      .slice(0, 8);
  }
  function pathHour(model, row, offset) {
    if (
      !model.valid ||
      !Number.isInteger(offset) ||
      offset < 0 ||
      offset >= model.count ||
      !Array.isArray(row?.hours)
    )
      return null;
    return row.hours[(model.hour + offset) % 24] || null;
  }
  function create({ api, getSettings, isVisible }) {
    const $ = (id) => document.getElementById(id),
      put = (id, v) => {
        $(id).textContent = v;
      };
    const fmt = (n, d = 1) => (finite(n) ? n.toFixed(d) : "—");
    const utc = (t, full = false) =>
      finite(t)
        ? new Intl.DateTimeFormat("en-GB", {
            timeZone: "UTC",
            ...(full ? { month: "short", day: "numeric" } : {}),
            hour: "2-digit",
            minute: "2-digit",
            hourCycle: "h23",
          }).format(new Date(t * 1000)) + " UTC"
        : "Time unavailable";
    const local = (t, options) =>
      new Intl.DateTimeFormat("en-US", {
        timeZone: getSettings()?.timezone || "America/New_York",
        ...options,
      }).format(new Date(t));
    let data = null,
      revision = 0,
      timer = null,
      clockTimer = null,
      started = false,
      map = "muf",
      mapKey = "",
      identity = "";
    const locationIdentity = () =>
      `${getSettings()?.latitude}|${getSettings()?.longitude}`;
    function metric(id, value, s, detail) {
      put(id, value ?? "—");
      $(id).closest(".radio-metric").dataset.stale = String(!!s.stale);
      put(id + "Note", `${s.stale && s.value ? "Saved · " : ""}${detail}`);
      $(id).closest(".radio-metric").title =
        `${detail} · ${utc(s.value?.timestamp, true)}${s.error ? " · Refresh unavailable" : ""}`;
    }
    function clock() {
      if (!isVisible() || !getSettings()) return;
      const settings = getSettings(),
        now = Date.now();
      put(
        "radioUtc",
        new Intl.DateTimeFormat("en-GB", {
          timeZone: "UTC",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        }).format(new Date(now)) + " UTC",
      );
      put(
        "radioLocal",
        local(now, {
          hour: "numeric",
          minute: "2-digit",
          timeZoneName: "short",
        }),
      );
      put(
        "radioStation",
        `${settings.callsign || "Your station"} · ${grid(settings.latitude, settings.longitude)} · ${settings.location || "Your location"}`,
      );
      try {
        const a = globalThis.WeatherAstro.calculate(
          now,
          settings.latitude,
          settings.longitude,
          settings.timezone,
        );
        const daylight = a.sunAltitude >= -0.833;
        put(
          "radioSun",
          `${daylight ? "Day" : "Night"} at home · rise ${finite(a.sun.rise) ? local(a.sun.rise, { hour: "numeric", minute: "2-digit" }) : "—"} · set ${finite(a.sun.set) ? local(a.sun.set, { hour: "numeric", minute: "2-digit" }) : "—"}`,
        );
      } catch {
        put("radioSun", "Check station coordinates and timezone in Settings.");
      }
    }
    function refreshMap() {
      if (!isVisible()) return;
      const definition = MAPS[map],
        key = map + "|" + Math.floor(Date.now() / 1000 / definition.interval);
      if (key === mapKey) return;
      mapKey = key;
      const img = $("radioMapImage");
      img.hidden = true;
      $("radioMapPlaceholder").hidden = false;
      put("radioMapPlaceholder", "Loading the source map…");
      put("radioMapTitle", definition.name);
      put("radioMapNote", definition.note);
      $("radioMapLink").href = definition.link;
      for (const id of Object.keys(MAPS)) {
        const button = $("radioMap" + id);
        button.classList.toggle("active", id === map);
        button.setAttribute("aria-pressed", String(id === map));
      }
      const expected = map;
      img.onload = () => {
        if (expected !== map) return;
        img.hidden = false;
        $("radioMapPlaceholder").hidden = true;
        if (img.naturalWidth > 0 && img.naturalHeight > 0)
          $("radioSection").style.setProperty(
            "--radio-map-ratio",
            String(img.naturalWidth / img.naturalHeight),
          );
        put(
          "radioMapChecked",
          `Image loaded ${utc(Date.now() / 1000)} · use the valid time printed on the map`,
        );
      };
      img.onerror = () => {
        if (expected !== map) return;
        img.hidden = true;
        $("radioMapPlaceholder").hidden = false;
        put(
          "radioMapPlaceholder",
          "Map unavailable. Use Open source map, or wait for the next automatic refresh.",
        );
        put(
          "radioMapChecked",
          "Image unavailable · automatic retry follows the source refresh interval",
        );
      };
      img.src = definition.url + "?weatherdesk=" + key.split("|")[1];
    }
    function table() {
      const p = pathModel(data),
        body = $("radioBandRows");
      body.replaceChildren();
      for (const [id, offset] of [
        ["radioPathColumn", 0],
        ["radioPlus3Column", 3],
        ["radioPlus6Column", 6],
      ])
        put(
          id,
          p.valid && offset < p.count
            ? utc(p.time + offset * 3600).replace(":00 UTC", "Z")
            : offset
              ? "+" + offset + " H"
              : "NOW",
        );
      put(
        "radioSnrHeading",
        `SNR · ${MODES[data?.settings?.mode]?.bandwidth || 3000} Hz`,
      );
      for (const band of BANDS) {
        const row = p.rows.find((r) => r.band === band);
        const tr = document.createElement("tr"),
          head = document.createElement("th");
        head.scope = "row";
        head.textContent = band;
        tr.append(head);
        for (const offset of [0, 3, 6]) {
          const cell = pathHour(p, row, offset),
            td = document.createElement("td");
          td.textContent = finite(cell?.bcr) ? fmt(cell.bcr, 0) + "%" : "—";
          td.dataset.rating = category(cell?.bcr);
          td.title = cell
            ? `${utc(p.time + offset * 3600, true)} · KC2G modelled circuit reliability for your selected path and station settings.`
            : "Forecast unavailable for this hour.";
          tr.append(td);
        }
        const snr = document.createElement("td");
        snr.textContent = finite(row?.snr) ? `${fmt(row.snr)} dB` : "—";
        snr.title =
          "Predicted signal power minus noise power in the selected mode bandwidth.";
        tr.append(snr);
        body.append(tr);
      }
      put(
        "radioLowest",
        p.valid
          ? p.lowest
            ? `${p.lowest.band} · ${fmt(p.lowest.mhz, 2)} MHz`
            : "No tested band ≥50%"
          : "Forecast unavailable",
      );
      put(
        "radioBest",
        p.valid
          ? p.best
            ? `${p.best.band} · ${fmt(p.best.bcr, 0)}%`
            : "No tested band ≥50%"
          : "Waiting for forecast",
      );
      const settings = data?.settings;
      put(
        "radioPathLabel",
        data?.context
          ? `Your station → ${data.context.destination}`
          : "Your station → England · Midlands",
      );
      put(
        "radioAssumptions",
        settings
          ? `${settings.mode} · ${fmt(settings.power, 0)} W · idealised ${fmt(settings.txGain, 0)}/${fmt(settings.rxGain, 0)} dBi antennas · ${settings.noise.toLowerCase()} noise`
          : "Choose the path and station assumptions.",
      );
      put(
        "radioPathStamp",
        p.valid
          ? `Model ${utc(data.path.value.timestamp, true)} · values for ${utc(p.time, true)}`
          : data?.path?.loading
            ? "Calculating the selected path…"
            : data?.path?.error
              ? "Path source unavailable. NOAA conditions are checked independently."
              : "Waiting for the selected path.",
      );
      $("radioForecastBtn").disabled = !p.valid;
    }
    function render() {
      if (!data) return;
      clock();
      const mag = source(data, "mag"),
        wind = source(data, "wind"),
        x = source(data, "xray"),
        k = source(data, "kp"),
        s = source(data, "scales");
      const { solar, spots, aIndex: a } = noaaDaily(data);
      const reportDate = (feed) =>
        feed.value?.date
          ? new Intl.DateTimeFormat("en-US", {
              timeZone: "UTC",
              month: "short",
              day: "numeric",
            }).format(new Date(feed.value.date + "T12:00:00Z"))
          : "date unavailable";
      metric(
        "radioSfi",
        finite(solar.value?.sfi) ? fmt(solar.value.sfi, 0) : null,
        solar,
        `NOAA 10.7 cm · ${reportDate(solar)} · sfu`,
      );
      metric(
        "radioSn",
        finite(spots.value?.sn) ? fmt(spots.value.sn, 0) : null,
        spots,
        `NOAA SWPC · ${reportDate(spots)}`,
      );
      metric(
        "radioA",
        finite(a.value?.aIndex) ? fmt(a.value.aIndex, 0) : null,
        a,
        `NOAA daily A · ${reportDate(a)}`,
      );
      metric(
        "radioKp",
        finite(k.value?.current?.kp) ? fmt(k.value.current.kp, 2) : null,
        k,
        `NOAA ${k.value?.current?.kind || ""} Kp · 3h`,
      );
      metric(
        "radioGeomag",
        geomagnetic(s.value?.levels?.G),
        s,
        "NOAA observed geomagnetic scale",
      );
      metric(
        "radioWind",
        finite(wind.value?.speed) ? fmt(wind.value.speed, 0) + " km/s" : null,
        wind,
        `NOAA ${wind.value?.spacecraft || "primary"} solar wind`,
      );
      metric(
        "radioBz",
        finite(mag.value?.bz)
          ? (mag.value.bz > 0 ? "+" : "") + fmt(mag.value.bz) + " nT"
          : null,
        mag,
        `NOAA Bz · ${finite(mag.value?.bz) ? (mag.value.bz < 0 ? "southward" : "northward") : "—"} · Bt ${fmt(mag.value?.bt)} nT`,
      );
      metric(
        "radioXray",
        x.value ? xrayClass(x.value.flux) : null,
        x,
        "NOAA GOES · 0.1–0.8 nm",
      );
      for (const code of ["R", "S", "G"]) {
        const n = s.value?.levels?.[code];
        put(
          "radioScale" + code,
          s.stale || !finite(n) ? `${code} —` : `${code}${fmt(n, 0)}`,
        );
        $("radioScale" + code).dataset.severity =
          s.stale || !finite(n)
            ? "unknown"
            : n >= 3
              ? "high"
              : n > 0
                ? "elevated"
                : "quiet";
      }
      put(
        "radioScaleTime",
        s.stale
          ? "NOAA scales unavailable / saved data"
          : `Latest observed · ${utc(s.value?.timestamp, true)}`,
      );
      const kp = futureKp(data),
        target = $("radioKpBars");
      target.replaceChildren();
      for (const point of kp) {
        const bar = document.createElement("div"),
          fill = document.createElement("i"),
          label = document.createElement("span");
        fill.style.height = 6 + point.kp * 4 + "px";
        fill.dataset.level =
          point.kp >= 5 ? "storm" : point.kp >= 4 ? "active" : "quiet";
        label.textContent = utc(point.timestamp).slice(0, 2) + "Z";
        bar.title = `${utc(point.timestamp, true)} · predicted Kp ${fmt(point.kp, 2)}`;
        const number = document.createElement("b");
        number.textContent = fmt(point.kp, 1);
        bar.append(number, fill, label);
        target.append(bar);
      }
      put(
        "radioKpPeak",
        kp.length
          ? `Next 24h peak Kp ${fmt(Math.max(...kp.map((p) => p.kp)), 2)}`
          : "Kp outlook unavailable",
      );
      const bullet = source(data, "bulletins");
      const count = bullet.value?.items?.length;
      put(
        "radioBulletinsNote",
        bullet.stale
          ? "Recent bulletin feed unavailable"
          : `${count || 0} bulletin${count === 1 ? "" : "s"} issued in the last 48 hours`,
      );
      const outlook = !s.stale ? s.value?.outlook?.[0] : null;
      put(
        "radioBlackoutOutlook",
        finite(outlook?.minorBlackout)
          ? `NOAA outlook · R1–R2 ${fmt(outlook.minorBlackout, 0)}% · R3–R5 ${fmt(outlook.majorBlackout, 0)}%`
          : "Radio-blackout outlook unavailable",
      );
      put(
        "radioDensity",
        finite(wind.value?.density)
          ? `${wind.stale ? "Saved · " : ""}${fmt(wind.value.density)} /cm³`
          : "—",
      );
      $("radioDensity").title =
        `NOAA ${wind.value?.spacecraft || "primary"} proton density · ${utc(wind.value?.timestamp, true)}`;
      const countLoading =
        Object.values(data.sources || {}).filter((v) => v.loading).length +
        (data.path?.loading ? 1 : 0);
      const failed =
        Object.values(data.sources || {}).filter((v) => v.error).length +
        (data.path?.error ? 1 : 0);
      put(
        "radioStatus",
        countLoading
          ? `Updating ${countLoading} source${countLoading === 1 ? "" : "s"}…`
          : failed
            ? `${failed} source${failed === 1 ? "" : "s"} unavailable · saved values are labelled`
            : "Feed checks complete · observation times in tooltips",
      );
      table();
    }
    async function refresh(token = revision) {
      if (!isVisible() || token !== revision) return;
      clearTimeout(timer);
      const id = locationIdentity();
      try {
        const value = await api("/api/radio");
        if (token !== revision || id !== locationIdentity()) return;
        data = value;
        render();
        refreshMap();
        timer = setTimeout(() => refresh(token), value.loading ? 2000 : 60000);
      } catch (error) {
        if (token !== revision) return;
        put("radioStatus", error.message || "Radio feeds could not refresh.");
        if (data) {
          for (const s of Object.values(data.sources || {})) s.stale = true;
          if (data.path) data.path.stale = true;
          render();
          put("radioStatus", error.message);
        }
        timer = setTimeout(() => refresh(token), 60000);
      }
    }
    function showSetup() {
      const form = $("radioSettingsForm"),
        prefs = data?.settings || {
          target: "uk",
          customGrid: "IO92",
          mode: "SSB",
          power: 100,
          txGain: 0,
          rxGain: 0,
          noise: "RESIDENTIAL",
        };
      for (const [key, value] of Object.entries(prefs))
        if (form.elements[key]) form.elements[key].value = value;
      put("radioSettingsStatus", "");
      $("radioSettingsDialog").showModal();
    }
    async function saveSetup(event) {
      event.preventDefault();
      const button = $("radioSettingsSave");
      button.disabled = true;
      const values = Object.fromEntries(new FormData($("radioSettingsForm")));
      try {
        await api("/api/radio/settings", values);
        $("radioSettingsDialog").close();
        revision++;
        data = null;
        table();
        put("radioStatus", "Loading the selected path…");
        await refresh(revision);
      } catch (error) {
        put("radioSettingsStatus", error.message);
      } finally {
        button.disabled = false;
      }
    }
    function showForecast() {
      const p = pathModel(data);
      if (!p.valid) return;
      const table = $("radioForecastTable");
      table.replaceChildren();
      const header = document.createElement("tr"),
        label = document.createElement("th");
      label.textContent = "Band";
      header.append(label);
      for (let i = 0; i < p.count; i++) {
        const th = document.createElement("th");
        th.scope = "col";
        th.textContent = utc(p.time + i * 3600).slice(0, 2) + "Z";
        header.append(th);
      }
      const thead = document.createElement("thead");
      thead.append(header);
      table.append(thead);
      const body = document.createElement("tbody");
      for (const row of p.rows) {
        const tr = document.createElement("tr"),
          th = document.createElement("th");
        th.scope = "row";
        th.textContent = row.band;
        tr.append(th);
        for (let i = 0; i < p.count; i++) {
          const cell = row.hours[(p.hour + i) % 24],
            td = document.createElement("td");
          td.textContent = finite(cell?.bcr) ? fmt(cell.bcr, 0) + "%" : "—";
          td.dataset.rating = category(cell?.bcr);
          td.title = `${utc(p.time + i * 3600, true)} · SNR ${fmt(cell?.snr)} dB`;
          tr.append(td);
        }
        body.append(tr);
      }
      table.append(body);
      put(
        "radioForecastNote",
        `${data.context.destination} · ${data.settings.mode} · ${data.settings.power} W · ${p.count} hourly predictions from ${utc(p.time, true)}. Percentage = circuit reliability; hover for SNR in the selected mode bandwidth.`,
      );
      $("radioForecastDialog").showModal();
    }
    function showBulletins() {
      const feed = source(data, "bulletins"),
        body = $("radioBulletinList");
      body.replaceChildren();
      const summary = document.createElement("p");
      summary.textContent = feed.stale
        ? "The bulletin feed is unavailable. Saved messages below may be out of date."
        : "Recent bulletins, issued within 48 hours. A bulletin can describe an event that has ended; read its validity times.";
      body.append(summary);
      for (const item of feed.value?.items || []) {
        const article = document.createElement("article"),
          heading = document.createElement("h3"),
          date = document.createElement("p"),
          text = document.createElement("pre");
        heading.textContent = item.headline;
        date.textContent = utc(item.timestamp, true);
        text.textContent = item.message;
        article.append(heading, date, text);
        body.append(article);
      }
      if (!feed.value?.items?.length) {
        const p = document.createElement("p");
        p.textContent = feed.stale
          ? "No saved messages are available."
          : "No bulletins returned for this time window.";
        body.append(p);
      }
      $("radioBulletinsDialog").showModal();
    }
    function sync() {
      const id = locationIdentity();
      if (identity !== id) {
        identity = id;
        revision++;
        clearTimeout(timer);
        data = null;
        if (started && isVisible()) {
          table();
          put("radioStatus", "Loading the selected station path…");
          refresh(revision);
        }
      }
      if (started && isVisible()) clock();
    }
    function open() {
      identity = locationIdentity();
      revision++;
      clock();
      refreshMap();
      if (data) render();
      refresh(revision);
      clearInterval(clockTimer);
      clockTimer = setInterval(clock, 1000);
    }
    function close() {
      revision++;
      clearTimeout(timer);
      clearInterval(clockTimer);
    }
    function start() {
      if (started) return;
      started = true;
      for (const id of Object.keys(MAPS))
        $("radioMap" + id).addEventListener("click", () => {
          map = id;
          refreshMap();
        });
      $("radioRefreshBtn").addEventListener("click", () => {
        refresh(revision);
        refreshMap();
      });
      $("radioSetupBtn").addEventListener("click", showSetup);
      $("radioSettingsForm").addEventListener("submit", saveSetup);
      $("radioForecastBtn").addEventListener("click", showForecast);
      $("radioBulletinsBtn").addEventListener("click", showBulletins);
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden && isVisible()) {
          clock();
          refreshMap();
          refresh(revision);
        }
      });
    }
    return { start, open, close, sync };
  }
  return {
    BANDS,
    MAPS,
    MODES,
    grid,
    xrayClass,
    category,
    source,
    noaaDaily,
    geomagnetic,
    pathModel,
    pathHour,
    futureKp,
    create,
  };
});
