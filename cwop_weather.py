"""Nearby CWOP reports through NOAA MADIS, with optional keyed WU stations.

The public viewer is an on-demand service, not a guaranteed continuous feed.
Requests are geographically bounded, cached for ten minutes and backed off on
failure. Network work runs outside HTTP requests; official observations remain
the fallback. Rain periods and pressure datums are never silently substituted.
"""

from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from local_store import write_private_json
from rain_observations import has_measured_rain
from weather_location import distance_km

ENDPOINT = "https://madis-data.ncep.noaa.gov/madisPublic1/cgi-bin/madisXmlPublicDir"
SOURCE = "https://madis.ncep.noaa.gov/madis_cwop.shtml"
MAX_AGE = 5400
RADIUS_KM = 75
FIELDS = (
    "T",
    "TD",
    "RH",
    "DD",
    "FF",
    "FFGUST",
    "SLP",
    "ALTSE",
    "PCP1H",
    "PCP24H",
    "PCPLM",
    "STALOC",
)


def fetch_xml(url):
    request = Request(
        url,
        headers={
            "User-Agent": "WeatherDesk/25 (local CWOP dashboard)",
            "Accept": "application/xml",
            "Accept-Encoding": "identity",
        },
    )
    with urlopen(request, timeout=45) as response:
        data = response.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("The MADIS response exceeded the local size limit.")
    if data.startswith(b"\x1f\x8b"):
        # Limit decompression as well as the compressed download.
        import io

        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            data = compressed.read(4_000_001)
    if len(data) > 4_000_000:
        raise ValueError("The MADIS response exceeded the local size limit.")
    return data


def query_url(latitude, longitude, *, pressure_time=None, wind_time=None):
    target = pressure_time if pressure_time is not None else wind_time
    dy = (1 if wind_time is not None else RADIUS_KM) / 111.0
    dx = dy / max(0.15, math.cos(math.radians(latitude)))
    params = [
        ("time", dt.datetime.fromtimestamp(target, dt.timezone.utc).strftime("%Y%m%d_%H%M") if target is not None else "0"),
        ("minbck", "-20" if wind_time is not None else "-30" if pressure_time is not None else "-89"),
        ("minfwd", "0"),
        ("recwin", "4" if wind_time is not None else "3"),
        ("timefilter", "0"),
        ("dfltrsel", "1"),
        ("stasel", "0"),
        ("pvdrsel", "1"),
        ("pvd", "APRSWXNET"),
        ("varsel", "1"),
        ("qctype", "0"),
        ("qcsel", "1"),
        ("xml", "1"),
        ("latll", f"{max(-90, latitude-dy):.4f}"),
        ("lonll", f"{max(-180, longitude-dx):.4f}"),
        ("latur", f"{min(90, latitude+dy):.4f}"),
        ("lonur", f"{min(180, longitude+dx):.4f}"),
    ]
    fields = ("SLP", "ALTSE") if pressure_time is not None else FIELDS
    return ENDPOINT + "?" + urlencode(params + [("nvars", field) for field in fields])


def parse_pressure_history(xml, chosen):
    """A pressure-only archive report must match station, location, datum and span."""
    if isinstance(xml, str):
        xml = xml.encode()
    if len(xml) > 4_000_000 or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("The MADIS history response was not a supported observation document.")
    current = chosen["current"]
    field = "SLP" if current.get("pressureKind") == "sea-level" else "ALTSE"
    groups = {}
    for element in ET.fromstring(xml).iter("record"):
        a = element.attrib
        if (a.get("provider") != "APRSWXNET" or a.get("shef_id") != chosen["stationId"]
                or a.get("var", "").removeprefix("V-") != field or a.get("QCD") not in ("C", "S", "V", "G")):
            continue
        pressure = numeric(a.get("data_value"), 80000, 110000)
        lat, lon = numeric(a.get("lat"), -90, 90), numeric(a.get("lon"), -180, 180)
        if pressure is None or lat is None or lon is None:
            continue
        if distance_km(lat, lon, chosen["latitude"], chosen["longitude"]) > 1:
            continue
        try:
            when = dt.datetime.fromisoformat(a.get("ObTime", "").replace("Z", "+00:00"))
            stamp = when.replace(tzinfo=when.tzinfo or dt.timezone.utc).timestamp()
        except (ValueError, OverflowError):
            continue
        if abs(stamp - (current["timestamp"] - 10800)) <= 1800:
            groups[stamp] = {"timestamp": stamp, "pressureHpa": pressure / 100,
                             "pressureKind": current["pressureKind"]}
    return [groups[stamp] for stamp in sorted(groups)]


