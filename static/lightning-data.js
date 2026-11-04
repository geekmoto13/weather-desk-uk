/* CF/NetCDF flash decoding shared by the worker and validation tools. */
(function (root) {
  "use strict";
  const finite = (x) => typeof x === "number" && Number.isFinite(x);
  const scalar = (value) =>
    Array.isArray(value) || ArrayBuffer.isView(value) ? value[0] : value;
  const attr = (dataset, name) => scalar(dataset.attrs?.[name]?.value);
  function column(file, name) {
    if (!file.keys().includes(name))
      throw Error(`Missing satellite variable: ${name}`);
    const dataset = file.get(name),
      values = dataset.value;
    if (
      !(Array.isArray(values) || ArrayBuffer.isView(values)) ||
      values.length > 150000
    )
      throw Error("Unrecognised satellite array");
    const scale = attr(dataset, "scale_factor") ?? 1,
      offset = attr(dataset, "add_offset") ?? 0;
    if (!finite(scale) || !finite(offset))
      throw Error("Invalid packed satellite values");
    const fill = attr(dataset, "_FillValue"),
      missing = attr(dataset, "missing_value");
    const range = dataset.attrs?.valid_range?.value;
    return {
      length: values.length,
      units: attr(dataset, "units"),
      at(index) {
        const raw = Number(values[index]);
        if (
          !finite(raw) ||
          raw === fill ||
          raw === missing ||
          (range?.length === 2 && (raw < range[0] || raw > range[1]))
        )
          return null;
        return raw * scale + offset;
      },
    };
  }
  function timeBase(units) {
    if (typeof units !== "string") throw Error("Missing satellite time units");
    const match = /^(seconds|milliseconds|microseconds) since (.+)$/i.exec(
      units.trim(),
    );
    if (!match) throw Error("Unsupported satellite time units");
    let text = match[2]
      .trim()
      .replace(/\s+(UTC|GMT)$/i, "Z")
      .replace(" ", "T");
    if (!/(Z|[+-]\d\d:?\d\d)$/i.test(text)) text += "Z";
    const base = Date.parse(text) / 1000;
    if (!finite(base)) throw Error("Unreadable satellite time origin");
    return {
      base,
      multiplier:
        match[1].toLowerCase() === "seconds"
          ? 1
          : match[1].toLowerCase() === "milliseconds"
            ? 0.001
            : 0.000001,
    };
  }
  function distanceBearing(lat, lon, otherLat, otherLon) {
    const p = (lat * Math.PI) / 180,
      q = (otherLat * Math.PI) / 180,
      d = ((otherLon - lon) * Math.PI) / 180;
    const a =
      Math.sin((q - p) / 2) ** 2 +
      Math.cos(p) * Math.cos(q) * Math.sin(d / 2) ** 2;
    const distanceKm =
      6371.0088 * 2 * Math.asin(Math.sqrt(Math.max(0, Math.min(1, a))));
    const bearingDegrees =
      distanceKm < 0.000001
        ? null
        : ((Math.atan2(
            Math.sin(d) * Math.cos(q),
            Math.cos(p) * Math.sin(q) - Math.sin(p) * Math.cos(q) * Math.cos(d),
          ) *
            180) /
            Math.PI +
            360) %
          360;
    const directions = [
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
    return {
      distanceKm,
      bearingDegrees,
      direction:
        bearingDegrees === null
          ? "At location"
          : directions[Math.floor((bearingDegrees + 11.25) / 22.5) % 16],
    };
  }
  function decode(file, metadata, location) {
    const noaa = metadata.provider === "noaa";
    const lat = column(file, noaa ? "flash_lat" : "latitude"),
      lon = column(file, noaa ? "flash_lon" : "longitude");
    const time = column(
      file,
      noaa ? "flash_time_offset_of_first_event" : "flash_time",
    );
    if (lat.length !== lon.length || lat.length !== time.length)
      throw Error("Satellite flash arrays do not match");
    const clock = timeBase(time.units);
    const quality = noaa
      ? column(file, "flash_quality_flag")
      : file.keys().includes("flash_filter_confidence")
        ? column(file, "flash_filter_confidence")
        : null;
    if (quality && quality.length !== time.length)
      throw Error("Satellite quality array does not match");
    let qualityWarning = false;
    if (!noaa)
      for (const name of [
        "l1b_missing_warning",
        "l1b_geolocation_warning",
        "l1b_radiometric_warning",
      ]) {
        if (
          file.keys().includes(name) &&
          Number(scalar(file.get(name).value)) !== 0
        )
          qualityWarning = true;
      }
    const flashes = [];
    let rejected = 0;
    for (let i = 0; i < lat.length; i++) {
      const latitude = lat.at(i),
        longitude = lon.at(i),
        offset = time.at(i),
        q = quality?.at(i);
      if (
        !finite(latitude) ||
        !finite(longitude) ||
        !finite(offset) ||
        Math.abs(latitude) > 90 ||
        Math.abs(longitude) > 180 ||
        (quality && (!finite(q) || (noaa ? q !== 0 : q > 0.5 || q < 0)))
      ) {
        rejected++;
        continue;
      }
      const timestamp = clock.base + offset * clock.multiplier;
      if (
        !finite(timestamp) ||
        timestamp < metadata.start - 120 ||
        timestamp > metadata.end + 120
      ) {
        rejected++;
        continue;
      }
      const vector = distanceBearing(
        location.latitude,
        location.longitude,
        latitude,
        longitude,
      );
      if (vector.distanceKm > 75) continue;
      flashes.push({
        latitude,
        longitude,
        timestamp,
        ...vector,
        kind: "Satellite flash",
      });
    }
    // If a nonempty file contains no usable geolocation/time/quality rows,
    // do not convert that schema/data failure into an empty observation.
    if (lat.length > 0 && rejected === lat.length)
      throw Error("Satellite file contains no usable flash observations");
    return { flashes, qualityWarning, rejected, total: lat.length };
  }
  function summarize(results, catalogue) {
    const all = [],
      seen = new Set();
    let decoded = 0,
      qualityWarnings = 0;
    for (const file of catalogue.files) {
      const parsed = results.get(file.id);
      if (!parsed) continue;
      decoded++;
      if (parsed.qualityWarning) qualityWarnings++;
      for (const flash of parsed.flashes) {
        if (
          flash.timestamp < catalogue.windowStart ||
          flash.timestamp > catalogue.windowEnd
        )
          continue;
        const key = `${flash.timestamp.toFixed(3)}/${flash.latitude.toFixed(5)}/${flash.longitude.toFixed(5)}`;
        if (seen.has(key)) continue;
        seen.add(key);
        all.push(flash);
      }
    }
    all.sort(
      (a, b) => a.distanceKm - b.distanceKm || b.timestamp - a.timestamp,
    );
    return {
      closest: all[0] || null,
      flashCount: all.length,
      decoded,
      total: catalogue.files.length,
      qualityWarnings,
      complete:
        decoded === catalogue.files.length &&
        catalogue.coverageComplete &&
        qualityWarnings === 0,
    };
  }
  const exported = { decode, column, timeBase, distanceBearing, summarize };
  if (typeof module !== "undefined" && module.exports)
    module.exports = exported;
  root.WeatherLightningData = exported;
})(typeof globalThis !== "undefined" ? globalThis : this);
