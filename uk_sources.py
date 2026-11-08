"""Keyless UK forecast, real METAR observations, warnings and radar metadata."""

from __future__ import annotations
from forecast_values import numeric
import copy
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from weather_location import distance_km
from rain_observations import has_measured_rain

WARNINGS_PAGE = "https://weather.metoffice.gov.uk/warnings-and-advice/uk-warnings"
WARNINGS_API = (
    "https://services.arcgis.com/Lq3V5RFuTBC9I7kv/arcgis/rest/services/Met_Office_National_Severe_Weather_Warning_Service_Live/FeatureServer/0/query?"
    + urlencode({"where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson"})
)


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


class Cache:
    def __init__(self, fetch):
        self.fetch = fetch
        self.items = {}
        self.lock = threading.RLock()

    def get(self, key, ttl, work):
        with self.lock:
            cached = self.items.get(key)
            if cached and time.monotonic() - cached[0] < ttl:
                return copy.deepcopy(cached[1])
            value = work()
            self.items[key] = (time.monotonic(), value)
            return copy.deepcopy(value)


def metar_reading(row, feels):
    temp, dew = number(row.get("temp")), number(row.get("dewp"))
    tf = temp * 1.8 + 32 if temp is not None else None
    df = dew * 1.8 + 32 if dew is not None else None
    humidity = (
        min(
            100,
            max(0, 100 * math.exp(17.625 * dew / (243.04 + dew) - 17.625 * temp / (243.04 + temp))),
        )
        if temp is not None and dew is not None
        else None
    )
    speed = number(row.get("wspd"))
    speed = speed * 1.150779448 if speed is not None else None
    gust = number(row.get("wgst"))
    gust = gust * 1.150779448 if gust is not None else None
    value, method = feels(tf, humidity, speed)
    slp, qnh = number(row.get("slp")), number(row.get("altim"))
    result = {
        "timestamp": number(row.get("obsTime")),
        "tempF": tf,
        "dewF": df,
        "humidity": humidity,
        "feelsF": value,
        "feelsSource": method,
        "windMph": speed,
        "windMean10mMph": speed if str(row.get("icaoId", "")).startswith(("EG", "EI")) else None,
        "windMean10mSource": "UK/Ireland METAR reported mean (ten-minute convention)",
        "windDeg": number(row.get("wdir")),
        "gustMph": gust,
        "maxGustMph": None,
        "pressureHpa": slp if slp is not None else qnh,
        "pressureKind": "sea-level" if slp is not None else "QNH",
        "rainDayMm": None,
        "rainEventMm": None,
        "rainWeekMm": None,
        "rainMonthMm": None,
        "rainYearMm": None,
        "rainRateMmHr": None,
        "solar": None,
        "uv": None,
        "stationTimezone": "Europe/London",
    }
    # AWC publishes these measured accumulation amounts in inches. Weather codes
    # (RA, SHRA, etc.) are not numerical rain-gauge measurements.
    for field, target in [
        ("precip", "rainLastHourMm"),
        ("pcp3hr", "rainLast3hMm"),
        ("pcp6hr", "rainLast6hMm"),
        ("pcp24hr", "rainLast24hMm"),
    ]:
        amount = number(row.get(field))
        result[target] = (
            amount * 25.4 if amount is not None and 0 <= amount * 25.4 <= 3000 else None
        )
    return result