def numeric(value, minimum, maximum):
    try:
        number = float(value)
        return number if math.isfinite(number) and minimum <= number <= maximum else None
    except (TypeError, ValueError):
        return None


def parse_reports(xml, latitude, longitude, feels_like, now=None, *, require_rain=False, keep_all=False):
    """Keep QC decisions per variable and complete observation timestamps separate."""
    now = time.time() if now is None else now
    if isinstance(xml, str):
        xml = xml.encode()
    if len(xml) > 4_000_000 or b"<!DOCTYPE" in xml.upper() or b"<!ENTITY" in xml.upper():
        raise ValueError("The MADIS response was not a supported observation document.")
    document = ET.fromstring(xml)
    groups = {}
    for element in document.iter("record"):
        a = element.attrib
        sid = a.get("shef_id", "")
        if a.get("provider") != "APRSWXNET" or not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", sid):
            continue
        try:
            when = dt.datetime.fromisoformat(a.get("ObTime", "").replace("Z", "+00:00"))
            stamp = (
                when.replace(tzinfo=dt.timezone.utc).timestamp()
                if when.tzinfo is None
                else when.timestamp()
            )
        except (ValueError, OverflowError):
            continue
        if not now - MAX_AGE <= stamp <= now + 300:
            continue
        lat, lon = numeric(a.get("lat"), -90, 90), numeric(a.get("lon"), -180, 180)
        if lat is None or lon is None:
            continue
        distance = distance_km(latitude, longitude, lat, lon)
        if distance > RADIUS_KM:
            continue
        group = groups.setdefault(
            (sid, stamp),
            {"values": {}, "name": sid, "distance": distance, "latitude": lat, "longitude": lon},
        )
        field = a.get("var", "").removeprefix("V-")
        if field == "STALOC":
            group["name"] = " ".join(a.get("data_value", sid).split())[:150]
        elif a.get("QCD") in ("C", "S", "V", "G"):
            group["values"][field] = a.get("data_value")
    candidates = []
    for (sid, stamp), group in groups.items():
        values = group["values"]
        temperature = numeric(values.get("T"), 172.04, 344.26)
        if temperature is None:
            continue
        temp = (temperature - 273.15) * 1.8 + 32
        dew = numeric(values.get("TD"), 144.26, 344.26)
        humidity = numeric(values.get("RH"), 0, 100)
        speed = numeric(values.get("FF"), 0, 178.8)
        gust = numeric(values.get("FFGUST"), 0, 178.8)
        wind = speed * 2.2369362921 if speed is not None else None
        apparent, method = feels_like(temp, humidity, wind)
        pressure = numeric(values.get("SLP"), 80000, 110000)
        kind = "sea-level"
        if pressure is None:
            pressure = numeric(values.get("ALTSE"), 80000, 110000)
            kind = "QNH"
        current = {
            "timestamp": stamp,
            "tempF": temp,
            "dewF": (dew - 273.15) * 1.8 + 32 if dew is not None else None,
            "dewSource": "NOAA MADIS / CWOP",
            "humidity": humidity,
            "feelsF": apparent,
            "feelsSource": method,
            "windMph": wind,
            "gustMph": gust * 2.2369362921 if gust is not None else None,
            "windDeg": numeric(values.get("DD"), 0, 360),
            "maxGustMph": None,
            "pressureHpa": pressure / 100 if pressure is not None else None,
            "pressureKind": kind,
            "solar": None,
            "uv": None,
            "stationTimezone": None,
            "rainYearStatus": "missing",
            "rainRateMmHr": None,
        }
        for field in ("Day", "Event", "Week", "Month", "Year", "Total"):
            current["rain" + field + "Mm"] = None
        for field, target in [
            ("PCP1H", "rainLastHourMm"),
            ("PCP24H", "rainLast24hMm"),
            ("PCPLM", "rainDayMm"),
        ]:
            amount = numeric(values.get(field), 0, 3)
            current[target] = amount * 1000 if amount is not None else None
        if require_rain and not has_measured_rain(current):
            continue
        candidates.append(
            {
                "current": current,
                "stationId": sid,
                "stationName": group["name"],
                "latitude": group["latitude"],
                "longitude": group["longitude"],
                "distanceKm": round(group["distance"], 1),
                "_distance": group["distance"],
            }
        )
    # A station may appear more than once. Only its latest complete report competes.
    if keep_all:
        for row in candidates:
            row.pop("_distance")
        return sorted(candidates, key=lambda row: row["current"]["timestamp"])
    latest = {}
    for row in candidates:
        previous = latest.get(row["stationId"])
        if previous is None or row["current"]["timestamp"] > previous["current"]["timestamp"]:
            latest[row["stationId"]] = row
    result = sorted(
        latest.values(), key=lambda row: (row["_distance"], -row["current"]["timestamp"])
    )
    for row in result:
        row.pop("_distance")
    return result


