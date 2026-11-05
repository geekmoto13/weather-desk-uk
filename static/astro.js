/* Low-precision solar/lunar ephemerides, calculated locally.
 * Equatorial-coordinate formulae with first-order lunar periodic terms.
 * Rise/set: horizon crossing sampled every 10 minutes, refined by bisection.
 * Solar center -0.833 deg; geocentric lunar center +0.133 deg.
 * Approximate, not a navigation ephemeris. No remote requests or dependencies.
 */
(() => {
  "use strict";
  const D = Math.PI / 180,
    day = 86400000;
  const sin = Math.sin,
    cos = Math.cos,
    atan2 = Math.atan2,
    asin = Math.asin;
  const days = (t) => t / day + 2440587.5 - 2451545;
  const eq = (lon, lat) => ({
    ra: atan2(
      sin(lon) * cos(23.4397 * D) - Math.tan(lat) * sin(23.4397 * D),
      cos(lon),
    ),
    dec: asin(
      sin(lat) * cos(23.4397 * D) + cos(lat) * sin(23.4397 * D) * sin(lon),
    ),
  });
  function sun(t) {
    const d = days(t),
      m = (357.5291 + 0.98560028 * d) * D;
    return eq(
      (280.46646 +
        0.98564736 * d +
        1.9148 * sin(m) +
        0.02 * sin(2 * m) +
        0.0003 * sin(3 * m)) *
        D,
      0,
    );
  }
  function moon(t) {
    const d = days(t),
      m = (134.963 + 13.064993 * d) * D,
      f = (93.272 + 13.22935 * d) * D;
    return {
      ...eq((218.316 + 13.176396 * d + 6.289 * sin(m)) * D, 5.128 * sin(f) * D),
      distance: 385001 - 20905 * cos(m),
    };
  }
  function altitude(t, lat, lon, body) {
    const p = body(t),
      h = (280.46061837 + 360.98564736629 * days(t) + lon) * D - p.ra;
    return (
      asin(sin(lat * D) * sin(p.dec) + cos(lat * D) * cos(p.dec) * cos(h)) / D
    );
  }
  function parts(t, tz) {
    const p = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).formatToParts(new Date(t));
    return Object.fromEntries(
      p.filter((x) => x.type !== "literal").map((x) => [x.type, +x.value]),
    );
  }
  function zonedMidnight(y, m, d, tz) {
    const target = Date.UTC(y, m - 1, d),
      n = new Date(target);
    const canonical = Date.UTC(
      n.getUTCFullYear(),
      n.getUTCMonth(),
      n.getUTCDate(),
    );
    let guess = canonical;
    for (let i = 0; i < 4; i++) {
      const p = parts(guess, tz),
        asUTC = Date.UTC(
          p.year,
          p.month - 1,
          p.day,
          p.hour,
          p.minute,
          p.second,
        );
      guess += canonical - asUTC;
    }
    return guess;
  }
  function crossings(start, end, lat, lon, body, horizon) {
    let previous = altitude(start, lat, lon, body) - horizon;
    const output = { rise: null, set: null, alwaysUp: true, alwaysDown: true };
    for (let t = start + 600000; t <= end + 600000; t += 600000) {
      const right = Math.min(t, end),
        value = altitude(right, lat, lon, body) - horizon;
      if (value < 0) output.alwaysUp = false;
      else output.alwaysDown = false;
      if ((previous < 0 && value >= 0) || (previous >= 0 && value < 0)) {
        let a = Math.max(start, right - 600000),
          b = right,
          above = previous >= 0;
        for (let i = 0; i < 18; i++) {
          const mid = (a + b) / 2;
          if (altitude(mid, lat, lon, body) >= horizon === above) a = mid;
          else b = mid;
        }
        const key = previous < 0 ? "rise" : "set";
        if (output[key] === null) output[key] = (a + b) / 2;
      }
      previous = value;
      if (right === end) break;
    }
    return output;
  }
  function illumination(t) {
    const s = sun(t),
      m = moon(t),
      phi = Math.acos(
        Math.max(
          -1,
          Math.min(
            1,
            sin(s.dec) * sin(m.dec) +
              cos(s.dec) * cos(m.dec) * cos(s.ra - m.ra),
          ),
        ),
      );
    const inc = atan2(149598000 * sin(phi), m.distance - 149598000 * cos(phi));
    const angle = atan2(
      cos(s.dec) * sin(s.ra - m.ra),
      sin(s.dec) * cos(m.dec) - cos(s.dec) * sin(m.dec) * cos(s.ra - m.ra),
    );
    return {
      fraction: (1 + cos(inc)) / 2,
      phase: 0.5 + (0.5 * inc * (angle < 0 ? -1 : 1)) / Math.PI,
    };
  }
  function calculate(t, lat, lon, tz) {
    const p = parts(t, tz),
      start = zonedMidnight(p.year, p.month, p.day, tz),
      end = zonedMidnight(p.year, p.month, p.day + 1, tz);
    return {
      sun: crossings(start, end, lat, lon, sun, -0.833),
      moon: crossings(start, end, lat, lon, moon, 0.133),
      illumination: illumination(t),
      sunAltitude: altitude(t, lat, lon, sun),
      twilight: twilight(t, lat, lon),
      start,
      end,
    };
  }
  function twilight(t, lat, lon) {
    const elevation = altitude(t, lat, lon, sun);
    const stage = elevation >= -0.8333 ? "Daylight"
      : elevation >= -6 ? "Civil twilight"
      : elevation >= -12 ? "Nautical twilight"
      : elevation >= -18 ? "Astronomical twilight" : "Night";
    const phase = stage.includes("twilight")
      ? (altitude(t + 60000, lat, lon, sun) >= altitude(t - 60000, lat, lon, sun) ? "dawn" : "dusk") : null;
    return { stage, phase, sunAltitude: elevation };
  }
  window.WeatherAstro = { calculate, zonedMidnight, altitude, sun, moon, twilight };
})();