class UKPublicWeather:
    def __init__(self, directory, fetch, resolver, feels, trend):
        self.fetch = fetch
        self.cache = Cache(fetch)
        self.feels = feels
        self.trend = trend
        self.directory = Path(directory) / "uk-public"
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.saved = {}

    def snapshot(self, settings):
        lat, lon = settings["latitude"], settings["longitude"]
        key = f"{lat:.4f},{lon:.4f}"

        def work():
            now = time.time()
            candidates = []
            for radius in (1.2, 2.5):
                wide = radius / max(0.3, math.cos(math.radians(lat)))
                url = "https://aviationweather.gov/api/data/metar?" + urlencode(
                    {
                        "bbox": f"{lat-radius:.4f},{lon-wide:.4f},{lat+radius:.4f},{lon+wide:.4f}",
                        "format": "json",
                        "hours": 2,
                    }
                )
                rows = self.fetch(url)
                if not isinstance(rows, list):
                    raise ValueError("The public station feed was unreadable.")
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    stamp = number(row.get("obsTime"))
                    a, b = number(row.get("lat")), number(row.get("lon"))
                    if (
                        stamp is None
                        or a is None
                        or b is None
                        or stamp > now + 300
                        or now - stamp > 5400
                        or number(row.get("temp")) is None
                    ):
                        continue
                    current = metar_reading(row, self.feels)
                    if not has_measured_rain(current):
                        continue
                    d = distance_km(lat, lon, a, b)
                    if d <= 250:
                        candidates.append((d, -stamp, row))
                if candidates:
                    break
            if not candidates:
                raise ValueError(
                    "No recent airport observation with measured rainfall was found within 250 km."
                )
            d, _, row = min(candidates, key=lambda r: (r[0], r[1]))
            current = metar_reading(row, self.feels)
            previous = self.saved.get(key, {})
            points = previous.get("points", [])
            if previous.get("stationId") != row["icaoId"]:
                points = []
            points = {p["timestamp"]: p for p in points if p.get("timestamp", 0) > now - 86400
                      and p.get("pressureKind") == current.get("pressureKind")}
            archive = []
            if current.get("pressureHpa") is not None and self.trend(current, list(points.values())).get("delta") is None:
                try:
                    # Query only the selected airport; keep nearby discovery small.
                    archive = self.fetch("https://aviationweather.gov/api/data/metar?" + urlencode({
                        "ids": row["icaoId"], "format": "json", "hours": 5,
                    }))
                except Exception:
                    pass
            for older in archive if isinstance(archive, list) else []:
                if not isinstance(older, dict) or older.get("icaoId") != row["icaoId"]:
                    continue
                old_lat, old_lon = number(older.get("lat")), number(older.get("lon"))
                if old_lat is None or old_lon is None or distance_km(old_lat, old_lon, row["lat"], row["lon"]) > 1:
                    continue
                point = metar_reading(older, self.feels)
                if (point.get("timestamp") is not None
                        and abs(point["timestamp"] - (current["timestamp"] - 10800)) <= 1800
                        and point.get("pressureKind") == current.get("pressureKind")
                        and point.get("pressureHpa") is not None):
                    points[point["timestamp"]] = point
            points[current["timestamp"]] = current
            points = [points[k] for k in sorted(points)]
            value = {
                "demo": False,
                "source": "metar",
                "stationName": row.get("name", row["icaoId"]),
                "stationId": row["icaoId"],
                "distanceKm": round(d, 1),
                "current": current,
                "points": points,
                "trend": self.trend(current, points),
                "fetchedAt": now,
                "stale": now - current["timestamp"] > 5400,
                "staleAfterSeconds": 5400,
                "rainGaugeRequired": True,
                "loading": False,
                "error": None,
            }
            self.saved[key] = value
            try:
                (self.directory / (key + ".json")).write_text(json.dumps(value))
                (self.directory / (key + ".json")).chmod(0o600)
            except OSError:
                pass
            return value

        try:
            value = self.cache.get(key, 300, work)
            if not has_measured_rain(value.get("current")):
                raise ValueError("The airport observation has no measured rainfall report.")
            return value
        except Exception as e:
            saved = self.saved.get(key)
            if saved is None:
                try:
                    saved = json.loads((self.directory / (key + ".json")).read_text())
                except (OSError, ValueError):
                    saved = {}
            if not has_measured_rain(saved.get("current")):
                saved = {}
            return {
                **saved,
                "demo": False,
                "source": "metar",
                "loading": False,
                "stale": True,
                "rainGaugeRequired": True,
                "error": (
                    str(e)
                    if isinstance(e, ValueError)
                    else "The public airport feed could not refresh. Retrying shortly."
                ),
            }

    def history(self, settings):
        value = self.snapshot(settings)
        return {
            "demo": False,
            "points": value.get("points", []),
            "trend": value.get("trend"),
            "error": value.get("error"),
        }


