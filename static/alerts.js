/* NOAA/Met Office notices are separate from station readings and the forecast beacon. */
(function (root, factory) {
  const value = factory();
  if (typeof module === "object" && module.exports) module.exports = value;
  else root.WeatherAlerts = value;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const number = (value) => typeof value === "number" && Number.isFinite(value);
  const kind = (event) =>
    ["warning", "watch", "advisory"].find((word) =>
      String(event).toLowerCase().split(/\s+/).includes(word),
    ) || "statement";
  function pointKey(point) {
    return number(point?.latitude) && number(point?.longitude)
      ? `${point.latitude.toFixed(4)},${point.longitude.toFixed(4)}`
      : "";
  }
  function officialUrl(point) {
    if (point?.countryCode === "IE") return "https://www.met.ie/warnings";
    return "https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings";
  }
  function view(data, now = Date.now() / 1000) {
    if (!data)
      return {
        status: "loading",
        tone: "neutral",
        title: "Checking local alerts…",
        alerts: [],
        stale: false,
      };
    const stale =
      !!data.error ||
      !!data.stale ||
      !number(data.checkedAt) ||
      now - data.checkedAt > 180 ||
      data.checkedAt > now + 60;
    const tooOld =
      !number(data.checkedAt) ||
      now - data.checkedAt > 180 ||
      data.checkedAt > now + 60;
    const alerts = tooOld
      ? []
      : (data.alerts || []).filter(
          (a) => number(a.validUntil) && a.validUntil > now,
        );
    if (alerts.length) {
      return {
        status: stale ? "stale" : "active",
        tone: alerts[0].level || kind(alerts[0].event),
        stale,
        alerts,
        title: `${stale ? "Last known: " : ""}${alerts[0].event}${alerts.length > 1 ? ` + ${alerts.length - 1} more` : ""}`,
      };
    }
    return {
      status: stale ? "unavailable" : "quiet",
      tone: stale ? "neutral" : "quiet",
      stale,
      alerts,
      title: stale
        ? `${data.source === "Met Éireann" ? "Met Éireann" : "Met Office"} warnings unavailable`
        : `No active ${data.source === "Met Éireann" ? "Met Éireann" : "Met Office"} warnings`,
    };
  }
  function create({ fetchAlerts, getSettings, formatDate }) {
    const $ = (id) => document.getElementById(id);
    let data = null,
      requestedPoint = "",
      pending = null,
      revision = 0,
      started = false;
    let lastAnnouncement = "",
      dialogSignature = "";
    const label = () => getSettings()?.location || "Your configured location";
    const agency = () =>
      getSettings()?.countryCode === "IE" ? "Met Éireann" : "Met Office";
    const set = (id, value) => {
      if ($(id).textContent !== value) $(id).textContent = value;
    };
    const stamp = (value) =>
      number(value) ? formatDate(value * 1000) : "Not yet checked";
    function renderDialog(model) {
      const signature = JSON.stringify([
        data,
        model.status,
        model.alerts.map((a) => a.id),
        label(),
      ]);
      if (signature === dialogSignature) return;
      dialogSignature = signature;
      const target = $("alertsList");
      target.replaceChildren();
      set(
        "alertsDialogNote",
        `${label()} · ${number(data?.checkedAt) ? `Last successful check ${stamp(data.checkedAt)}` : `Waiting for a successful ${agency()} check`}. Checks every minute while the dashboard is open.${agency() === "Met Éireann" ? " All Republic of Ireland land warnings are shown; read each affected area in the headline." : ""}`,
      );
      const source = $("alertsOfficialLink");
      source.href = officialUrl(getSettings());
      source.textContent = `Official ${agency()} warnings ↗`;
      if (model.stale) {
        const note = document.createElement("p");
        note.className = "alerts-stale-note";
        note.textContent = `The feed is unavailable or overdue. There may be newer notices. Check the official ${agency()} warning page for the latest information.`;
        target.append(note);
      }
      if (!model.alerts.length) {
        const note = document.createElement("p");
        note.textContent =
          model.status === "quiet"
            ? `${agency()} returned no active alerts ${agency() === "Met Éireann" ? "for the Republic of Ireland" : "for these coordinates"} at the last successful check.`
            : model.status === "loading"
              ? `Contacting ${agency()}…`
              : "Current alert status could not be confirmed.";
        target.append(note);
      }
      for (const alert of model.alerts) {
        const card = document.createElement("article");
        card.className = "alert-detail";
        card.dataset.tone = alert.level || kind(alert.event);
        const heading = document.createElement("h3");
        heading.textContent = alert.event;
        card.append(heading);
        function paragraph(value, className = "") {
          if (!value) return;
          const p = document.createElement("p");
          p.className = className;
          p.textContent = value;
          card.append(p);
        }
        paragraph(alert.headline, "alert-headline");
        paragraph(
          [
            alert.sender,
            `Severity: ${alert.severity}`,
            `Urgency: ${alert.urgency}`,
            `Certainty: ${alert.certainty}`,
          ]
            .filter(Boolean)
            .join(" · "),
          "alert-meta",
        );
        paragraph(
          `Issued: ${stamp(alert.sentAt)}${number(alert.onsetAt) ? `\nStarts: ${stamp(alert.onsetAt)}` : ""}${number(alert.endsAt) ? `\nEnds: ${stamp(alert.endsAt)}` : ""}\nNotice expires: ${stamp(alert.expiresAt)}`,
          "alert-meta",
        );
        paragraph(
          alert.area ? `${agency()} affected area: ${alert.area}` : "",
          "alert-area",
        );
        paragraph(alert.description, "alert-description");
        paragraph(alert.updateDescription, "alert-description");
        paragraph(
          alert.updatedText ? `${agency()} update: ` + alert.updatedText : "",
          "alert-meta",
        );
        if (alert.instruction) {
          const instructions = document.createElement("h4");
          instructions.textContent = "What to do";
          card.append(instructions);
          paragraph(alert.instruction, "alert-instructions");
        }
        target.append(card);
      }
    }
    function render() {
      const model = view(data);
      const panel = $("weatherAlerts");
      panel.dataset.status = model.status;
      panel.dataset.tone = model.tone;
      set(
        "alertsArea",
        `${agency()} Weather Warnings · ${agency() === "Met Éireann" ? "All Republic of Ireland land warnings" : label()}`,
      );
      document.querySelectorAll(".met-office-license").forEach((element) => {
        element.hidden = agency() === "Met Éireann";
      });
      set("alertsTitle", model.title);
      $("alertsTitle").title = model.title;
      const first = model.alerts[0];
      let summary;
      if (first)
        summary = `Issued ${stamp(first.sentAt)} · ${number(first.onsetAt) && first.onsetAt > Date.now() / 1000 ? `Starts ${stamp(first.onsetAt)} · ` : ""}${number(first.endsAt) ? "Ends" : "Notice expires"} ${stamp(first.endsAt ?? first.expiresAt)}${model.stale ? " · UPDATE UNAVAILABLE" : ""}`;
      else
        summary = model.stale
          ? "Current status unconfirmed · retrying automatically"
          : model.status === "loading"
            ? "Warnings, watches and advisories for your saved location"
            : "Warnings, watches and advisories · checks every minute";
      set("alertsSummary", summary);
      set(
        "alertsChecked",
        (number(data?.checkedAt)
          ? `${model.stale ? "Last success" : "Checked"} ${stamp(data.checkedAt)}`
          : "Auto · every minute") +
          (agency() === "Met Éireann"
            ? " · © Met Éireann"
            : " · © Crown copyright " +
              new Date().getFullYear() +
              ", Met Office"),
      );
      set(
        "alertsDetailsBtn",
        model.alerts.length
          ? `View ${model.alerts.length === 1 ? "alert" : `${model.alerts.length} alerts`}`
          : "Details",
      );
      const announcement = `${model.status}:${model.alerts.map((a) => `${a.id}:${a.expiresAt}`).join(",")}`;
      if (announcement !== lastAnnouncement) {
        set("alertsLive", model.title);
        lastAnnouncement = announcement;
      }
      if ($("alertsDialog").open) renderDialog(model);
    }
    function refresh() {
      const key = pointKey(getSettings());
      if (!key) return Promise.resolve();
      if (key !== requestedPoint) {
        requestedPoint = key;
        revision++;
        data = null;
        pending = null;
        render();
      }
      if (pending) return pending;
      const currentRevision = revision;
      const request = (async () => {
        try {
          const result = await fetchAlerts();
          if (currentRevision !== revision || pointKey(getSettings()) !== key)
            return;
          if (
            !result ||
            !Array.isArray(result.alerts) ||
            pointKey(result.point) !== key
          )
            throw new Error("Unreadable alert feed");
          data = result;
        } catch (_) {
          if (currentRevision !== revision || pointKey(getSettings()) !== key)
            return;
          data = {
            ...(data || {}),
            alerts: data?.alerts || [],
            stale: true,
            source: agency(),
            error: `${agency()} alerts could not refresh.`,
          };
        } finally {
          if (currentRevision === revision) render();
        }
      })();
      pending = request;
      request.finally(() => {
        if (pending === request) pending = null;
      });
      return request;
    }
    function start() {
      if (started) return;
      started = true;
      $("alertsDetailsBtn").addEventListener("click", () => {
        renderDialog(view(data));
        $("alertsDialog").showModal();
      });
      render();
      refresh();
      setInterval(refresh, 60000);
      // Expired notices and old "no alerts" results disappear even if a request stalls.
      setInterval(render, 15000);
    }
    return { start, refresh };
  }
  return { kind, pointKey, officialUrl, view, create };
});
