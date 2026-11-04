/* Free official satellite observations, alongside Blitzortung's documented map. */
(function (root) {
  "use strict";
  const finite = (x) => typeof x === "number" && Number.isFinite(x);
  function point(settings) {
    if (
      !finite(settings?.latitude) ||
      !finite(settings?.longitude) ||
      Math.abs(settings.latitude) > 90 ||
      Math.abs(settings.longitude) > 180
    )
      return null;
    return `${settings.latitude.toFixed(4)},${settings.longitude.toFixed(4)}`;
  }
  function mapURL(settings, expanded = false) {
    const location = point(settings);
    if (!location) return null;
    const [lat, lon] = location.split(",");
    const params = new URLSearchParams({
      interactive: expanded ? "1" : "0",
      NavigationControl: expanded ? "1" : "0",
      FullScreenControl: "0",
      Cookies: "0",
      InfoDiv: "0",
      MenuButtonDiv: expanded ? "1" : "0",
      ScaleControl: "1",
      Advertisment: "0",
      LinksCheckboxChecked: "0",
      DetectorsCheckboxChecked: "0",
      AudioCheckboxChecked: "0",
    });
    return `https://map.blitzortung.org/index.php?${params}#7/${lat}/${lon}`;
  }
  function formatAge(stamp, now) {
    if (!finite(stamp)) return "Time unavailable";
    const minutes = Math.max(0, Math.floor((now - stamp) / 60));
    return minutes < 1
      ? "Less than a minute ago"
      : minutes < 60
        ? `${minutes} min ago`
        : `${Math.floor(minutes / 60)} h ${minutes % 60} min ago`;
  }
  function describe(data, now = Date.now() / 1000) {
    if (!data)
      return {
        value: "Starting…",
        detail: "Free satellite lightning",
        note: "Connecting to the local dashboard.",
        state: "loading",
      };
    if (!data.configured)
      return {
        value: "Live map",
        detail: "Connect your free EUMETSAT account for nearby flashes.",
        note: "Consumer key and secret go in Settings. No paid subscription.",
        state: "map",
      };
    if (data.status === "unsupported")
      return {
        value: "Map only",
        detail: "Satellite coverage unavailable here",
        note: data.error,
        state: "error",
      };
    if (data.decoderError)
      return {
        value: "Decoder unavailable",
        detail: "The live map remains available.",
        note: data.decoderError,
        state: "error",
      };
    if (!data.windowEnd)
      return {
        value: data.error ? "Unavailable" : "Connecting…",
        detail: data.source || "Satellite lightning",
        note: data.error || "Finding recent satellite observations.",
        state: data.error ? "error" : "loading",
      };
    const old = !!data.stale || now - data.windowEnd > data.staleAfterSeconds;
    const partial = !data.complete;
    if (!data.closest)
      return {
        value: old
          ? "Update delayed"
          : partial
            ? "Loading flashes…"
            : "No detections",
        detail: partial
          ? `${data.decoded || 0}/${data.files.length} files · coverage incomplete`
          : "Within 75 km · 45-min observation window",
        note: old
          ? data.error ||
            "Current activity is unknown; the observations are old."
          : data.failures
            ? data.fileError ||
              "Some files could not load. Retrying automatically."
            : data.qualityWarnings
              ? "Satellite input quality warnings; empty results are provisional."
              : partial
                ? "Reading observations. Missing files are never treated as no lightning."
                : "No accepted flashes in this observation window.",
        state: old ? "error" : partial ? "loading" : "quiet",
      };
    const flash = data.closest;
    return {
      value: `${(flash.distanceKm / 1.609344).toFixed(1)} mi · ${flash.distanceKm.toFixed(1)} km`,
      detail: `${flash.direction} · ${formatAge(flash.timestamp, now)}${old ? " · OLD REPORT" : partial ? " · PROVISIONAL" : ""}`,
      note: old
        ? data.error || "Update delayed. Showing the last available flash."
        : partial
          ? data.fileError ||
            `Nearest loaded flash · ${data.decoded || 0}/${data.files.length} files`
          : `Closest satellite flash · ${data.flashCount} nearby in this window`,
      state: old ? "error" : partial ? "loading" : "detected",
    };
  }
  function create({ api, getSettings, onSettings, region = "US" }) {
    const $ = (id) => document.getElementById(id);
    const put = (id, text) => {
      const el = $(id);
      if (el && el.textContent !== text) el.textContent = text;
    };
    let locationKey = null,
      epoch = 0,
      pending = null,
      result = null,
      lastRequest = 0,
      started = false,
      worker = null,
      workerCatalogue = "",
      quickTimer = null;
    function stamp(value) {
      try {
        return new Intl.DateTimeFormat("en-GB", {
          timeZone: getSettings()?.timezone,
          month: "short",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          timeZoneName: "short",
        }).format(new Date(value * 1000));
      } catch {
        return new Date(value * 1000).toISOString();
      }
    }
    function render() {
      const view = describe(result);
      put("lightningDistance", view.value);
      put("lightningWhen", view.detail);
      put("lightningNote", view.note);
      $("lightningPanel").dataset.state = view.state;
      const flash = result?.closest;
      put("lightningAt", flash ? `Occurred ${stamp(flash.timestamp)}` : "");
      $("lightningAt").hidden = !flash;
      $("lightningWhen").title = flash
        ? `Reported position ${flash.latitude.toFixed(4)}, ${flash.longitude.toFixed(4)}. Distance from your saved location; not an exact ground-strike location.`
        : "";
      put(
        "lightningChecked",
        result?.windowEnd
          ? `${result.source} · through ${stamp(result.windowEnd)}`
          : region === "UK"
            ? "EUMETSAT flashes · Blitzortung map"
            : "NOAA flashes · Blitzortung map",
      );
      put(
        "lightningConnectStatus",
        region === "US"
          ? "NOAA lightning runs automatically. No account or key is needed."
          : result?.notice ||
              (result?.configured
                ? `${result.saved ? "Connection saved on this computer" : "Connection in use for this session"}. ${result.error || "EUMETSAT catalogue checked every 5 minutes."}`
                : "Connect your free EUMETSAT consumer key and secret. The map works without them."),
      );
      $("eumetsatConnection").hidden = region !== "UK";
      $("lightningForgetBtn").disabled = !result?.configured && !result?.notice;
      $("lightningSetupBtn").textContent = region === "UK" ? "Set up" : "Info";
    }
    function releaseWorker() {
      worker?.terminate();
      worker = null;
      workerCatalogue = "";
    }
    function decode(catalogue) {
      if (!catalogue.files?.length) return;
      if (!worker) {
        try {
          worker = new Worker("/lightning-worker.js");
        } catch {
          result.decoderError =
            "This browser could not start the satellite worker. Reload the page or use the live map.";
          render();
          return;
        }
        const generation = epoch;
        worker.onmessage = (event) => {
          if (generation !== epoch) return;
          const value = event.data;
          if (value.fatal) {
            result.decoderError = value.error;
            render();
            return;
          }
          if (value.epoch !== epoch || value.catalogueId !== workerCatalogue)
            return;
          Object.assign(result, value);
          render();
        };
        worker.onerror = () => {
          if (generation === epoch) {
            result.decoderError =
              "The satellite decoder could not run. Reload the page or use the live map.";
            render();
          }
        };
      }
      workerCatalogue = `${catalogue.provider}/${catalogue.windowEnd}/${catalogue.files.map((f) => f.id).join(",")}`;
      worker.postMessage({
        epoch,
        catalogueId: workerCatalogue,
        catalogue,
        location: {
          latitude: catalogue.latitude,
          longitude: catalogue.longitude,
        },
      });
    }
    function sync() {
      const settings = getSettings(),
        key = point(settings);
      if (key === locationKey) return;
      locationKey = key;
      epoch++;
      result = null;
      lastRequest = 0;
      releaseWorker();
      clearTimeout(quickTimer);
      render();
      const url = mapURL(settings);
      $("lightningFrame").src = url || "about:blank";
      $("lightningFrame").title =
        `Blitzortung lightning map centred on ${settings?.location || "your saved location"}`;
      put(
        "lightningMapLocation",
        `Map centred on ${settings?.location || "your saved location"}`,
      );
      $("lightningMapLink").href = url || "https://map.blitzortung.org/";
      if ($("lightningMapDialog").open)
        $("lightningLargeFrame").src = mapURL(settings, true) || "about:blank";
      if (started) refresh();
    }
    async function refresh() {
      sync();
      const version = epoch,
        key = locationKey;
      if (!key || pending === version) return;
      pending = version;
      lastRequest = Date.now();
      clearTimeout(quickTimer);
      try {
        const data = await api("/api/lightning"),
          [lat, lon] = key.split(",").map(Number);
        if (
          epoch !== version ||
          !finite(data.latitude) ||
          !finite(data.longitude) ||
          Math.abs(data.latitude - lat) > 0.00011 ||
          Math.abs(data.longitude - lon) > 0.00011
        )
          return;
        const sameWindow = result?.windowEnd === data.windowEnd;
        const previous = sameWindow
          ? {
              closest: result?.closest,
              complete: result?.complete,
              decoded: result?.decoded,
              flashCount: result?.flashCount,
              qualityWarnings: result?.qualityWarnings,
              failures: result?.failures,
              fileError: result?.fileError,
            }
          : {};
        result = { ...data, ...previous };
        if (data.files?.length) decode(data);
        if (data.status === "loading") quickTimer = setTimeout(refresh, 2000);
      } catch {
        if (epoch !== version) return;
        result = {
          ...(result || {}),
          configured: result?.configured ?? true,
          stale: true,
          error:
            "The local dashboard could not refresh lightning. Keep its server window open.",
        };
      } finally {
        if (pending === version) pending = null;
        if (epoch === version) render();
      }
    }
    async function update(action) {
      const button = $(
        action === "connect" ? "lightningConnectBtn" : "lightningForgetBtn",
      );
      button.disabled = true;
      try {
        await api("/api/lightning/settings", {
          action,
          consumerKey: $("eumetsatKey").value,
          consumerSecret: $("eumetsatSecret").value,
          remember: $("rememberLightning").checked,
        });
        $("eumetsatKey").value = "";
        $("eumetsatSecret").value = "";
        epoch++;
        result = null;
        releaseWorker();
        await refresh();
      } catch (error) {
        put("lightningConnectStatus", error.message);
      } finally {
        button.disabled =
          action === "forget" && !result?.configured && !result?.notice;
      }
    }
    function start() {
      if (started) return;
      started = true;
      $("lightningSetupBtn").addEventListener("click", () => {
        onSettings();
        $(region === "UK" ? "eumetsatKey" : "lightningSettings").focus();
      });
      $("lightningConnectBtn").addEventListener("click", () =>
        update("connect"),
      );
      $("lightningForgetBtn").addEventListener("click", () => update("forget"));
      $("lightningExpandBtn").addEventListener("click", () => {
        $("lightningLargeFrame").src =
          mapURL(getSettings(), true) || "about:blank";
        $("lightningMapDialog").showModal();
      });
      $("lightningMapDialog").addEventListener("close", () => {
        $("lightningLargeFrame").src = "about:blank";
      });
      $("lightningReloadBtn").addEventListener("click", () => {
        $("lightningFrame").src = mapURL(getSettings()) || "about:blank";
        if ($("lightningMapDialog").open)
          $("lightningLargeFrame").src =
            mapURL(getSettings(), true) || "about:blank";
      });
      refresh();
      setInterval(() => {
        if (!document.hidden) refresh();
      }, 60000);
      setInterval(() => {
        if (!document.hidden) render();
      }, 30000);
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden && Date.now() - lastRequest >= 60000) refresh();
      });
    }
    return { start, sync, refresh };
  }
  const exported = { point, mapURL, formatAge, describe, create };
  if (typeof module !== "undefined" && module.exports)
    module.exports = exported;
  root.WeatherLightning = exported;
})(typeof globalThis !== "undefined" ? globalThis : this);