WEATHER = {
    0: "Clear",
    1: "Mostly Clear",
    2: "Partly Cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing Fog",
    51: "Light Drizzle",
    53: "Drizzle",
    55: "Heavy Drizzle",
    56: "Freezing Rain",
    57: "Freezing Rain",
    61: "Light Rain",
    63: "Rain",
    65: "Heavy Rain",
    66: "Freezing Rain",
    67: "Freezing Rain",
    71: "Light Snow",
    73: "Snow",
    75: "Heavy Snow",
    77: "Snow Grains",
    80: "Rain Showers",
    81: "Rain Showers",
    82: "Heavy Rain Showers",
    85: "Snow Showers",
    86: "Heavy Snow Showers",
    95: "Thunderstorms",
    96: "Thunderstorms With Hail",
    99: "Thunderstorms With Hail",
}


class UKForecast:
    def __init__(self, fetch):
        self.fetch = fetch
        self.cache = Cache(fetch)

    def get(self, lat, lon):
        def work():
            params = {
                "latitude": lat,
                "longitude": lon,
                "models": "icon_seamless",
                "timezone": "Europe/London",
                "timeformat": "unixtime",
                "forecast_days": 4,
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "hourly": "weather_code,precipitation_probability,temperature_2m,wind_speed_10m",
            }
            raw = self.fetch("https://api.open-meteo.com/v1/forecast?" + urlencode(params))
            now = time.time()
            h = raw["hourly"]
            rows = []
            def sample(name, i, low, high):
                values = h.get(name, [])
                return numeric(values[i], low, high) if i < len(values) else None
            for i, start in enumerate(h["time"]):
                if (
                    not isinstance(start, (int, float))
                    or start + 3600 <= now
                    or start >= now + 49 * 3600
                ):
                    continue
                code = h["weather_code"][i]
                chance = h.get("precipitation_probability", [])
                rows.append(
                    {
                        "start": start,
                        "end": start + 3600,
                        "summary": WEATHER.get(code, f"Unknown forecast code {code}"),
                        "pop": numeric(chance[i], 0, 100) if i < len(chance) else None,
                        "tempF": sample("temperature_2m", i, -150, 180),
                        "windMph": sample("wind_speed_10m", i, 0, 300),
                    }
                )
            if not rows:
                raise ValueError("No hourly forecast was available.")
            return {
                "demo": False,
                "issuedAt": now,
                "fetchedAt": now,
                "timeBasis": "retrieved",
                "periods": rows,
                "stale": False,
                "error": None,
                "source": "Open-Meteo / DWD ICON",
            }

        try:
            return self.cache.get((lat, lon), 900, work)
        except Exception:
            return {
                "demo": False,
                "periods": [],
                "issuedAt": None,
                "stale": True,
                "error": "The UK forecast could not refresh. The beacon is unlit.",
            }


def ring_contains(ring, x, y):
    inside = False
    for a, b in zip(ring, ring[1:] + ring[:1]):
        ax, ay = a[:2]
        bx, by = b[:2]
        cross = (x - ax) * (by - ay) - (y - ay) * (bx - ax)
        if (
            abs(cross) < 1e-10
            and min(ax, bx) - 1e-10 <= x <= max(ax, bx) + 1e-10
            and min(ay, by) - 1e-10 <= y <= max(ay, by) + 1e-10
        ):
            return True
        if (ay > y) != (by > y) and x < (bx - ax) * (y - ay) / (by - ay) + ax:
            inside = not inside
    return inside


def contains(geometry, lat, lon):
    if geometry.get("type") == "Polygon":
        polys = [geometry["coordinates"]]
    elif geometry.get("type") == "MultiPolygon":
        polys = geometry["coordinates"]
    else:
        raise ValueError("Warning coverage could not be verified.")
    return any(
        rings
        and ring_contains(rings[0], lon, lat)
        and not any(ring_contains(h, lon, lat) for h in rings[1:])
        for rings in polys
    )


