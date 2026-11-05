"""Optional, owner-keyed Weather Underground PWS discovery and observations.

Only documented api.weather.com endpoints are used. Keys stay on the local
server; unchecked/failed QC and stations without measured daily rain are skipped.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from local_store import SavedStationError, StationStore
from weather_location import distance_km

ROOT = "https://api.weather.com"
SOURCE = "https://www.wunderground.com/pws-api"
MAX_AGE = 3600
RADIUS_KM = 75


class WundergroundError(ValueError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_json(url):
    try:
        request = Request(url, headers={
            "User-Agent": "WeatherDesk (local personal weather dashboard)",
            "Accept": "application/json", "Accept-Encoding": "identity",
        })
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(4_000_001)
        if len(raw) > 4_000_000:
            raise ValueError()
        return json.loads(raw)
    except HTTPError as exc:
        if exc.code in (401, 403):
            message = "Weather Underground rejected this API key or its access to this endpoint."
        elif exc.code == 429:
            message = "Weather Underground's request allowance was reached; the app will retry later."
        else:
            message = "Weather Underground could not return station data."
        raise WundergroundError(message) from None
    except Exception:
        # Network errors can contain a URL and its key. Never expose that text.
        raise WundergroundError("Weather Underground could not return station data.") from None


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and low <= value <= high else None


def passed_qc(value):
    return not isinstance(value, bool) and value in (1, "1")


def nearby_sites(payload, latitude, longitude):
    location = payload.get("location", {}) if isinstance(payload, dict) else {}
    if not isinstance(location, dict) or not isinstance(location.get("stationId"), list):
        return []
    def field(name, i):
        values = location.get(name, [])
        return values[i] if isinstance(values, list) and i < len(values) else None
    sites = []
    for i, sid in enumerate(location["stationId"][:10]):
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Z0-9]{3,32}", sid):
            continue
        lat, lon = field("latitude", i), field("longitude", i)
        # Location-service coordinates may be documented numeric strings.
        try:
            lat = None if isinstance(lat, bool) else float(lat)
            lon = None if isinstance(lon, bool) else float(lon)
        except (ValueError, TypeError):
            continue
        lat, lon = number(lat, -90, 90), number(lon, -180, 180)
        if lat is None or lon is None or not passed_qc(field("qcStatus", i)):
            continue
        distance = distance_km(latitude, longitude, lat, lon)
        if distance <= RADIUS_KM:
            name = field("stationName", i)
            sites.append({"stationId": sid, "stationName": str(name or sid)[:150],
                          "latitude": lat, "longitude": lon, "distanceKm": distance})
    return sorted(sites, key=lambda row: row["distanceKm"])


def observation(row, site, latitude, longitude, feels_like, now=None):
    now = time.time() if now is None else now
    if not isinstance(row, dict) or row.get("stationID") != site["stationId"]:
        return None
    if not passed_qc(row.get("qcStatus")):
        return None
    stamp = number(row.get("epoch"), now - MAX_AGE, now + 300)
    lat, lon = number(row.get("lat"), -90, 90), number(row.get("lon"), -180, 180)
    metric = row.get("metric")
    if stamp is None or lat is None or lon is None or not isinstance(metric, dict):
        return None
    distance = distance_km(latitude, longitude, lat, lon)
    if distance > RADIUS_KM or distance_km(lat, lon, site["latitude"], site["longitude"]) > 1:
        return None
    temp, rain = number(metric.get("temp"), -101, 71), number(metric.get("precipTotal"), 0, 3000)
    if temp is None or rain is None:
        return None
    temp = temp * 1.8 + 32
    humidity = number(row.get("humidity"), 0, 100)
    wind = number(metric.get("windSpeed"), 0, 644)
    gust = number(metric.get("windGust"), 0, 644)
    wind = wind / 1.609344 if wind is not None else None
    apparent, method = feels_like(temp, humidity, wind)
    dew = number(metric.get("dewpt"), -129, 71)
    current = {
        "timestamp": stamp, "tempF": temp, "humidity": humidity,
        "dewF": dew * 1.8 + 32 if dew is not None else None,
        "dewSource": "Weather Underground", "feelsF": apparent, "feelsSource": method,
        "windMph": wind, "windMean10mMph": wind,
        "windMean10mSource": "Weather Underground reported ten-minute mean",
        "stationLocalDay": str(row.get("obsTimeLocal", ""))[:10],
        "gustMph": gust / 1.609344 if gust is not None else None,
        "windDeg": number(row.get("winddir"), 0, 360), "maxGustMph": None,
        "pressureHpa": number(metric.get("pressure"), 800, 1100), "pressureKind": "sea-level",
        "solar": number(row.get("solarRadiation"), 0, 2000), "uv": number(row.get("uv"), 0, 30),
        "stationTimezone": None, "rainYearStatus": "missing",
        "rainRateMmHr": number(metric.get("precipRate"), 0, 3000), "rainDayMm": rain,
    }
    for field in ("Event", "Week", "Month", "Year", "Total", "LastHour", "Last24h"):
        current["rain" + field + "Mm"] = None
    return {**site, "latitude": lat, "longitude": lon, "distanceKm": round(distance, 1),
            "current": current, "source": "wunderground", "sourceUrl": SOURCE,
            "provider": "Weather Underground / The Weather Company"}


class WundergroundWeather:
    def __init__(self, directory, feels_like, fetch=fetch_json):
        self.feels_like, self.fetch = feels_like, fetch
        self.lock = threading.RLock()
        self.store = StationStore(directory)
        self.store.path = Path(directory) / "wunderground-key.json"
        self.api_key, self.saved, self.notice, self.generation = "", False, None, 0
        self.sites = {}
        try:
            saved = self.store.load()
            if saved:
                self.api_key, self.saved = self.validate_key(saved.get("apiKey")), True
        except (SavedStationError, WundergroundError):
            self.notice = "The saved Weather Underground key could not be opened. Re-enter or forget it in Settings."

    @staticmethod
    def validate_key(value):
        if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,200}", value.strip()):
            raise WundergroundError("Enter your Weather Underground data API key, separately from your station upload key or Ambient keys.")
        return value.strip()

    def connection(self):
        with self.lock:
            return {"configured": bool(self.api_key), "saved": self.saved, "notice": self.notice}

    def update(self, values):
        with self.lock:
            try:
                if values.get("action") == "forget":
                    self.store.forget()
                    api_key, saved = "", False
                elif values.get("action") == "connect":
                    api_key = self.validate_key(values.get("apiKey"))
                    saved = values.get("remember", True)
                    if not isinstance(saved, bool):
                        raise WundergroundError("Choose whether to remember the Weather Underground key.")
                    if saved:
                        self.store.save({"apiKey": api_key})
                    else:
                        self.store.forget()
                else:
                    raise WundergroundError("Choose Save key or Forget key.")
            except SavedStationError:
                raise WundergroundError("The Weather Underground key could not be saved or removed. Check the app folder's write permission.") from None
            self.api_key, self.saved, self.notice = api_key, saved, None
            self.generation += 1
            self.sites.clear()
            return self.connection()

    def _request(self, path, api_key, **params):
        params.update(format="json", apiKey=api_key)
        try:
            return self.fetch(ROOT + path + "?" + urlencode(params))
        except WundergroundError:
            raise
        except Exception:
            raise WundergroundError("Weather Underground could not return station data.") from None

    def candidates(self, settings):
        with self.lock:
            api_key, generation = self.api_key, self.generation
        if not api_key:
            return []
        lat, lon = settings["latitude"], settings["longitude"]
        key = (lat, lon)
        with self.lock:
            cached = self.sites.get(key)
        if cached and time.monotonic() - cached[0] < 3600:
            sites = cached[1]
        else:
            payload = self._request("/v3/location/near", api_key, geocode=f"{lat:.5f},{lon:.5f}", product="pws")
            sites = nearby_sites(payload, lat, lon)
            with self.lock:
                if generation != self.generation:
                    return []
                self.sites[key] = (time.monotonic(), sites)
        failed = False
        for site in sites:
            try:
                payload = self._request("/v2/pws/observations/current", api_key,
                                        stationId=site["stationId"], units="m", numericPrecision="decimal")
                rows = payload.get("observations", []) if isinstance(payload, dict) else []
                rows = rows if isinstance(rows, list) else []
                values = [value for row in rows[:10]
                          if (value := observation(row, site, lat, lon, self.feels_like))]
            except WundergroundError:
                failed = True
                continue
            if values:
                with self.lock:
                    if generation != self.generation:
                        return []
                # The nearest qualifying WU station is enough to join the CWOP pool.
                return [max(values, key=lambda value: value["current"]["timestamp"])]
        if failed:
            raise WundergroundError("Weather Underground could not check nearby stations. Check the key and its API access; other public sources are still available.")
        return []

    def pressure_history(self, chosen):
        with self.lock:
            api_key, generation = self.api_key, self.generation
        if not api_key:
            return []
        payload = self._request("/v2/pws/observations/all/1day", api_key,
                                stationId=chosen["stationId"], units="m", numericPrecision="decimal")
        rows = payload.get("observations", []) if isinstance(payload, dict) else []
        current = chosen["current"]
        points = []
        for row in (rows[:3000] if isinstance(rows, list) else []):
            if not isinstance(row, dict) or row.get("stationID") != chosen["stationId"] or not passed_qc(row.get("qcStatus")):
                continue
            stamp = number(row.get("epoch"), current["timestamp"] - 12600, current["timestamp"] - 9000)
            lat, lon = number(row.get("lat"), -90, 90), number(row.get("lon"), -180, 180)
            if stamp is None or lat is None or lon is None or distance_km(lat, lon, chosen["latitude"], chosen["longitude"]) > 1:
                continue
            metric = row.get("metric", {})
            if not isinstance(metric, dict):
                continue
            pressure = number(metric.get("pressure"), 800, 1100)
            low, high = number(metric.get("pressureMin"), 800, 1100), number(metric.get("pressureMax"), 800, 1100)
            point = {"timestamp": stamp, "pressureKind": "sea-level"}
            if pressure is not None:
                point["pressureHpa"] = pressure
            elif low is not None and high is not None and 0 <= high - low <= 1:
                point.update(pressureHpa=(low + high) / 2, pressureMinHpa=low, pressureMaxHpa=high,
                             pressureApproximate=high > low)
            else:
                continue
            points.append(point)
        with self.lock:
            return points if generation == self.generation else []
