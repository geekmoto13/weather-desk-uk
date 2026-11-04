"""Met Éireann observations, point outlook and unaltered national land warnings.

Observations use the published EDR 60-minute collection. Times are UTC, rainfall
is a measured hourly amount, and pressure is station pressure, not sea-level.
The point outlook uses the same public JSON endpoint as met.ie. It has no
documented stability guarantee; schema changes fail visibly, without relabelling
another provider's forecast as Met Éireann.
"""

from __future__ import annotations
import copy
import datetime as dt
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from local_store import write_private_json
from timezones import local_date, midnight, _british_changes
from uk_sources import Cache
from weather_location import distance_km

UTC = dt.timezone.utc
EDR = "https://opendata2.met.ie/edr/collections/observations-swob-nrt-60min"
WARNINGS = "https://www.met.ie/Open_Data/json/warning_IRELAND.json"
PARAMETERS = "air_temperature,relative_humidity,precipitation_amount,wind_speed_mean,wind_direction,wind_gust,air_pressure"
SOURCE_NOTE = "© Met Éireann · www.met.ie · CC BY 4.0. Raw observations are provisional. Units and display have been converted. Rain uses 00:00–24:00 UTC observing days. Station pressure is not sea-level pressure."


def stamp(value):
    try:
        value = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value.timestamp() if value.tzinfo else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def valid(value, low, high):
    return (
        isinstance(value, (float, int))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and low <= value <= high
    )


def parse_observations(raw, feels, now):
    if not isinstance(raw, dict) or raw.get("collection_id") != "observations-swob-nrt-60min":
        raise ValueError("Unrecognized Met Éireann observation response.")
    if raw.get("numberMatched", raw.get("count_total", 0)) > len(raw.get("items", [])):
        raise ValueError("The observation response was truncated.")
    grouped = {}
    for item in raw.get("items", []):
        when = stamp(item.get("observed_at"))
        if when is None or not now - 9 * 86400 <= when <= now + 300:
            continue
        # The published collection defines the A sensor. Never add duplicate
        # sensor readings together or silently use a rejected alternate sensor.
        if item.get("sensor_source") not in (None, "", "suit_a"):
            continue
        field, value = item.get("parameter_name"), item.get("value_num")
        values = grouped.setdefault(when, {})
        if field in values and values[field] != value:
            values[field] = None
        else:
            values[field] = value
    readings = []
    for when, values in sorted(grouped.items()):

        def number(key, low, high):
            v = values.get(key)
            return v if valid(v, low, high) else None

        celsius = number("air_temperature", -80, 60)
        if celsius is None:
            continue
        temperature = celsius * 1.8 + 32
        humidity = number("relative_humidity", 0, 100)
        wind, gust = number("wind_speed_mean", 0, 250), number("wind_gust", 0, 300)
        wind = wind * 1.150779448 if wind is not None else None
        gust = gust * 1.150779448 if gust is not None else None
        dew = None
        if humidity is not None and humidity > 0:
            gamma = math.log(humidity / 100) + 17.625 * celsius / (243.04 + celsius)
            dew = 243.04 * gamma / (17.625 - gamma) * 1.8 + 32
        feels_f, method = feels(temperature, humidity, wind)
        readings.append(
            {
                "timestamp": when,
                "tempF": temperature,
                "feelsF": feels_f,
                "feelsSource": method,
                "dewF": dew,
                "dewSource": "calculated from measured temperature and humidity",
                "humidity": humidity,
                "windMph": wind,
                "gustMph": gust,
                "windDeg": number("wind_direction", 0, 360),
                "maxGustMph": None,
                "pressureHpa": number("air_pressure", 800, 1100),
                "pressureKind": "station",
                "rainLastHourMm": number("precipitation_amount", 0, 3000),
                "rainDayMm": None,
                "rainEventMm": None,
                "rainWeekMm": None,
                "rainMonthMm": None,
                "rainYearMm": None,
                "rainRateMmHr": None,
                "solar": None,
                "uv": None,
            }
        )
    return readings