def parse_warnings(raw, lat, lon, now):
    if (
        not isinstance(raw, dict)
        or raw.get("type") != "FeatureCollection"
        or not isinstance(raw.get("features"), list)
        or raw.get("exceededTransferLimit")
    ):
        raise ValueError("Incomplete warning feed.")
    result = {}
    for feature in raw["features"]:
        p = feature["properties"]
        end = number(p.get("validtodate"))
        start = number(p.get("validfromdate"))
        issued = number(p.get("issuedDate"))
        if end is None or start is None or issued is None:
            raise ValueError("Unreadable warning dates.")
        if end / 1000 <= now or not contains(feature.get("geometry") or {}, lat, lon):
            continue
        level = str(p.get("warninglevel", "")).title()
        if level not in ("Red", "Amber", "Yellow"):
            raise ValueError("Unknown warning level.")
        identifier = str(p.get("GlobalID") or p.get("OBJECTID"))
        result[identifier] = {
            "id": identifier,
            "event": f'{level} {p.get("weathertype","").lower()} warning',
            "kind": "warning",
            "level": level.lower(),
            "headline": p.get("warningheadline", ""),
            "description": p.get("warningFurtherDetails", ""),
            "instruction": p.get("whatToExpect", ""),
            "area": p.get("affectedAreas", ""),
            "updateDescription": p.get("warningUpdateDescription", ""),
            "updatedText": p.get("modifiedDateString", ""),
            "sender": "Met Office Weather Warnings",
            "severity": level,
            "urgency": "Expected" if start / 1000 > now else "Immediate",
            "certainty": p.get("warningLikelihood", ""),
            "sentAt": issued / 1000,
            "onsetAt": start / 1000,
            "endsAt": end / 1000,
            "expiresAt": end / 1000,
            "validUntil": end / 1000,
        }
    return sorted(
        result.values(),
        key=lambda r: ({"red": 0, "amber": 1, "yellow": 2}[r["level"]], r["sentAt"]),
    )


class UKWarnings:
    def __init__(self, fetch):
        self.fetch = fetch
        self.cache = Cache(fetch)
        self.saved = {}

    def get(self, lat, lon):
        key = (lat, lon)
        now = time.time()
        try:
            snapshot = self.cache.get(
                "all", 60, lambda: {"raw": self.fetch(WARNINGS_API), "checkedAt": time.time()}
            )
            alerts = parse_warnings(snapshot["raw"], lat, lon, now)
            value = {
                "source": "Met Office Weather Warnings",
                "point": {"latitude": lat, "longitude": lon},
                "checkedAt": snapshot["checkedAt"],
                "alerts": alerts,
                "stale": False,
                "error": None,
            }
            self.saved[key] = value
            return value
        except Exception:
            old = copy.deepcopy(self.saved.get(key, {}))
            if now - old.get("checkedAt", 0) > 180:
                old["alerts"] = []
            return {
                **old,
                "point": {"latitude": lat, "longitude": lon},
                "source": "Met Office Weather Warnings",
                "stale": True,
                "error": "Met Office warnings could not be confirmed. Check the official warning map.",
            }


class RadarData:
    def __init__(self, fetch):
        self.fetch = fetch
        self.cache = Cache(fetch)

    def get(self):
        def work():
            raw = self.fetch("https://api.rainviewer.com/public/weather-maps.json")
            if raw.get("host") != "https://tilecache.rainviewer.com":
                raise ValueError("Unknown radar host.")
            rows = []
            for row in raw.get("radar", {}).get("past", []):
                stamp = number(row.get("time"))
                path = row.get("path", "")
                if (
                    stamp
                    and isinstance(path, str)
                    and path.startswith("/v2/radar/")
                    and all(c.isalnum() or c in "/_-" for c in path)
                    and stamp <= time.time() + 300
                ):
                    rows.append({"time": stamp, "path": path})
            if not rows:
                raise ValueError("Radar frames are unavailable.")
            return {
                "host": raw["host"],
                "frames": sorted(rows, key=lambda r: r["time"])[-6:],
                "fetchedAt": time.time(),
            }

        return self.cache.get("radar", 300, work)
