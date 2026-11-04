/* Optional public PWS credentials; observation requests stay on the local server. */
(function (root) {
  "use strict";
  function create({ api, onChange }) {
    const $ = (id) => document.getElementById(id);
    let connection = {}, pending = false;
    function sync(value) {
      if (value) connection = value;
      $("rememberWunderground").checked = connection.configured
        ? !!connection.saved : true;
      $("wundergroundStatus").textContent = connection.notice || (
        connection.configured
          ? `Weather Underground key ${connection.saved ? "saved on this computer" : "set for this session"}. API access is checked during the next public-station search.`
          : "Optional: no Weather Underground key is set. CWOP and official stations remain available."
      );
      $("wundergroundForgetBtn").disabled = pending || !connection.configured;
    }
    async function refresh() {
      try { sync(await api("/api/public-stations")); }
      catch { $("wundergroundStatus").textContent = "Public-station settings could not load. Retry when the local server is available."; }
    }
    async function update(action) {
      if (pending) return;
      pending = true;
      $("wundergroundSaveBtn").disabled = true;
      $("wundergroundForgetBtn").disabled = true;
      try {
        const value = await api("/api/public-stations/settings", {
          action, apiKey: action === "connect" ? $("wundergroundKey").value : "",
          remember: $("rememberWunderground").checked,
        });
        $("wundergroundKey").value = "";
        sync(value);
        await onChange?.();
      } catch (error) {
        $("wundergroundStatus").textContent = error.message;
      } finally {
        pending = false;
        $("wundergroundSaveBtn").disabled = false;
        $("wundergroundForgetBtn").disabled = !connection.configured;
      }
    }
    $("wundergroundSaveBtn").addEventListener("click", () => update("connect"));
    $("wundergroundForgetBtn").addEventListener("click", () => update("forget"));
    return { sync, refresh };
  }
  const api = { create };
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherPublicStations = api;
})(typeof window === "object" ? window : globalThis);