def rain_ledger(readings, station, now):
    # Hourly accumulations are labelled by their ending UTC hour. 00:00 is
    # therefore the previous day's final interval, never the next day's rain.
    rain = {
        r["timestamp"]: r["rainLastHourMm"]
        for r in readings
        if valid(r.get("rainLastHourMm"), 0, 3000)
    }
    through = max(rain, default=None)
    today = local_date(now, "UTC")
    end = math.floor(now / 3600) * 3600
    if through is not None:
        end = min(end, through)
    starts = {
        "day": today,
        "week": today - dt.timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "year": today.replace(month=1, day=1),
    }
    periods = {}
    for name, day in starts.items():
        start = midnight(day, "UTC")
        expected = max(0, int((end - start) / 3600))
        values = [rain.get(start + i * 3600) for i in range(1, expected + 1)]
        present = [v for v in values if v is not None]
        periods[name] = {
            "mm": sum(present) if present else None,
            "complete": expected > 0 and len(present) == expected,
            "missing": expected - len(present),
            "expected": expected,
            "intervalMinutes": 60,
        }
        if name in ("month", "year") and not periods[name]["complete"]:
            periods[name]["measuredSubtotalMm"] = periods[name]["mm"]
            periods[name]["mm"] = None
    periods["event"] = {"mm": None, "complete": False, "missing": 0}
    return {
        **station,
        "source": "Met Éireann",
        "providerLabel": "Met Éireann",
        "intervalMinutes": 60,
        "year": today.year,
        "timestamp": through,
        "periods": periods,
        "months": [],
        "rateMmHr": None,
        "stale": through is None or now - through > 5400,
        "note": "Measured hourly rain. Totals use UTC calendar boundaries, matching the Met Éireann daily archive. ≥ is only a measured subtotal: missing hours are not zero. Month/YTD remain incomplete unless every hour is present. Rain-event totals and instantaneous rates are not supplied.",
        "sourceUrl": "https://opendata2.met.ie/obs_public/",
    }


