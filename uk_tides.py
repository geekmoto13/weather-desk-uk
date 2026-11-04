"""Observed UK coastal tide curves from the Environment Agency's OGL API.

These are 15-minute mean sea levels in metres relative to Ordnance Datum
Newlyn, not chart-datum heights or future tidal predictions.
"""

from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode, urlsplit
from local_store import write_private_json
from weather_location import distance_km

BASE = "https://environment.data.gov.uk/flood-monitoring"
ATTRIBUTION = "this uses Environment Agency tide gauge data from the real-time data API (Beta)"


def stamp(value):
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return when.timestamp() if when.tzinfo is not None else None
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None


def official_uri(value, kind):
    try:
        uri = urlsplit(value)
        if uri.hostname != "environment.data.gov.uk" or uri.scheme not in ("http", "https"):
            return None
        if not re.fullmatch(r"/flood-monitoring/id/" + kind + r"/[A-Za-z0-9_-]+", uri.path):
            return None
        return "https://environment.data.gov.uk" + uri.path
    except (TypeError, ValueError):
        return None


def valid_reading(row, now):
    if not isinstance(row, dict):
        return None
    when, value = stamp(row.get("dateTime")), row.get("value")
    quality = row.get("quality", "")
    if quality and str(quality).split("/")[-1].lower() in (
        "bad",
        "suspect",
        "unchecked",
        "missing",
        "rejected",
    ):
        return None
    if when is None or not now - 90000 <= when <= now + 300:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not -20 <= value <= 20
    ):
        return None
    return {"timestamp": when, "heightM": value}


def candidates(stations, measures, latitude, longitude, now):
    """Join exact station URIs, including region suffixes; exclude offline gauges."""
    fresh = {}
    for row in measures:
        if row.get("unitName") != "mAOD" or row.get("qualifier") != "Tidal Level":
            continue
        reading = valid_reading(row.get("latestReading"), now)
        station = official_uri(row.get("station"), "stations")
        measure = official_uri(row.get("@id"), "measures")
        if station and measure and reading and now - reading["timestamp"] <= 5400:
            fresh[station] = (measure, reading)
    result = []
    for row in stations:
        uri = official_uri(row.get("@id"), "stations")
        if uri not in fresh:
            continue
        try:
            lat, lon = float(row["lat"]), float(row["long"])
            if not math.isfinite(lat + lon) or not -90 <= lat <= 90 or not -180 <= lon <= 180:
                continue
            distance = distance_km(latitude, longitude, lat, lon)
        except (ValueError, TypeError, KeyError):
            continue
        result.append(
            {
                "stationName": str(row.get("label", "Coastal tide gauge"))[:100],
                "stationId": str(row.get("notation", uri.rsplit("/", 1)[-1]))[:80],
                "stationUrl": uri,
                "measureUrl": fresh[uri][0],
                "distanceKm": round(distance, 1),
            }
        )
    return sorted(result, key=lambda row: row["distanceKm"])


class UKTides:
    def __init__(self, directory, fetch):
        self.directory = Path(directory) / "uk-tides"
        self.fetch = fetch
        self.states = {}
        self.lock = threading.RLock()
        self.catalogue = None
        self.catalogue_at = 0

    def snapshot(self, settings):
        key = f"{settings['latitude']:.4f},{settings['longitude']:.4f}"
        with self.lock:
            state = self.states.get(key)
            if state is None:
                state = {"payload": None, "checked": -math.inf, "loading": False, "error": None}
                try:
                    saved = json.loads(self._path(key).read_text())
                    if saved.get("locationKey") == key and isinstance(
                        saved.get("observations"), list
                    ):
                        state["payload"] = saved
                except (OSError, ValueError, TypeError):
                    pass
                self.states[key] = state
            if not state["loading"] and time.monotonic() - state["checked"] >= 900:
                state["loading"] = True
                state["checked"] = time.monotonic()
                threading.Thread(
                    target=self._load, args=(key, dict(settings), state), daemon=True
                ).start()
            result = copy.deepcopy(state["payload"] or {})
            current = result.get("latest") or {}
            result.update(
                loading=state["loading"],
                error=state["error"],
                observed=True,
                stale=not current or time.time() - current.get("timestamp", 0) > 5400,
                attribution=ATTRIBUTION,
                datum="m AOD (Ordnance Datum Newlyn)",
                sourceUrl=BASE + "/doc/tidegauge",
                predictionsUrl="https://easytide.admiralty.co.uk/",
            )
            return result

    def _path(self, key):
        return self.directory / (hashlib.sha256(key.encode()).hexdigest()[:20] + ".json")

    def _load(self, key, settings, state):
        try:
            if self.catalogue is None or time.monotonic() - self.catalogue_at >= 86400:
                catalogue = self.fetch(
                    BASE + "/id/stations?type=TideGauge&unitName=mAOD&_limit=100"
                )["items"]
                if not isinstance(catalogue, list):
                    raise ValueError("The coastal gauge catalogue was unreadable.")
                self.catalogue, self.catalogue_at = catalogue, time.monotonic()
            measures = self.fetch(
                BASE + "/id/measures?stationType=TideGauge&unitName=mAOD&_limit=100"
            )["items"]
            now = time.time()
            stations = candidates(
                self.catalogue, measures, settings["latitude"], settings["longitude"], now
            )
            for station in stations[:4]:
                since = (
                    dt.datetime.fromtimestamp(now - 86400, dt.timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z")
                )
                try:
                    data = self.fetch(
                        station["measureUrl"]
                        + "/readings?"
                        + urlencode({"since": since, "_sorted": "", "_limit": 200})
                    )
                    rows = [valid_reading(row, now) for row in data["items"]]
                    rows = {
                        row["timestamp"]: row
                        for row in rows
                        if row and row["timestamp"] >= now - 86400
                    }
                    rows = [rows[t] for t in sorted(rows)]
                    if len(rows) < 2 or now - rows[-1]["timestamp"] > 5400:
                        continue
                    result = {
                        **station,
                        "locationKey": key,
                        "observations": rows,
                        "latest": rows[-1],
                        "fetchedAt": now,
                    }
                    try:
                        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                        write_private_json(self._path(key), result)
                    except OSError:
                        pass
                    with self.lock:
                        state.update(payload=result, error=None)
                    break
                except Exception:
                    continue
            else:
                raise ValueError(
                    "No reporting UK coastal tide gauge was available. Future tide tables are linked below."
                )
        except Exception as exc:
            with self.lock:
                state["error"] = (
                    str(exc)
                    if isinstance(exc, ValueError)
                    else "Coastal tide readings could not refresh. Retrying automatically."
                )
        finally:
            with self.lock:
                state["loading"] = False
