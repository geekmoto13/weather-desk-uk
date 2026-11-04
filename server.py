#!/usr/bin/env python3
"""Shannon's Weather Desk. Python 3.10+, standard library only.

Defaults to loopback. Remembered API credentials stay in a private local store. Public files
are served from an explicit allowlist; private observations never become files
in the web root. See README.md for setup and data definitions.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import errno
import hmac
import gzip
import io
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import webbrowser

from radio_space import RadioDesk
from climate import BostonClimate
from climate_rain import RainfallRecords
from annual_rain import AnnualRain
from historical_days import HistoricalDays, canonical_zone, finite, validate_day
from local_store import SavedStationError, StationStore
from lightning import LightningDesk, LightningError
from uk_location import LocationResolver
from uk_sources import UKPublicWeather as PublicWeather, UKForecast, UKWarnings, RadarData
from uk_climate import UKArchive, DEFAULT_CLIMATE, ClimateView, STATIONS
from uk_monthly import MetOfficeMonthly
from uk_midas import MidasArchive
from uk_rain import UKRain
from uk_tides import UKTides
from cwop_weather import PreferredPublicWeather
from wunderground_weather import WundergroundWeather, WundergroundError
from rain_observations import RAIN_FIELDS
from temperature_calendar import TemperatureCalendar
from timezones import local_date, validate_zone
from regional_weather import is_ireland, provider_profile
from ireland_sources import IrishPublicWeather, IrishOutlook
from ireland_climate import IrishMonthly
from ireland_tides import IrishTides

ROOT = Path(__file__).resolve().parent
UTC = dt.timezone.utc
BUILD = "UK13"
DEFAULTS = {
    "title": "Weather Desk UK & Ireland",
    "location": "Swadlincote, Derbyshire",
    "callsign": "",
    "latitude": 52.77,
    "longitude": -1.56,
    "timezone": "Europe/London",
    "skyTheme": "auto",
    "weatherSource": "auto",
    "countryCode": "GB",
    "locationConfigured": False,
    "climateStation": DEFAULT_CLIMATE,
}

ASSETS = {
    "/public-stations.js": ("public-stations.js", "text/javascript; charset=utf-8"),
    "/h5wasm.js": ("h5wasm.js", "text/javascript; charset=utf-8"),
    "/lightning-data.js": ("lightning-data.js", "text/javascript; charset=utf-8"),
    "/lightning-worker.js": ("lightning-worker.js", "text/javascript; charset=utf-8"),
    "/lightning.js": ("lightning.js", "text/javascript; charset=utf-8"),
    "/lightning.css": ("lightning.css", "text/css; charset=utf-8"),
    "/uk-tides.js": ("uk-tides.js", "text/javascript; charset=utf-8"),
    "/uk-tides.css": ("uk-tides.css", "text/css; charset=utf-8"),
    "/uk-data.js": ("uk-data.js", "text/javascript; charset=utf-8"),
    "/uk-data.css": ("uk-data.css", "text/css; charset=utf-8"),
    "/uk-map.json": ("uk-map.json", "application/json; charset=utf-8"),
    "/uk.css": ("uk.css", "text/css; charset=utf-8"),
    "/uk-radar.js": ("uk-radar.js", "text/javascript; charset=utf-8"),
    "/uk-monthly.js": ("uk-monthly.js", "text/javascript; charset=utf-8"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/alerts.js": ("alerts.js", "text/javascript; charset=utf-8"),
    "/alerts.css": ("alerts.css", "text/css; charset=utf-8"),
    "/radio.js": ("radio.js", "text/javascript; charset=utf-8"),
    "/radio.css": ("radio.css", "text/css; charset=utf-8"),
    "/past.js": ("past.js", "text/javascript; charset=utf-8"),
    "/past.css": ("past.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/astro.js": ("astro.js", "text/javascript; charset=utf-8"),
    "/design.js": ("design.js", "text/javascript; charset=utf-8"),
    "/readings.js": ("readings.js", "text/javascript; charset=utf-8"),
    "/desktop.css": ("desktop.css", "text/css; charset=utf-8"),
    "/climate.css": ("climate.css", "text/css; charset=utf-8"),
    "/climate.js": ("climate.js", "text/javascript; charset=utf-8"),
    "/location.js": ("location.js", "text/javascript; charset=utf-8"),
    "/location.css": ("location.css", "text/css; charset=utf-8"),
    "/calendar.js": ("calendar.js", "text/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class WeatherError(Exception):
    pass


def number(value):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def bounded(value, low, high):
    value = number(value)
    return value if value is not None and low <= value <= high else None


def observed_time(raw):
    stamp = number(raw.get("dateutc"))
    try:
        if stamp is not None:
            return dt.datetime.fromtimestamp(stamp / 1000, UTC).timestamp()
        date = dt.datetime.fromisoformat(str(raw.get("date", "")).replace("Z", "+00:00"))
        return date.replace(tzinfo=date.tzinfo or UTC).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def feels_like(temp, humidity, wind):
    if temp is None:
        return None, "unavailable"
    if temp <= 50 and wind is not None and wind > 3:
        w = wind**0.16
        return 35.74 + 0.6215 * temp - 35.75 * w + 0.4275 * temp * w, "calculated wind chill"
    if humidity is not None:
        # NWS preliminary heat-index screen, then Rothfusz regression/adjustments.
        simple = 0.5 * (temp + 61 + (temp - 68) * 1.2 + humidity * 0.094)
        if (simple + temp) / 2 >= 80:
            t, r = temp, humidity
            result = (
                -42.379
                + 2.04901523 * t
                + 10.14333127 * r
                - 0.22475541 * t * r
                - 0.00683783 * t * t
                - 0.05481717 * r * r
                + 0.00122874 * t * t * r
                + 0.00085282 * t * r * r
                - 0.00000199 * t * t * r * r
            )
            if r < 13 and 80 <= t <= 112:
                result -= (13 - r) / 4 * math.sqrt((17 - abs(t - 95)) / 17)
            elif r > 85 and 80 <= t <= 87:
                result += (r - 85) / 10 * (87 - t) / 5
            return result, "calculated heat index"
    return temp, "air temperature (no heat index or wind chill applies)"


def normalize(raw):
    """Ambient values are °F, inches, inHg, mph; null is never zero."""
    temp = bounded(raw.get("tempf"), -150, 160)
    humidity = bounded(raw.get("humidity"), 0, 100)
    wind = bounded(raw.get("windspeedmph"), 0, 400)
    apparent = bounded(raw.get("feelsLike"), -200, 220)
    apparent_source = "Ambient Weather"
    if apparent is None:
        apparent, apparent_source = feels_like(temp, humidity, wind)
    dew = bounded(raw.get("dewPoint"), -200, 160)
    dew_source = "Ambient Weather"
    if dew is None and temp is not None and humidity is not None and humidity > 0:
        c = (temp - 32) * 5 / 9
        g = math.log(humidity / 100) + 17.625 * c / (243.04 + c)
        dew = (243.04 * g / (17.625 - g)) * 9 / 5 + 32
        dew_source = "calculated (Magnus approximation)"
    pressure = bounded(raw.get("baromrelin"), 20, 35)
    result = {
        "timestamp": observed_time(raw),
        "tempF": temp,
        "feelsF": apparent,
        "feelsSource": apparent_source,
        "dewF": dew,
        "dewSource": dew_source,
        "humidity": humidity,
        "windMph": wind,
        "gustMph": bounded(raw.get("windgustmph"), 0, 400),
        "maxGustMph": bounded(raw.get("maxdailygust"), 0, 400),
        "windDeg": bounded(raw.get("winddir"), 0, 360),
        "pressureHpa": pressure * 33.8638866667 if pressure is not None else None,
        "solar": bounded(raw.get("solarradiation"), 0, 2500),
        "uv": bounded(raw.get("uv"), 0, 50),
    }
    for key, field in [
        ("rainDayMm", "dailyrainin"),
        ("rainEventMm", "eventrainin"),
        ("rainWeekMm", "weeklyrainin"),
        ("rainMonthMm", "monthlyrainin"),
        ("rainYearMm", "yearlyrainin"),
        ("rainTotalMm", "totalrainin"),
        ("rainRateMmHr", "hourlyrainin"),
    ]:
        value = bounded(raw.get(field), 0, 10000)
        result[key] = value * 25.4 if value is not None else None
    result["stationTimezone"] = (
        raw.get("tz") if isinstance(raw.get("tz"), str) and len(raw["tz"]) <= 100 else None
    )
    result["rainYearStatus"] = (
        "reported"
        if result["rainYearMm"] is not None
        else "missing" if raw.get("yearlyrainin") in (None, "") else "invalid"
    )
    return result


def pressure_trend(current, history):
    stamp, pressure = current.get("timestamp"), current.get("pressureHpa")
    if stamp is None or pressure is None:
        return {"state": "unavailable", "delta": None}
    # Compare with the closest observation to three hours earlier, ±30 minutes.
    usable = [
        p
        for p in history
        if p.get("pressureHpa") is not None
        and p.get("timestamp")
        and abs(p["timestamp"] - (stamp - 10800)) <= 1800
    ]
    if not usable:
        return {"state": "building history", "delta": None}
    previous = min(usable, key=lambda p: abs(p["timestamp"] - (stamp - 10800)))
    delta = pressure - previous["pressureHpa"]
    approximate = bool(previous.get("pressureApproximate"))
    direction = "rising" if delta > 0.5 else "falling" if delta < -0.5 else "steady"
    if approximate:
        low = pressure - previous["pressureMaxHpa"]
        high = pressure - previous["pressureMinHpa"]
        direction = ("rising" if low > 0.5 else "falling" if high < -0.5
                     else "steady" if low >= -0.5 and high <= 0.5 else "uncertain")
    return {
        "state": direction,
        "approximate": approximate,
        "delta": round(delta, 2),
        "hours": round((stamp - previous["timestamp"]) / 3600, 2),
    }


def demo_data():
    # Deterministic synthetic observations; no demo records ever enter SQLite.
    now = int(time.time() // 300 * 300)
    history = []
    for i in range(289):
        stamp = now - (288 - i) * 300
        local_hour = ((stamp / 3600) - 4) % 24
        t = 55 + 7 * math.sin((local_hour - 9) * math.pi / 12) + 0.5 * math.sin(i * 0.17)
        solar = (
            max(0, 440 * math.sin((local_hour - 7) * math.pi / 11)) if 7 < local_hour < 18 else 0
        )
        raw = {
            "dateutc": stamp * 1000,
            "tempf": t,
            "humidity": 66 + 8 * math.cos(i / 30),
            "windspeedmph": 6.2 + 2.4 * math.sin(i * 0.21),
            "windgustmph": 13.8,
            "maxdailygust": 18.4,
            "winddir": 225 + 16 * math.sin(i * 0.08),
            "baromrelin": (1018.3 + i * 0.006) / 33.8638866667,
            "dailyrainin": 0.04,
            "eventrainin": 0.12,
            "weeklyrainin": 0.38,
            "monthlyrainin": 2.14,
            "yearlyrainin": 30.26,
            "hourlyrainin": 0,
            "solarradiation": solar,
            "uv": round(solar / 150, 1),
        }
        history.append(normalize(raw))
    return history[-1], history


def fetch_json(url):
    try:
        request = Request(
            url,
            headers={
                "User-Agent": "ShannonsWeatherDesk/1.0 (personal weather dashboard)",
                "Accept-Encoding": "identity",
                "Accept": (
                    "application/geo+json"
                    if urlsplit(url).hostname == "api.weather.gov"
                    else "application/json"
                ),
            },
        )
        with urlopen(request, timeout=12) as response:
            payload = response.read(8_000_001)
            if len(payload) > 8_000_000:
                raise WeatherError("The weather service returned too much data.")
            if payload.startswith(b"\x1f\x8b"):
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                    payload = compressed.read(8_000_001)
                if len(payload) > 8_000_000:
                    raise WeatherError("The weather service returned too much data.")
            return json.loads(payload)
    except HTTPError as exc:
        if exc.code in (401, 403):
            message = (
                "The service rejected the keys or request. Check both Ambient keys."
                if urlsplit(url).hostname == "rt.ambientweather.net"
                else "The public weather service could not accept this request. Try again shortly."
            )
            raise WeatherError(message) from None
        if exc.code == 429:
            raise WeatherError(
                "The service rate limit was reached. Please wait a minute."
            ) from None
        raise WeatherError(
            f"The weather service returned HTTP {exc.code}. Try again shortly."
        ) from None
    except (URLError, TimeoutError, OSError):
        raise WeatherError(
            "Cannot reach the weather service. Check your internet connection and try again."
        ) from None
    except (ValueError, UnicodeError):
        raise WeatherError("The weather service returned an unreadable response.") from None


class Desk:
    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir or ROOT / "private")
        self.data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.uk_archive = UKArchive(self.data_dir)
        self.irish_monthly = IrishMonthly(self.uk_archive, STATIONS)
        self.irish_weather = IrishPublicWeather(
            self.data_dir, lambda url: fetch_json(url), feels_like, pressure_trend
        )
        self.irish_outlook = IrishOutlook(lambda url: fetch_json(url))
        self.irish_tides = IrishTides(lambda url: fetch_json(url), self.data_dir)
        self.uk_midas = MidasArchive(self.data_dir)
        self.uk_rain = UKRain(self.data_dir, lambda url: fetch_json(url))
        self.uk_forecast = UKForecast(lambda url: fetch_json(url))
        self.uk_radar = RadarData(lambda url: fetch_json(url))
        self.uk_monthly = MetOfficeMonthly()
        self.settings = copy.deepcopy(DEFAULTS)
        settings_path = self.data_dir / "preferences.json"
        if settings_path.exists():
            try:
                saved_preferences = json.loads(settings_path.read_text("utf-8"))
                self.settings.update(self.validate_settings(saved_preferences))
                if "locationConfigured" not in saved_preferences:
                    self.settings["locationConfigured"] = True
            except (ValueError, OSError, WeatherError):
                pass
        self.lock = threading.RLock()
        self.ambient_lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.api_key = self.app_key = self.mac = ""
        self.station_name = "Sample station"
        self.cache = None
        self.history_checked = self.last_ambient = 0
        self.history_error = None
        self.cache_at = self.retry_after = 0
        self.uk_tides = UKTides(self.data_dir, lambda url: fetch_json(url))
        self.warnings = UKWarnings(lambda url: fetch_json(url))
        self.location_resolver = LocationResolver(lambda url: fetch_json(url), self.uk_archive)
        # Migrate old Irish locations which inherited a UK climate reference.
        if (
            is_ireland(self.settings)
            and STATIONS[self.settings["climateStation"]["id"]].get("country") != "IE"
        ):
            self.settings.update(
                self.location_resolver.resolve(
                    self.settings["latitude"], self.settings["longitude"], self.settings["location"]
                )
            )
            settings_path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        self.location_choices = {}
        self.wunderground = WundergroundWeather(self.data_dir, feels_like)
        self.public_weather = PreferredPublicWeather(
            self.data_dir,
            PublicWeather(
                self.data_dir,
                lambda url: fetch_json(url),
                self.location_resolver,
                feels_like,
                pressure_trend,
            ),
            feels_like,
            pressure_trend,
            nearby=self.wunderground,
        )
        self.temperature_calendar = TemperatureCalendar(self.data_dir, self.reference_fetch)
        self.radio_space = RadioDesk(self.data_dir)
        self.lightning_desk = LightningDesk(self.data_dir, region="UK")
        self.db = sqlite3.connect(self.data_dir / "observations.sqlite3", check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS observations (station TEXT, stamp REAL, data TEXT, PRIMARY KEY(station, stamp))"
        )
        self.db.commit()
        self.station_store = StationStore(self.data_dir)
        self.credentials_saved = False
        self.credential_notice = None
        self.climate = ClimateView(
            BostonClimate(self.data_dir, self.reference_fetch, self.settings["climateStation"]),
            self.uk_archive,
        )
        self.rain_records = ClimateView(
            RainfallRecords(self.data_dir, self.reference_fetch, self.settings["climateStation"]),
            self.uk_archive,
        )
        self.annual_rain = AnnualRain(self.data_dir)
        self.past_days = HistoricalDays(
            self.data_dir, self.reference_fetch, self.settings["climateStation"]
        )
        try:
            saved = self.station_store.load()
            if saved:
                api, app, mac = self.validate_station(saved)
                if not mac:
                    raise WeatherError("Saved station has no MAC address.")
                self.api_key, self.app_key, self.mac = api, app, mac
                self.station_name = str(saved.get("stationName", "Ambient Weather station"))[:100]
                self.credentials_saved = True
                row = self.db.execute(
                    "SELECT data FROM observations WHERE station=? ORDER BY stamp DESC LIMIT 1",
                    (mac,),
                ).fetchone()
                if row:
                    self.cache = json.loads(row[0])
        except (SavedStationError, WeatherError, ValueError, TypeError):
            self.api_key = self.app_key = self.mac = ""
            self.credential_notice = "The saved station could not be opened. Reconnect once in Settings or choose Forget station."
        if not settings_path.exists() and not self.api_key:
            self.settings.update(title="Weather Desk UK & Ireland", callsign="")

    @staticmethod
    def validate_settings(values):
        clean = {}
        for key in DEFAULTS:
            if key not in values:
                continue
            if key == "locationConfigured":
                if not isinstance(values[key], bool):
                    raise WeatherError("Choose a valid location setting.")
                clean[key] = values[key]
            elif key == "climateStation":
                site = values[key]
                if not isinstance(site, dict) or not re.fullmatch(
                    r"[A-Za-z0-9]{3,30}", str(site.get("id", ""))
                ):
                    raise WeatherError("Choose a climate station using location search.")
                sid = site.get("sid", "")
                if sid != "unavailable" and not re.fullmatch(r"[A-Za-z0-9]{3,30} [1-9]", str(sid)):
                    raise WeatherError("Choose a valid climate station.")
                if (
                    sid.split()[0] != site["id"]
                    or not isinstance(site.get("name"), str)
                    or not 1 <= len(site["name"]) <= 100
                    or any(ord(c) < 32 for c in site["name"])
                ):
                    raise WeatherError("Choose a valid climate station.")
                try:
                    validate_zone(site.get("timezone", ""))
                except (ValueError, TypeError):
                    raise WeatherError("Climate timezone is invalid.") from None
                clean[key] = {
                    k: site[k]
                    for k in (
                        "id",
                        "sid",
                        "name",
                        "label",
                        "timezone",
                        "sourceUrl",
                        "available",
                        "distanceKm",
                        "periods",
                        "recordScale",
                    )
                    if k in site
                }
                if (
                    site["id"] not in STATIONS
                    or site.get("sourceUrl") != STATIONS[site["id"]]["daily_url"]
                ):
                    raise WeatherError(
                        "Choose a supported local climate station from location search."
                    )
                clean[key]["recordScale"] = 254

            elif key in ("latitude", "longitude"):
                bound = 90 if key == "latitude" else 180
                val = bounded(values[key], -bound, bound)
                if val is None:
                    raise WeatherError(f"Enter a valid {key}.")
                clean[key] = val
            else:
                val = str(values[key]).strip()
                if len(val) > 100 or any(ord(c) < 32 for c in val):
                    raise WeatherError("Keep setting names under 100 characters.")
                clean[key] = val
        if "timezone" in clean and not re.fullmatch(r"[A-Za-z_+\-/0-9]{1,100}", clean["timezone"]):
            raise WeatherError("Enter the Europe/London timezone.")
        if "timezone" in clean:
            try:
                validate_zone(clean["timezone"])
            except ValueError:
                raise WeatherError("Enter a supported IANA timezone.") from None
        if clean.get("timezone", "Europe/London") not in ("Europe/London", "Europe/Dublin"):
            raise WeatherError("Choose a UK or Republic of Ireland location to set its timezone.")
        if clean.get("countryCode", "GB") not in ("GB", "IE"):
            raise WeatherError("Choose a UK or Republic of Ireland location.")
        if clean.get("weatherSource", "auto") not in ("auto", "cwop", "noaa", "demo"):
            raise WeatherError(
                "Choose Automatic, nearby CWOP, Official public station or Sample weather."
            )
        if "skyTheme" in clean and clean["skyTheme"] not in (
            "auto",
            "dawn",
            "day",
            "soft",
            "dusk",
            "night",
        ):
            raise WeatherError("Choose one of the available sky color themes.")
        return clean

    def status(self):
        return {
            "build": BUILD,
            "settings": dict(self.settings),
            "connected": bool(self.api_key),
            "stationName": self.station_name,
            "selectedStation": self.mac,
            "credentialsSaved": self.credentials_saved,
            "credentialStorage": self.station_store.protection,
            "credentialNotice": self.credential_notice,
            "csrfToken": self.token,
            "refreshSeconds": 60,
            "publicStations": self.wunderground.connection(),
            "providers": provider_profile(self.settings),
        }

    def weather_mode(self):
        source = self.settings.get("weatherSource", "auto")
        return (
            "demo"
            if source == "demo"
            else "noaa" if source in ("noaa", "cwop") or not self.api_key else "ambient"
        )

    def location_search(self, values):
        try:
            return self.location_resolver.search(values.get("query", ""))
        except ValueError as exc:
            raise WeatherError(str(exc)) from None

    def location_resolve(self, values):
        try:
            profile = self.location_resolver.resolve(
                values.get("latitude"), values.get("longitude"), values.get("name", "")
            )
        except ValueError as exc:
            raise WeatherError(str(exc)) from None
        token = secrets.token_urlsafe(24)
        with self.lock:
            if len(self.location_choices) > 10:
                self.location_choices.clear()
            self.location_choices[token] = profile
        return {"settings": profile, "locationToken": token}

    def calendar(self, month=None):
        with self.lock:
            zone = (
                "UTC"
                if is_ireland(self.settings) and self.weather_mode() != "ambient"
                else self.settings["timezone"]
            )
            site = dict(self.settings["climateStation"])
            credentials = (self.api_key, self.app_key)
            station = self.mac if self.weather_mode() == "ambient" else ""
            context = (
                self.settings["latitude"],
                self.settings["longitude"],
                site["id"],
                self.weather_mode(),
            )
        month = month or local_date(zone=zone).strftime("%Y-%m")

        def active():
            return (
                (self.api_key, self.app_key) == credentials
                and (
                    "UTC"
                    if is_ireland(self.settings) and self.weather_mode() != "ambient"
                    else self.settings["timezone"]
                )
                == zone
                and context
                == (
                    self.settings["latitude"],
                    self.settings["longitude"],
                    self.settings["climateStation"]["id"],
                    self.weather_mode(),
                )
            )

        def fetch_page(cursor):
            raw = self.ambient(
                "devices/" + quote(station, safe=""),
                _credentials=credentials,
                _active=active,
                limit=288,
                endDate=cursor,
            )
            if not isinstance(raw, list):
                raise WeatherError("Station daily history unavailable.")
            return [normalize(row) for row in raw if isinstance(row, dict)]

        result = self.temperature_calendar.snapshot(
            month,
            site,
            zone,
            station,
            fetch_page,
            active,
            lambda day: self.past_days._load((station, canonical_zone(zone), day.isoformat())),
        )
        meta = self.uk_archive.metadata(site["id"])
        result.update({k: v for k, v in meta.items() if k != "source"})
        result["referenceSource"] = meta["source"]
        return result

    def ambient(self, path, _credentials=None, _active=None, **params):
        with self.ambient_lock:
            if _active is not None and not _active():
                raise WeatherError("The station connection changed.")
            pause = 1.1 - (time.monotonic() - self.last_ambient)
            if pause > 0:
                time.sleep(pause)
            self.last_ambient = time.monotonic()
            api, app = _credentials or (self.api_key, self.app_key)
            params.update(apiKey=api, applicationKey=app)
            return fetch_json("https://rt.ambientweather.net/v1/" + path + "?" + urlencode(params))

    @staticmethod
    def validate_station(values):
        api, app = (
            str(values.get("apiKey", "")).strip(),
            str(values.get("applicationKey", "")).strip(),
        )
        if not all(
            8 <= len(k) <= 512 and k.isascii() and not any(c.isspace() for c in k)
            for k in (api, app)
        ):
            raise WeatherError("Enter both your Ambient API key and application key.")
        chosen = str(values.get("macAddress", "")).strip().upper()
        if chosen and not re.fullmatch(r"(?:[A-F0-9]{2}:){5}[A-F0-9]{2}", chosen):
            raise WeatherError("The optional station MAC should look like AA:BB:CC:DD:EE:FF.")
        return api, app, chosen

    def connect(self, values):
        values = dict(values)
        if not values.get("apiKey") and not values.get("applicationKey") and self.api_key:
            values.update(apiKey=self.api_key, applicationKey=self.app_key)
            if not values.get("macAddress"):
                values["macAddress"] = self.mac
        api, app, chosen = self.validate_station(values)
        remember = values.get("remember", True)
        if not isinstance(remember, bool):
            raise WeatherError("Choose whether to remember this station.")
        with self.lock:
            old = (self.api_key, self.app_key)
            self.api_key, self.app_key = api, app
            try:
                devices = self.ambient("devices")
                if not isinstance(devices, list) or not devices:
                    raise WeatherError("No stations were found for these keys.")
                devices = [d for d in devices if isinstance(d, dict) and d.get("macAddress")]
                if not devices:
                    raise WeatherError("No compatible stations were returned.")
                if len(devices) > 1 and not chosen:
                    return {
                        "needsSelection": True,
                        "devices": [
                            {
                                "macAddress": d["macAddress"],
                                "name": str(d.get("info", {}).get("name", "Weather station")),
                            }
                            for d in devices
                        ],
                    }
                match = (
                    next(
                        (d for d in devices if str(d.get("macAddress", "")).upper() == chosen), None
                    )
                    if chosen
                    else devices[0]
                )
                if match is None:
                    raise WeatherError("That station was not found. Check its MAC address.")
                data = normalize(match.get("lastData") or {})
                if data["timestamp"] is None:
                    raise WeatherError("The station did not return a valid observation timestamp.")
            finally:
                self.api_key, self.app_key = old
            selected_mac = str(match["macAddress"]).upper()
            name = str(match.get("info", {}).get("name", "Ambient Weather station"))[:100]
            try:
                if remember:
                    self.station_store.save(
                        {
                            "apiKey": api,
                            "applicationKey": app,
                            "macAddress": selected_mac,
                            "stationName": name,
                        }
                    )
                else:
                    self.station_store.forget()
            except SavedStationError as exc:
                raise WeatherError(str(exc)) from None
            self.past_days.cancel()
            self.temperature_calendar.cancel()
            self.api_key, self.app_key = api, app
            self.mac, self.station_name = selected_mac, name
            self.credentials_saved, self.credential_notice = remember, None
            self.cache, self.cache_at = data, time.monotonic()
            self.history_checked = self.retry_after = 0
            self.history_error = None
            self.last_error = None
            self.store([data])
            return {
                "connected": True,
                "stationName": self.station_name,
                "credentialsSaved": remember,
            }

    def store(self, values):
        for row in values:
            if row.get("timestamp") is not None:
                self.db.execute(
                    "INSERT OR REPLACE INTO observations VALUES (?, ?, ?)",
                    (self.mac, row["timestamp"], json.dumps(row, allow_nan=False)),
                )
        self.db.execute("DELETE FROM observations WHERE stamp < ?", (time.time() - 40 * 86400,))
        self.db.commit()

    def saved_history(self):
        return [
            json.loads(r[0])
            for r in self.db.execute(
                "SELECT data FROM observations WHERE station=? AND stamp>=? ORDER BY stamp",
                (self.mac, time.time() - 86400),
            )
        ]

    def weather(self):
        if self.weather_mode() == "noaa":
            with self.lock:
                settings = copy.deepcopy(self.settings)
            if is_ireland(settings):
                if settings.get("weatherSource") != "cwop":
                    return self.irish_weather.snapshot(settings)
                value = self.public_weather.snapshot(settings)
                value["rain"] = self.rain()
                return value
            value = self.public_weather.snapshot(settings)
            rain = self.uk_rain.snapshot(settings)
            value["rain"] = rain
            if value.get("current"):
                current = dict(value["current"])
                # Retain the weather station's own measured rain separately from
                # the named EA gauge used for the calendar rainfall ledger.
                value["stationRain"] = {field: current.get(field) for field in RAIN_FIELDS}
                for name, field in [
                    ("day", "rainDayMm"),
                    ("event", "rainEventMm"),
                    ("week", "rainWeekMm"),
                    ("month", "rainMonthMm"),
                    ("year", "rainYearMm"),
                ]:
                    p = rain.get("periods", {}).get(name, {})
                    current[field] = (
                        p.get("mm")
                        if p.get("complete") and not rain.get("stale") and not rain.get("error")
                        else None
                    )
                current["rainRateMmHr"] = rain.get("rateMmHr")
                value["current"] = current
            return value
        with self.lock:
            if self.weather_mode() == "demo":
                current, history = demo_data()
                return {
                    "demo": True,
                    "current": current,
                    "trend": pressure_trend(current, history),
                    "stationName": "Sample station",
                    "error": None,
                    "stale": False,
                }
            error = None
            if (
                self.cache is None or time.monotonic() - self.cache_at >= 60
            ) and time.monotonic() >= self.retry_after:
                try:
                    devices = self.ambient("devices")
                    if not isinstance(devices, list):
                        raise WeatherError("Ambient returned an unexpected station response.")
                    device = next(
                        (
                            d
                            for d in devices
                            if isinstance(d, dict)
                            and str(d.get("macAddress", "")).upper() == self.mac
                        ),
                        None,
                    )
                    if device is None:
                        raise WeatherError("Your selected station is no longer listed by Ambient.")
                    data = normalize(device.get("lastData") or {})
                    if data["timestamp"] is None:
                        raise WeatherError("The station returned no valid observation time.")
                    self.cache, self.cache_at = data, time.monotonic()
                    self.store([data])
                    self.last_error = None
                except WeatherError as exc:
                    error = str(exc)
                    self.last_error = error
                    self.retry_after = time.monotonic() + 60
            error = error or getattr(self, "last_error", None)
            current = copy.deepcopy(self.cache)
            return {
                "demo": False,
                "current": current,
                "trend": pressure_trend(current or {}, self.saved_history()),
                "stationName": self.station_name,
                "error": error,
                "stale": bool(error or not current or time.time() - current["timestamp"] > 900),
            }

    def history(self):
        if self.weather_mode() == "noaa":
            with self.lock:
                settings = copy.deepcopy(self.settings)
            if is_ireland(settings) and settings.get("weatherSource") != "cwop":
                return self.irish_weather.history(settings)
            return self.public_weather.history(settings)
        with self.lock:
            if self.weather_mode() == "demo":
                return {"demo": True, "points": demo_data()[1], "error": None}
            if time.monotonic() - self.history_checked > 300:
                self.history_checked = time.monotonic()
                try:
                    rows = self.ambient("devices/" + quote(self.mac, safe=""), limit=288)
                    if not isinstance(rows, list):
                        raise WeatherError("Historical observations are temporarily unavailable.")
                    self.store([normalize(row) for row in rows if isinstance(row, dict)])
                    self.history_error = None
                except WeatherError as exc:
                    self.history_error = str(exc)
            points = self.saved_history()
            return {
                "demo": False,
                "points": points,
                "error": self.history_error,
                "trend": pressure_trend(self.cache or {}, points),
            }

    def annual(self):
        with self.lock:
            if self.weather_mode() == "noaa":
                return {
                    "status": "public",
                    "complete": False,
                    "error": None,
                    "note": (
                        "Met Éireann hourly rain uses UTC calendar boundaries. Open Rainfall ledger details for measured coverage; gaps are not zero."
                        if is_ireland(self.settings)
                        else "Measured rainfall uses a separate named Environment Agency gauge. Open Rainfall ledger details for its coverage."
                    ),
                }
            if not self.api_key:
                return {"status": "sample", "complete": False}
            current, mac, zone = copy.deepcopy(self.cache), self.mac, self.settings["timezone"]
            credentials = (self.api_key, self.app_key)
        if not current or current.get("timestamp") is None:
            return {
                "status": "waiting",
                "complete": False,
                "error": "Waiting for a station observation.",
            }
        if current.get("rainYearMm") is not None:
            self.annual_rain.cancel()
            return {
                "station": mac,
                "status": "reported",
                "complete": False,
                "error": None,
                "note": "Using Ambient's current yearly rain counter; no monthly import is needed.",
            }
        if current.get("stationTimezone") and canonical_zone(
            current["stationTimezone"]
        ) != canonical_zone(zone):
            return {
                "status": "incomplete",
                "complete": False,
                "error": "Match the dashboard timezone to your station’s daily rain reset.",
            }

        def active():
            return (
                self.mac == mac
                and (self.api_key, self.app_key) == credentials
                and self.settings["timezone"] == zone
            )

        def fetch_page(cursor):
            raw = self.ambient(
                "devices/" + quote(mac, safe=""),
                _credentials=credentials,
                _active=active,
                limit=288,
                endDate=cursor,
            )
            if not isinstance(raw, list):
                raise WeatherError("Historical station readings are unavailable.")
            return [normalize(row) for row in raw if isinstance(row, dict)]

        self.annual_rain.ensure(mac, zone, current, fetch_page, active)
        return self.annual_rain.result(mac, zone, current)

    def past_day(self, value, force=False):
        with self.lock:
            original_zone = self.settings["timezone"]
            zone = canonical_zone(original_zone)
            mac = self.mac if self.weather_mode() == "ambient" else ""
            credentials = (self.api_key, self.app_key)
        day = validate_day(value, zone)
        key = (mac, zone, day.isoformat())

        def active():
            return (
                self.mac == mac
                and (self.api_key, self.app_key) == credentials
                and self.settings["timezone"] == original_zone
            )

        def fetch_page(cursor):
            raw = self.ambient(
                "devices/" + quote(mac, safe=""),
                _credentials=credentials,
                _active=active,
                limit=288,
                endDate=cursor,
            )
            if not isinstance(raw, list):
                raise WeatherError("Historical station readings are unavailable.")
            return [normalize(row) for row in raw if isinstance(row, dict)]

        result = self.past_days.get(day, zone, mac, fetch_page, active, force)
        data = result.get("stationData")
        if mac and data:
            last = data["last"]
            rain_zone = last.get("stationTimezone")
            if rain_zone and canonical_zone(rain_zone) != zone:
                result["annual"] = {
                    "complete": False,
                    "status": "unavailable",
                    "error": "Match Settings timezone to your station's daily rain reset to compare calendar totals.",
                }
            elif finite(last.get("rainYearMm")):
                result["annual"] = {
                    "complete": True,
                    "status": "reported",
                    "method": "reported",
                    "mm": last["rainYearMm"],
                    "start": f"{day.year}-01-01",
                    "through": day.isoformat(),
                    "timestamp": last["timestamp"],
                }
            else:
                current = lambda: active() and self.past_days.is_current(key)
                self.past_days.annual.ensure(mac, zone, last, fetch_page, current)
                result["annual"] = self.past_days.annual.result(mac, zone, last)
            result["loading"] = result["loading"] or (
                result["annual"].get("status") == "building" and not result["annual"].get("error")
            )
        else:
            result["annual"] = None
        for part in ("climate", "records"):
            if isinstance(result.get(part), dict):
                result[part] = self.uk_archive.enrich(
                    result[part], self.settings["climateStation"]["id"]
                )
        return result

    def public_station_connection(self):
        return self.wunderground.connection()

    def public_station_settings(self, values):
        try:
            result = self.wunderground.update(values)
        except WundergroundError as exc:
            raise WeatherError(str(exc)) from None
        self.public_weather.reset()
        return result

    def lightning(self):
        with self.lock:
            settings = dict(self.settings)
        return self.lightning_desk.snapshot(settings)

    def lightning_settings(self, values):
        try:
            return self.lightning_desk.update(values)
        except LightningError as exc:
            raise WeatherError(str(exc)) from exc

    def radio(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
        return self.radio_space.snapshot(settings)

    def radio_settings(self, values):
        try:
            return self.radio_space.update(values)
        except ValueError as exc:
            raise WeatherError(str(exc)) from None

    def settings_update(self, values):
        clean = self.validate_settings(values)
        clean.pop("climateStation", None)
        clean.pop("countryCode", None)
        clean.pop("locationConfigured", None)
        with self.lock:
            token = values.get("locationToken", "")
            if not isinstance(token, str):
                raise WeatherError("Choose a valid location result.")
            profile = self.location_choices.pop(token, None)
            lat, lon = clean.get("latitude", self.settings["latitude"]), clean.get(
                "longitude", self.settings["longitude"]
            )
            moved = (lat, lon) != (self.settings["latitude"], self.settings["longitude"])
        if profile and (lat, lon) != (profile["latitude"], profile["longitude"]):
            profile = None
        if moved and profile is None:
            try:
                profile = self.location_resolver.resolve(lat, lon, clean.get("location", ""))
            except ValueError as exc:
                raise WeatherError(str(exc)) from None
        if profile:
            clean.update(profile)
        with self.lock:
            reference_changed = (
                bool(profile)
                or clean.get("timezone", self.settings["timezone"]) != self.settings["timezone"]
            )
            mode_changed = (
                clean.get("weatherSource", self.settings["weatherSource"])
                != self.settings["weatherSource"]
            )
            self.settings.update(clean)
            if reference_changed:
                self.settings["climateStation"]["timezone"] = (
                    "UTC" if is_ireland(self.settings) else self.settings["timezone"]
                )
                self.past_days.cancel()
                self.climate = ClimateView(
                    BostonClimate(
                        self.data_dir, self.reference_fetch, self.settings["climateStation"]
                    ),
                    self.uk_archive,
                )
                self.rain_records = ClimateView(
                    RainfallRecords(
                        self.data_dir, self.reference_fetch, self.settings["climateStation"]
                    ),
                    self.uk_archive,
                )
                self.past_days.climate_station = dict(self.settings["climateStation"])
                self.past_days.states.clear()
            if reference_changed or mode_changed:
                self.temperature_calendar.cancel()
                self.annual_rain.cancel()
            path = self.data_dir / "preferences.json"
            path.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return {"settings": dict(self.settings)}

    def disconnect(self):
        with self.lock:
            try:
                self.station_store.forget()
            except SavedStationError as exc:
                raise WeatherError(str(exc)) from None
            self.annual_rain.cancel()
            self.past_days.cancel()
            self.temperature_calendar.cancel()
            self.api_key = self.app_key = self.mac = ""
            self.credentials_saved, self.credential_notice = False, None
            self.station_name = "Sample station"
            self.cache = None
            self.last_error = None
        return {"connected": False}

    def tides(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
        if is_ireland(settings):
            return self.irish_tides.snapshot(settings)
        return self.uk_tides.snapshot(settings)

    def alerts(self):
        with self.lock:
            lat, lon = self.settings["latitude"], self.settings["longitude"]
        return (
            self.irish_outlook.warnings(lat, lon)
            if is_ireland({"latitude": lat, "longitude": lon})
            else self.warnings.get(lat, lon)
        )

    def forecast(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
        provider = self.irish_outlook.forecast if is_ireland(settings) else self.uk_forecast.get
        return provider(settings["latitude"], settings["longitude"])

    def reference_fetch(self, url):
        if urlsplit(url).hostname == "data.rcc-acis.org":
            return self.uk_archive.query(url)
        return fetch_json(url)

    def monthly(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
        provider = self.irish_monthly if is_ireland(settings) else self.uk_monthly
        return provider.get(settings["latitude"], settings["longitude"])

    def radar(self):
        return self.uk_radar.get()

    def rain(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
        if is_ireland(settings):
            weather = self.irish_weather.snapshot(settings)
            return weather.get("rain") or {
                "source": "Met Éireann",
                "providerLabel": "Met Éireann",
                "periods": {},
                "intervalMinutes": 60,
                "loading": weather.get("loading"),
                "stale": True,
                "error": weather.get("error"),
                "note": "Waiting for a rain-reporting Met Éireann station.",
            }
        return self.uk_rain.snapshot(settings)


class Handler(BaseHTTPRequestHandler):
    server_version = "WeatherDesk/1.0"

    def log_message(self, *_):
        pass  # Never log request bodies, keys, or upstream URLs.

    def allowed(self):
        host = urlsplit("http://" + self.headers.get("Host", "")).hostname
        if host in self.server.allowed_hosts:
            return True
        # LAN mode: also accept private-network IP addresses typed directly.
        # An IP literal cannot be a DNS-rebinding name, so this stays safe.
        if self.server.allow_lan and host:
            try:
                ip = ipaddress.ip_address(host)
            except ValueError:
                return False
            return ip.is_private or ip.is_link_local or ip.is_loopback
        return False

    def send_bytes(self, body, mime="application/json; charset=utf-8", status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: https://tilecache.rainviewer.com https://prop.kc2g.com https://services.swpc.noaa.gov; connect-src 'self'; frame-src 'self' https://map.blitzortung.org https://maps.blitzortung.org; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, allow_nan=False).encode("utf-8"), status=status)

    def do_GET(self):
        if not self.allowed():
            return self.send_json({"error": "Open the dashboard using its local address."}, 403)
        path = urlsplit(self.path).path
        try:
            if path in ASSETS:
                filename, mime = ASSETS[path]
                return self.send_bytes((ROOT / "static" / filename).read_bytes(), mime)
            if path == "/api/lightning/file":
                args = parse_qs(urlsplit(self.path).query)
                try:
                    body = self.server.desk.lightning_desk.file(args.get("id", [""])[0])
                    return self.send_bytes(body, "application/x-netcdf")
                except LightningError as exc:
                    return self.send_json({"error": str(exc)}, 502)
            if path == "/api/day":
                args = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                try:
                    return self.send_json(
                        self.server.desk.past_day(
                            args.get("date", [""])[0], args.get("refresh", ["0"])[0] == "1"
                        )
                    )
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if path == "/api/calendar":
                args = parse_qs(urlsplit(self.path).query, keep_blank_values=True)
                try:
                    return self.send_json(self.server.desk.calendar(args.get("month", [None])[0]))
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            if path == "/api/midas":
                args = parse_qs(urlsplit(self.path).query)
                if "station" in args:
                    try:
                        return self.send_json(
                            self.server.desk.uk_midas.view(
                                args["station"][0],
                                args.get("date", [local_date(zone="Europe/London").isoformat()])[0],
                            )
                        )
                    except ValueError as exc:
                        return self.send_json({"error": str(exc)}, 400)
                return self.send_json(self.server.desk.uk_midas.status())
            routes = {
                "/api/public-stations": self.server.desk.public_station_connection,
                "/api/lightning": self.server.desk.lightning,
                "/api/radio": self.server.desk.radio,
                "/api/status": self.server.desk.status,
                "/api/weather": self.server.desk.weather,
                "/api/history": self.server.desk.history,
                "/api/tides": self.server.desk.tides,
                "/api/forecast": self.server.desk.forecast,
                "/api/alerts": self.server.desk.alerts,
                "/api/climate": self.server.desk.climate.get,
                "/api/rain-records": self.server.desk.rain_records.get,
                "/api/annual": self.server.desk.annual,
                "/api/radar": self.server.desk.radar,
                "/api/monthly": self.server.desk.monthly,
                "/api/rain": self.server.desk.rain,
            }
            if path in routes:
                return self.send_json(routes[path]())
            return self.send_json({"error": "Not found"}, 404)
        except WeatherError as exc:
            self.send_json({"error": str(exc)}, 502)
        except (OSError, ValueError, TypeError, KeyError):
            self.send_json({"error": "Could not read the dashboard data. Please retry."}, 500)

    def do_POST(self):
        if not self.allowed():
            return self.send_json({"error": "Invalid local address."}, 403)
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.send_json({"error": "Open settings from the dashboard itself."}, 403)
        token = self.headers.get("X-Weather-Token", "")
        if not hmac.compare_digest(token, self.server.desk.token):
            return self.send_json({"error": "Reload the dashboard and try again."}, 403)
        try:
            path = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= (17_000_000 if path == "/api/midas/import" else 16000):
                return self.send_json({"error": "Invalid settings size."}, 400)
            values = json.loads(self.rfile.read(length))
            if not isinstance(values, dict):
                raise WeatherError("Settings must be a JSON object.")
            if path == "/api/midas/import":
                try:
                    return self.send_json(self.server.desk.uk_midas.import_files(values))
                except ValueError as exc:
                    return self.send_json({"error": str(exc)}, 400)
            routes = {
                "/api/location/search": self.server.desk.location_search,
                "/api/location/resolve": self.server.desk.location_resolve,
                "/api/public-stations/settings": self.server.desk.public_station_settings,
                "/api/lightning/settings": self.server.desk.lightning_settings,
                "/api/radio/settings": self.server.desk.radio_settings,
                "/api/connect": self.server.desk.connect,
                "/api/settings": self.server.desk.settings_update,
                "/api/disconnect": lambda _: self.server.desk.disconnect(),
            }
            if path not in routes:
                return self.send_json({"error": "Not found"}, 404)
            return self.send_json(routes[path](values))
        except (ValueError, UnicodeError):
            self.send_json({"error": "Unreadable settings. Please try again."}, 400)
        except WeatherError as exc:
            self.send_json({"error": str(exc)}, 400)
        except OSError:
            self.send_json({"error": "Could not save preferences on this computer."}, 500)


def lan_addresses():
    """Best-effort list of this computer's private IPv4 addresses."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # Sends nothing; just picks the LAN route.
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    result = []
    for addr in found:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if ip.is_private and not ip.is_loopback and addr not in result:
            result.append(addr)
    return result


