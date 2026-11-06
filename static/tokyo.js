(function (root) {
  "use strict";
  const labels = {
    rain: ["Water wet… ew nasty", "Water wet… ew nasty."],
    no_rain: ["It is are not raining", "It is are not raining"],
    wintry: ["Wintry precipitation", "Tokyo reports snow or ice instead."],
    nearby: ["Showers nearby", "Precipitation nearby — not at Haneda."],
    unknown: ["Rain status unclear", "Tokyo is receiving… but the rain status is unclear."],
    stale: ["Report delayed", "Tokyo’s report is getting a little old."],
    unavailable: ["Off the air", "Tokyo is off the air for the moment."],
    loading: ["Tuning in…", "Calling Tokyo…"],
  };
  function describe(data, now = Date.now()) {
    let key = "loading";
    if (data) {
      const stamp = data.observedAt;
      if (data.status === "unavailable") key = "unavailable";
      else if (!Number.isFinite(stamp) || stamp * 1000 > now + 300000) key = "unavailable";
      else if (data.status === "stale" || now - stamp * 1000 > 5400000) key = "stale";
      else key = Object.hasOwn(labels, data.condition) ? data.condition : "unknown";
    }
    return {key, short: labels[key][0], answer: labels[key][1]};
  }
  function create({api, document: doc = root.document, now = () => Date.now(),
                   every = root.setInterval?.bind(root)} = {}) {
    const find = id => doc.getElementById(id), button = find("tokyoCallBtn"), dialog = find("tokyoDialog");
    if (!button || !dialog) return null;
    let data = null, busy = false, started = false;
    const put = (id, text) => { find(id).textContent = text; };
    function render() {
      const view = describe(data, now());
      button.dataset.state = view.key;
      put("tokyoRainStatus", view.short);
      put("tokyoAnswer", view.answer);
      const stamp = Number.isFinite(data?.observedAt) ? new Intl.DateTimeFormat("en-GB", {
        timeZone: "Asia/Tokyo", year: "numeric", month: "short", day: "numeric",
        hour: "2-digit", minute: "2-digit", hourCycle: "h23",
      }).format(new Date(data.observedAt * 1000)) + " JST" : "Awaiting a Tokyo report";
      const last = ["stale", "unavailable"].includes(view.key) ? "Last received: " : "Observed: ";
      put("tokyoObserved", last + stamp);
      const temp = data?.temperatureC;
      put("tokyoTemperature", Number.isFinite(temp) ? `${temp.toFixed(1)} °C / ${(temp * 9 / 5 + 32).toFixed(1)} °F` : "Temperature unavailable");
      put("tokyoReport", data?.rawReport || "No report received yet.");
      button.title = `${view.answer} Tokyo Haneda · ${stamp}. Open for The Radio Ham.`;
      button.setAttribute("aria-label", `Is it raining in Tokyo? ${view.short}. Open Tokyo report.`);
    }
    async function refresh() {
      if (busy) return;
      busy = true;
      try { data = await api("/api/tokyo-rain"); }
      catch { data = {...data, status: "unavailable"}; }
      finally { busy = false; render(); }
    }
    button.addEventListener("click", () => { render(); dialog.showModal(); });
    find("tokyoCloseBtn").addEventListener("click", () => dialog.close());
    return {refresh, render, start() {
      if (started) return;
      started = true;
      render(); refresh(); every(refresh, 60000);
    }};
  }
  const api = {describe, create};
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.WeatherTokyo = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