class IrishPublicWeather:
    def __init__(self, directory, fetch, feels, trend):
        self.directory = Path(directory) / "met-eireann"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fetch, self.feels, self.trend = fetch, feels, trend
        self.states, self.lock = {}, threading.RLock()
        self.catalogue = Cache(fetch)

    def _state(self, settings):
        key = f"{settings['latitude']:.4f},{settings['longitude']:.4f}"
        if key not in self.states:
            saved = None
            try:
                candidate = json.loads((self.directory / (key + ".json")).read_text())
                if (
                    candidate.get("locationKey") == key
                    and candidate.get("source") == "met-eireann"
                    and valid(candidate.get("current", {}).get("rainLastHourMm"), 0, 3000)
                ):
                    saved = candidate
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            self.states[key] = {
                "payload": saved,
                "loading": False,
                "checked": -math.inf,
                "error": None,
            }
        return key, self.states[key]

    def snapshot(self, settings):
        with self.lock:
            key, state = self._state(settings)
            if not state["loading"] and time.monotonic() - state["checked"] >= 600:
                state["loading"], state["checked"] = True, time.monotonic()
                threading.Thread(
                    target=self._load, args=(key, dict(settings), state), daemon=True
                ).start()
            value = copy.deepcopy(state["payload"] or {})
            now = time.time()
            current = value.get("current")
            stale = bool(
                state["error"]
                or not current
                or not valid(current.get("timestamp"), now - 5400, now + 300)
            )
            value.update(
                source="met-eireann",
                demo=False,
                loading=state["loading"],
                stale=stale,
                error=state["error"],
                rainGaugeRequired=True,
                staleAfterSeconds=5400,
                publicSourceNote=SOURCE_NOTE,
            )
            if value.get("rain"):
                value["rain"]["stale"] = stale
            return value

    def _load(self, key, settings, state):
        try:
            locations = self.catalogue.get(
                "locations", 86400, lambda: self.fetch(EDR + "/locations?f=json")
            )
            candidates = []
            for feature in locations.get("features", []):
                prop = feature.get("properties", {})
                point = feature.get("geometry", {}).get("coordinates", [])
                sid = str(prop.get("station_id", ""))
                if (
                    not sid.isdigit()
                    or len(point) != 2
                    or not valid(point[0], -11, -5)
                    or not valid(point[1], 51, 56)
                ):
                    continue
                distance = distance_km(
                    settings["latitude"], settings["longitude"], point[1], point[0]
                )
                if distance <= 150:
                    candidates.append((distance, sid, prop))
            now = time.time()
            start = dt.datetime.fromtimestamp(now - 8 * 86400, UTC).strftime("%Y-%m-%dT%H:00:00Z")
            finish = dt.datetime.fromtimestamp(now, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            for distance, sid, prop in sorted(candidates)[:6]:
                try:
                    raw = self.fetch(
                        EDR
                        + "/locations/"
                        + sid.zfill(4)
                        + "?"
                        + urlencode(
                            {
                                "datetime": start + "/" + finish,
                                "parameter-name": PARAMETERS,
                                "f": "json",
                                "limit": 10000,
                            }
                        )
                    )
                    if str(raw.get("station_id")) != str(int(sid)):
                        continue
                    history = parse_observations(raw, self.feels, now)
                    usable = [
                        r
                        for r in history
                        if now - 5400 <= r["timestamp"] <= now + 300
                        and valid(r.get("rainLastHourMm"), 0, 3000)
                    ]
                    if not usable:
                        continue
                    current = dict(usable[-1])
                    station = {
                        "stationId": "MET-" + sid.zfill(4),
                        "stationName": prop.get("station_name", "Met Éireann station"),
                        "distanceKm": round(distance, 1),
                    }
                    # Keep this exact station's measured hourly history between
                    # refreshes/restarts. Never combine different gauges.
                    with self.lock:
                        old = copy.deepcopy(state.get("payload") or {})
                    if old.get("stationId") == station["stationId"]:
                        combined = {
                            r["timestamp"]: r
                            for r in old.get("history", [])
                            if isinstance(r, dict)
                            and valid(r.get("timestamp"), now - 370 * 86400, now + 300)
                        }
                        combined.update({r["timestamp"]: r for r in history})
                        history = [combined[t] for t in sorted(combined)]
                    rain = rain_ledger(history, station, now)
                    for period, field in [
                        ("day", "rainDayMm"),
                        ("week", "rainWeekMm"),
                        ("month", "rainMonthMm"),
                        ("year", "rainYearMm"),
                    ]:
                        p = rain["periods"][period]
                        current[field] = p["mm"] if p["complete"] and not rain["stale"] else None
                    day_start = midnight(local_date(now, "UTC"), "UTC")
                    gusts = [
                        r["gustMph"]
                        for r in history
                        if day_start < r["timestamp"] <= now and r["gustMph"] is not None
                    ]
                    current["maxGustMph"] = max(gusts) if gusts else None
                    value = {
                        **station,
                        "locationKey": key,
                        "source": "met-eireann",
                        "current": current,
                        "history": history,
                        "rain": rain,
                        "trend": self.trend(current, history),
                        "checkedAt": now,
                    }
                    write_private_json(self.directory / (key + ".json"), value)
                    with self.lock:
                        state["payload"], state["error"] = value, None
                    return
                except Exception:
                    continue
            raise ValueError(
                "No recent Met Éireann station with measured rain and temperature was available within 150 km. Retrying automatically."
            )
        except Exception as exc:
            with self.lock:
                state["error"] = str(exc)
        finally:
            with self.lock:
                state["loading"] = False

    def history(self, settings):
        value = self.snapshot(settings)
        return {
            "source": "met-eireann",
            "demo": False,
            "points": [
                r for r in value.get("history", []) if r["timestamp"] >= time.time() - 86400
            ],
            "error": value.get("error"),
            "stale": value["stale"],
        }


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("p", "li", "ul", "div", "br"):
            self.parts.append("\n")


def warning_text(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def parse_warnings(raw, now):
    if not isinstance(raw, list):
        raise ValueError("Unreadable Met Éireann warning feed.")
    alerts = []
    for item in raw:
        expiry, onset, issued = (
            stamp(item.get("expiry")),
            stamp(item.get("onset")),
            stamp(item.get("issued")),
        )
        if expiry is None or issued is None:
            raise ValueError("The Met Éireann warning feed has an unreadable validity time.")
        if expiry <= now or str(item.get("status", "")).lower() in (
            "cancelled",
            "canceled",
            "cancel",
        ):
            continue
        level = str(item.get("level", "")).lower()
        alerts.append(
            {
                "id": str(item.get("capId", item.get("id"))),
                "event": item.get("headline", "Met Éireann warning"),
                "headline": item.get("headline", ""),
                "description": warning_text(item.get("description")),
                "level": level if level in ("yellow", "orange", "red") else "advisory",
                "severity": item.get("severity", ""),
                "certainty": item.get("certainty", ""),
                "sender": "Met Éireann",
                "area": "See warning headline — all Republic of Ireland warnings are shown",
                "sentAt": issued,
                "onsetAt": onset,
                "endsAt": expiry,
                "expiresAt": expiry,
                "validUntil": expiry,
            }
        )
    return sorted(
        alerts, key=lambda a: ({"red": 0, "orange": 1, "yellow": 2}.get(a["level"], 3), a["sentAt"])
    )


def wall_candidates(day, clock):
    """Interpret Irish local civil time, including the repeated autumn hour."""
    wall = dt.datetime.fromisoformat(day + "T" + clock).replace(tzinfo=UTC).timestamp()
    spring, autumn = _british_changes(dt.datetime.fromtimestamp(wall, UTC).year)
    summer_start = midnight(spring, "UTC") + 3600
    summer_end = midnight(autumn, "UTC") + 3600
    return sorted(
        t
        for offset in (0, 3600)
        if (t := wall - offset) >= 0 and int(summer_start <= t < summer_end) * 3600 == offset
    )


def parse_forecast(raw, now):
    if not isinstance(raw, list):
        raise ValueError("Unrecognized Met Éireann point forecast.")
    periods, previous = [], -math.inf
    for item in raw:
        candidates = wall_candidates(item["date"], item["time"])
        if (
            len(candidates) > 1
            and sum(r.get("date") == item["date"] and r.get("time") == item["time"] for r in raw)
            != 2
        ):
            raise ValueError("Met Éireann forecast time is ambiguous during the clock change.")
        candidates = [t for t in candidates if t > previous]
        if not candidates:
            continue
        end = candidates[0]
        previous = end
        summary = item.get("weatherDescription")
        if not isinstance(summary, str) or not summary.strip():
            raise ValueError("Met Éireann forecast has no weather description.")
        # Met Éireann documents each symbol as the preceding hour's weather.
        if now < end <= now + 90000:
            periods.append({"start": end - 3600, "end": end, "summary": summary, "pop": None})
    if not periods:
        raise ValueError("No recent Met Éireann point forecast was available.")
    return periods


class IrishOutlook:
    def __init__(self, fetch):
        self.fetch, self.cache = fetch, Cache(fetch)

    def warnings(self, lat, lon):
        try:
            data = self.cache.get(
                "warnings", 60, lambda: {"raw": self.fetch(WARNINGS), "checkedAt": time.time()}
            )
            return {
                "source": "Met Éireann",
                "scope": "All Republic of Ireland land warnings",
                "point": {"latitude": lat, "longitude": lon},
                "checkedAt": data["checkedAt"],
                "alerts": parse_warnings(data["raw"], time.time()),
                "stale": False,
                "error": None,
            }
        except Exception:
            return {
                "source": "Met Éireann",
                "point": {"latitude": lat, "longitude": lon},
                "checkedAt": None,
                "alerts": [],
                "stale": True,
                "error": "Met Éireann warnings could not be confirmed. Check met.ie/warnings-today.",
            }

    def forecast(self, lat, lon):
        def load():
            now = time.time()
            raw = self.fetch(f"https://prodapi.met.ie/weather/details/{lat:.4f}/{lon:.4f}")
            return {
                "source": "Met Éireann",
                "sourceUrl": "https://www.met.ie/forecasts",
                "demo": False,
                "periods": parse_forecast(raw, now),
                "issuedAt": now,
                "fetchedAt": now,
                "timeBasis": "retrieved",
                "stale": False,
                "error": None,
            }

        try:
            return self.cache.get(("forecast", lat, lon), 900, load)
        except Exception:
            return {
                "source": "Met Éireann",
                "sourceUrl": "https://www.met.ie/forecasts",
                "demo": False,
                "periods": [],
                "issuedAt": None,
                "fetchedAt": None,
                "stale": True,
                "error": "Met Éireann point forecast could not refresh. The beacon is unlit; check the official forecast.",
            }