def create_server(host="127.0.0.1", port=8766, preview=False, data_dir=None,
                  lan=False, extra_hosts=()):
    server = ThreadingHTTPServer((host, port), Handler)
    server.desk = Desk(data_dir)
    server.allowed_hosts = {"127.0.0.1", "localhost", "::1"}
    server.allow_lan = bool(lan)
    if lan:
        # This computer's own names, so http://my-pc:8766/ and my-pc.local work too.
        name = socket.gethostname().lower()
        if name:
            server.allowed_hosts.update({name, name + ".local"})
    for extra in extra_hosts:
        extra = str(extra).strip().lower()
        if extra:
            server.allowed_hosts.add(extra)
    if preview:
        server.allowed_hosts.add("terminal.local")
    return server


def main():
    parser = argparse.ArgumentParser(description="Run your local weather dashboard.")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--lan", action="store_true",
                        help="Let other devices on your home network open the dashboard "
                             "(listens on all interfaces; there is no password)")
    parser.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                        help="Extra computer name this dashboard may be reached by (repeatable)")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in your browser")
    parser.add_argument("--preview", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    host = args.host
    if args.lan and host == "127.0.0.1":
        host = "0.0.0.0"
    lan = args.lan or host not in ("127.0.0.1", "localhost", "::1")
    try:
        server = create_server(host, args.port, args.preview, lan=lan,
                               extra_hosts=args.allow_host)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"Port {args.port} is busy. Close the other dashboard or try --port 8767.")
        else:
            print(f"Could not start the dashboard: {exc}")
            print("If this mentions permissions or a missing folder, check the private/ data folder.")
        return 1
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Weather Desk UK is ready: {url}")
    if lan:
        print("Network access is ON. Other devices on your home network can open:")
        addresses = lan_addresses()
        for addr in addresses:
            print(f"    http://{addr}:{args.port}/")
        if not addresses:
            print("    (could not detect this computer's address; run ipconfig to find it)")
        print("There is no password. Use only on a network you trust, and allow Python")
        print("through Windows Firewall on Private networks only if Windows asks.")
    print("Keep this window open. Press Ctrl+C to stop. Remembered stations reconnect next time.")
    if args.open:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.desk.annual_rain.cancel()
        server.desk.past_days.cancel()
        server.desk.temperature_calendar.cancel()
        server.server_close()
        server.desk.db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