class PreferredPublicWeather:
    """Choose the nearest qualifying personal station, with an official fallback."""

    def __init__(self, directory, official, feels_like, trend, fetch=fetch_xml, *, nearby=None):
        self.directory = Path(directory) / "cwop-weather"
        self.official, self.feels_like, self.trend, self.fetch = official, feels_like, trend, fetch
        self.lock = threading.RLock()
        self.states = {}
        self.nearby = nearby
        self.force_refresh = False

    def reset(self):
        with self.lock:
            self.states.clear()
            self.force_refresh = True

    def _state(self, settings):
        key = f"{settings['latitude']:.4f},{settings['longitude']:.4f}"
        state = self.states.get(key)
        if state is None:
            state = {
                "payload": None,
                "checked": -math.inf,
                "loading": False,
                "error": None,
                "failures": 0,
                "official": None,
                "officialLoading": False,
                "officialChecked": -math.inf,
                "historyChecked": {},
            }
            try:
                cached = json.loads(self._path(key).read_text())
                if (
                    cached.get("locationKey") == key
                    and isinstance(cached.get("points"), list)
                    and has_measured_rain(cached.get("current"))
                    and (cached.get("source") != "wunderground" or (self.nearby and self.nearby.connection()["configured"]))
                ):
                    state["payload"] = cached
                    age = max(0, time.time() - cached.get("fetchedAt", 0))
                    needs_archive = (cached.get("current", {}).get("pressureHpa") is not None
                                     and (cached.get("trend") or {}).get("delta") is None
                                     and not cached.get("pressureArchiveCheckedAt"))
                    needs_wind = cached.get("current", {}).get("windMph") is not None and cached.get("metricsVersion") != 1
                    state["checked"] = -math.inf if self.force_refresh or needs_archive or needs_wind else time.monotonic() - min(age, 600)
            except (OSError, ValueError, TypeError):
                pass
            self.states[key] = state
        return key, state

    def _path(self, key):
        return self.directory / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".json")

    @staticmethod
    def _fresh(payload):
        stamp = ((payload or {}).get("current") or {}).get("timestamp")
        return (
            has_measured_rain((payload or {}).get("current"))
            and isinstance(stamp, (int, float))
            and not isinstance(stamp, bool)
            and -300 <= time.time() - stamp <= (3600 if payload.get("source") == "wunderground" else MAX_AGE)
        )

    def snapshot(self, settings):
        official_only = settings.get("weatherSource") == "noaa"
        with self.lock:
            key, state = self._state(settings)
            # Old saved or in-memory station choices must obey the new rule too.
            if state["payload"] and not has_measured_rain(state["payload"].get("current")):
                state["payload"] = None
                state["checked"] = -math.inf
            if (
                state["official"]
                and state["official"].get("current")
                and not has_measured_rain(state["official"]["current"])
            ):
                state["official"] = None
                state["officialChecked"] = -math.inf
            if not official_only:
                delay = min(3600, 600 * 2 ** min(state["failures"], 3))
                if not state["loading"] and time.monotonic() - state["checked"] >= delay:
                    state["loading"] = True
                    state["checked"] = time.monotonic()
                    threading.Thread(
                        target=self._load, args=(key, dict(settings), state), daemon=True
                    ).start()
                if self._fresh(state["payload"]):
                    result = copy.deepcopy(state["payload"])
                    result.update(
                        demo=False,
                        source=result.get("source", "cwop"),
                        loading=False,
                        publicSearchLoading=state["loading"],
                        refreshing=state["loading"],
                        error=None,
                        stale=False,
                        staleAfterSeconds=3600 if result.get("source") == "wunderground" else MAX_AGE,
                        rainGaugeRequired=True,
                    )
                    if state["error"]:
                        result["publicSourceNote"] = (
                            state["error"] + " Showing the dated last usable report."
                        )
                    return result
            if not state["officialLoading"] and time.monotonic() - state["officialChecked"] >= (
                2 if (state["official"] or {}).get("loading") else 300
            ):
                state["officialLoading"] = True
                state["officialChecked"] = time.monotonic()
                threading.Thread(
                    target=self._fallback, args=(dict(settings), state), daemon=True
                ).start()
            result = copy.deepcopy(state["official"] or {})
            if not result.get("current") and not official_only and state["payload"]:
                result = copy.deepcopy(state["payload"])
                result["source"] = result.get("source", "cwop")
            if result.get("source") in ("cwop", "wunderground"):
                result["stale"] = True
                result["error"] = (
                    "The public station report is old. Checking an official station; observation time is unchanged."
                )
            searching = (
                (not official_only and state["loading"])
                or state["officialLoading"]
                or result.get("loading", False)
            )
            result.update(
                demo=False,
                publicSearchLoading=bool(searching),
                loading=bool(searching and not result.get("current")),
            )
            result.setdefault("source", "public")
            result.setdefault("stationName", "Nearby public station")
            if not official_only:
                result["publicSourceNote"] = (
                    "Looking for nearby personal stations reporting measured rainfall."
                    if state["loading"]
                    else state["error"]
                    or "No recent CWOP rain-gauge report within 75 km. Checking official rain-reporting stations."
                )
            result["rainGaugeRequired"] = True
            if not result.get("current") and not searching:
                result["error"] = result.get("error") or (
                    "No nearby weather station reporting measured rainfall was found. Retrying automatically."
                )
                result["stale"] = True
            return result

    def history(self, settings):
        result = self.snapshot(settings)
        return {
            "demo": False,
            "points": result.get("points", []),
            "trend": result.get("trend"),
            "error": result.get("error"),
        }

    def _fallback(self, settings, state):
        try:
            value = self.official.snapshot(settings)
            if value.get("current") and not has_measured_rain(value["current"]):
                raise ValueError("The official station has no usable measured rainfall report.")
            with self.lock:
                state["official"] = value
        except Exception:
            with self.lock:
                state["official"] = {
                    **(state["official"] or {}),
                    "stale": True,
                    "error": "No usable official rain-gauge observation is available. Retrying automatically.",
                }
        finally:
            with self.lock:
                state["officialLoading"] = False

    def _payload(self, key, chosen, previous):
        chosen = copy.deepcopy(chosen)
        current = chosen["current"]
        same_station = (previous.get("source", "cwop"), previous.get("stationId")) == (chosen["source"], chosen["stationId"])
        points = previous.get("points", []) if same_station else []
        points = {p["timestamp"]: p for p in points
                  if p.get("timestamp", 0) > time.time() - 86400
                  and p.get("pressureKind") == current.get("pressureKind")}
        points[current["timestamp"]] = current
        points = [points[t] for t in sorted(points)]
        chosen.update(locationKey=key, points=points, trend=self.trend(current, points), fetchedAt=time.time())
        chosen["metricsVersion"] = 1
        if same_station:
            chosen["windHistory"] = previous.get("windHistory", [])
        if same_station and previous.get("current", {}).get("pressureKind") == current.get("pressureKind"):
            for field in ("pressureArchiveCheckedAt", "pressureHistoryNote"):
                if field in previous:
                    chosen[field] = previous[field]
        if chosen["trend"].get("delta") is not None:
            chosen.pop("pressureHistoryNote", None)
        return chosen

    def _install(self, key, state, payload, *, save=False):
        with self.lock:
            if self.states.get(key) is not state:
                return False
            if save:
                try:
                    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    write_private_json(self._path(key), payload)
                except OSError:
                    pass
            state.update(payload=payload, error=None, failures=0)
            return True

    def _load(self, key, settings, state):
        try:
            with self.lock:
                previous = copy.deepcopy(state["payload"] or {})
            notes = []
            try:
                candidates = parse_reports(
                    self.fetch(query_url(settings["latitude"], settings["longitude"])),
                    settings["latitude"], settings["longitude"], self.feels_like, require_rain=True,
                )
            except Exception:
                candidates = []
                notes.append("NOAA MADIS could not refresh. Checking other rain-reporting stations.")
            for row in candidates:
                row.update(source="cwop", sourceUrl=SOURCE, provider="NOAA MADIS / CWOP")
            if candidates:
                # Make a current report visible before optional provider/archive calls.
                if not self._install(key, state, self._payload(key, candidates[0], previous)):
                    return
            if self.nearby:
                try:
                    candidates.extend(self.nearby.candidates(settings))
                except ValueError as exc:
                    notes.append(str(exc))
            if not candidates:
                raise ValueError(
                    " ".join(notes) or "No recent CWOP station reporting measured rainfall within 75 km. Checking official rain-reporting stations."
                )
            chosen = self._payload(key, min(candidates, key=lambda row: distance_km(
                settings["latitude"], settings["longitude"], row["latitude"], row["longitude"])), previous)
            if notes:
                chosen["publicSourceNote"] = " ".join(notes)
            current = chosen["current"]
            if chosen["source"] == "cwop" and current.get("windMph") is not None:
                chosen["windHistoryLoading"] = True
                if not self._install(key, state, copy.deepcopy(chosen)):
                    return
                try:
                    reports = parse_reports(self.fetch(query_url(chosen["latitude"], chosen["longitude"], wind_time=current["timestamp"])),
                        chosen["latitude"], chosen["longitude"], self.feels_like,
                        now=current["timestamp"], keep_all=True)
                    chosen["windHistory"] = [row["current"] for row in reports
                        if row["stationId"] == chosen["stationId"]
                        and distance_km(row["latitude"], row["longitude"], chosen["latitude"], chosen["longitude"]) <= 1
                        and current["timestamp"]-1200 <= row["current"]["timestamp"] <= current["timestamp"]]
                except Exception:
                    chosen["windHistoryNote"] = "Recent CWOP wind reports could not be retrieved. Ten-minute means require sufficient actual reports."
                finally:
                    chosen["windHistoryLoading"] = False
            identity = (chosen["source"], chosen["stationId"], current.get("pressureKind"))
            if (current.get("pressureHpa") is not None and chosen["trend"].get("delta") is None
                    and time.time() - chosen.get("pressureArchiveCheckedAt", -math.inf) >= 1800
                    and time.monotonic() - state["historyChecked"].get(identity, -math.inf) >= 1800):
                state["historyChecked"][identity] = time.monotonic()
                chosen["pressureArchiveCheckedAt"] = time.time()
                chosen["pressureHistoryLoading"] = True
                if not self._install(key, state, copy.deepcopy(chosen)):
                    return
                try:
                    if chosen["source"] == "wunderground":
                        earlier = self.nearby.pressure_history(chosen)
                    else:
                        earlier = parse_pressure_history(self.fetch(query_url(
                            chosen["latitude"], chosen["longitude"], pressure_time=current["timestamp"] - 10800)), chosen)
                    points = {p["timestamp"]: p for p in earlier}
                    # Saved instantaneous observations take priority over summaries.
                    points.update({p["timestamp"]: p for p in chosen["points"]})
                    chosen["points"] = [points[stamp] for stamp in sorted(points)]
                    chosen["trend"] = self.trend(current, chosen["points"])
                    if chosen["trend"].get("delta") is None:
                        chosen["pressureHistoryNote"] = "No matching pressure observation around three hours earlier was available. Current readings are saved for the next comparison."
                except Exception:
                    chosen["pressureHistoryNote"] = "Earlier pressure could not be retrieved. Current readings are saved locally; the archive will be retried."
                finally:
                    chosen["pressureHistoryLoading"] = False
            self._install(key, state, chosen, save=True)
        except Exception as exc:
            with self.lock:
                state["error"] = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "NOAA MADIS could not refresh. Checking official rain-reporting stations."
                )
                state["failures"] += 1
        finally:
            with self.lock:
                state["loading"] = False
