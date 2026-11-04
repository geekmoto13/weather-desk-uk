/* Location search edits the settings form; Save preferences commits the choice. */
((root) => {
  "use strict";
  function create({ api, getSettings }) {
    const $ = (id) => document.getElementById(id);
    const form = $("settingsForm");
    let revision = 0;
    const note = (message, error = false) => {
      $("locationStatus").textContent = message;
      $("locationStatus").classList.toggle("failed", error);
    };
    function describe(settings) {
      const irish = settings.countryCode === "IE";
      $("providerLocationNotice").textContent = irish
        ? "Republic of Ireland · Automatic public observations, forecast, warnings and daily climate: Met Éireann."
        : "United Kingdom · Public observations: CWOP / NOAA AWC · Forecast: Open-Meteo / DWD ICON · Warnings: Met Office.";
      const site = settings.climateStation;
      $("climateLocationNotice").textContent =
        site?.available === false
          ? "Local climate reference unavailable. Try another nearby place or retry the lookup."
          : `Climate reference: ${site?.name || "Daily archive"}${typeof site?.distanceKm === "number" ? ` · ${site.distanceKm.toFixed(1)} km from your chosen location` : ""} · calculated 1991–2020 averages${irish ? " · 00:00–24:00 UTC" : ""}.`;
    }
    function reset() {
      revision++;
      $("locationToken").value = "";
      $("locationResults").replaceChildren();
      note("");
      describe(getSettings() || {});
      $("locationSearchBtn").disabled = false;
    }
    async function choose(place, token) {
      if (token !== revision) return;
      $("locationSearchBtn").disabled = true;
      $("locationResults").replaceChildren();
      note("Selecting your country's weather providers and daily reference…");
      try {
        const result = await api("/api/location/resolve", place);
        if (token !== revision) return;
        for (const [key, value] of Object.entries(result.settings))
          if (form.elements[key]) form.elements[key].value = value;
        $("locationToken").value = result.locationToken;
        describe(result.settings);
        note("Location ready. Click Save preferences to use it.");
      } catch (error) {
        if (token === revision) note(error.message, true);
      } finally {
        if (token === revision) $("locationSearchBtn").disabled = false;
      }
    }
    async function search() {
      const token = ++revision;
      $("locationSearchBtn").disabled = true;
      $("locationResults").replaceChildren();
      $("locationToken").value = "";
      note("Looking up your location…");
      try {
        const result = await api("/api/location/search", {
          query: $("locationQuery").value,
        });
        if (token !== revision) return;
        if (!result.matches?.length) {
          note(
            result.message ||
              "No match found. Try a UK or Irish town, UK postcode or grid.",
            true,
          );
          return;
        }
        note("Choose your location:");
        for (const place of result.matches) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "location-result";
          button.textContent = place.name;
          button.addEventListener("click", () => choose(place, token));
          $("locationResults").append(button);
        }
      } catch (error) {
        if (token === revision) note(error.message, true);
      } finally {
        if (token === revision) $("locationSearchBtn").disabled = false;
      }
    }
    $("locationSearchBtn").addEventListener("click", search);
    $("locationQuery").addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        search();
      }
    });
    return { reset };
  }
  root.WeatherLocation = { create };
})(typeof window === "object" ? window : globalThis);
