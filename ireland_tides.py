"""Marine Institute Irish coastal sea levels, in metres OD Malin Head."""

import copy
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import quote
from ireland_sources import stamp, valid
from local_store import write_private_json
from uk_sources import Cache
from weather_location import distance_km

API = "https://erddap.marine.ie/erddap/tabledap/IrishNationalTideGaugeNetwork.json?station_id,longitude,latitude,time,Water_Level_OD_Malin,QC_Flag&time%3E=now-24hours"
OFFICIAL = (
    "https://www.marine.ie/site-area/data-services/real-time-observations/tidal-observations-0"
)


def select_tide(raw, lat, lon, now):
    table = raw["table"]
    expected = ["station_id", "longitude", "latitude", "time", "Water_Level_OD_Malin", "QC_Flag"]
    if table.get("columnNames") != expected:
        raise ValueError("Unrecognized Marine Institute tide response.")
    stations = {}
    for values in table.get("rows", []):
        if not isinstance(values, list) or len(values) != 6:
            continue
        name, longitude, latitude, when, height, quality = values
        when = stamp(when)
        if (
            not isinstance(name, str)
            or not valid(longitude, -11, -5)
            or not valid(latitude, 51, 56)
        ):
            continue
        if when is None or not now - 86400 <= when <= now + 300 or not valid(height, -20, 20):
            continue
        if isinstance(quality, bool) or quality not in (0, 1):
            continue
        station = stations.setdefault(
            name, {"latitude": latitude, "longitude": longitude, "rows": {}}
        )
        station["rows"][when] = {"timestamp": when, "heightM": height, "provisional": quality == 0}
    candidates = []
    for name, station in stations.items():
        rows = sorted(station["rows"].values(), key=lambda row: row["timestamp"])
        distance = distance_km(lat, lon, station["latitude"], station["longitude"])
        if rows and now - rows[-1]["timestamp"] <= 5400 and distance <= 200:
            candidates.append((distance, name, rows))
    if not candidates:
        raise ValueError("No recent Irish coastal gauge was available within 200 km.")
    distance, name, rows = min(candidates)
    return {
        "stationName": name,
        "distanceKm": round(distance, 1),
        "latest": rows[-1],
        "observations": rows,
        "datumLabel": "OD Malin",
        "datumName": "Ordnance Datum Malin Head (OSGM15)",
        "observed": True,
        "source": "Marine Institute",
        "sourceUrl": OFFICIAL,
        "stale": False,
        "loading": False,
        "error": None,
        "note": "© Marine Institute · CC BY 4.0. Past 24 hours, in metres OD Malin Head. Quality 0 is unverified/provisional; quality 1 is good. Missing/bad readings are excluded. These are observed sea levels, not future tide predictions.",
    }


class IrishTides:
    def __init__(self, fetch, directory=None):
        self.fetch, self.cache = fetch, Cache(fetch)
        self.directory = Path(directory) / "irish-tides" if directory else None
        if self.directory:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.states, self.lock = {}, threading.RLock()

    def _state(self, settings):
        key = f"{settings['latitude']:.4f},{settings['longitude']:.4f}"
        if key not in self.states:
            saved = None
            if self.directory:
                try:
                    candidate = json.loads((self.directory / (key + ".json")).read_text())
                    if (
                        candidate.get("locationKey") == key
                        and candidate.get("source") == "Marine Institute"
                        and isinstance(candidate.get("observations"), list)
                        and isinstance(candidate.get("latest"), dict)
                    ):
                        saved = candidate
                except (OSError, ValueError, TypeError, AttributeError):
                    pass
            self.states[key] = {
                "payload": saved,
                "loading": False,
                "checked": -math.inf,
                "retrySeconds": 300,
                "error": None,
            }
        return key, self.states[key]

    def snapshot(self, settings):
        with self.lock:
            key, state = self._state(settings)
            if (
                not state["loading"]
                and time.monotonic() - state["checked"] >= state["retrySeconds"]
            ):
                state["loading"], state["checked"] = True, time.monotonic()
                threading.Thread(
                    target=self._load, args=(key, dict(settings), state), daemon=True
                ).start()
            value = copy.deepcopy(state["payload"] or {})
            when = value.get("latest", {}).get("timestamp")
            value.update(
                source="Marine Institute",
                sourceUrl=OFFICIAL,
                observed=True,
                observations=value.get("observations", []),
                loading=state["loading"],
                stale=not valid(when, time.time() - 5400, time.time() + 300)
                or bool(state["error"]),
                error=state["error"] or value.get("historyError"),
            )
            if not value.get("note"):
                value["note"] = (
                    "Marine Institute · CC BY 4.0. Irish sea levels use metres OD Malin Head. Recent readings are checked first, then this port's history is loaded."
                )
            return value

    def _load(self, key, settings, state):
        recent_value = None
        try:
            now = time.time()
            # Discover the nearest *reporting* gauge with a small recent query.
            # Do not download 24-hour histories for the whole Irish coastline.
            recent = self.cache.get(
                "recent", 300, lambda: self.fetch(API.replace("now-24hours", "now-90minutes"))
            )
            recent_value = select_tide(recent, settings["latitude"], settings["longitude"], now)
            station = recent_value["stationName"]
            url = API + "&station_id=" + quote(json.dumps(station), safe="")
            raw = self.fetch(url)
            value = select_tide(raw, settings["latitude"], settings["longitude"], time.time())
            if value["stationName"] != station:
                raise ValueError("The history response did not match the selected Irish port.")
            value.update(locationKey=key, historyPartial=False, historyError=None)
            self._save(key, state, value)
        except Exception as exc:
            if recent_value:
                # A fresh level is still useful if the larger history request
                # fails. Combine only this exact port's known observations.
                with self.lock:
                    old = copy.deepcopy(state["payload"] or {})
                rows = {
                    r["timestamp"]: r
                    for r in old.get("observations", [])
                    if old.get("stationName") == recent_value["stationName"]
                    and isinstance(r, dict)
                    and valid(r.get("timestamp"), time.time() - 86400, time.time() + 300)
                }
                rows.update({r["timestamp"]: r for r in recent_value["observations"]})
                recent_value.update(
                    locationKey=key,
                    observations=[rows[t] for t in sorted(rows)],
                    historyPartial=True,
                    historyError="The 24-hour history request failed; showing the available readings for this port.",
                )
                self._save(key, state, recent_value)
                with self.lock:
                    state["retrySeconds"] = 60
            else:
                with self.lock:
                    state["error"] = "Marine Institute tide refresh failed: " + str(exc)
                    state["retrySeconds"] = 60
        finally:
            with self.lock:
                state["loading"] = False

    def _save(self, key, state, value):
        with self.lock:
            state.update(payload=value, error=None, retrySeconds=300)
        if self.directory:
            try:
                write_private_json(self.directory / (key + ".json"), value)
            except OSError:
                with self.lock:
                    state["payload"][
                        "cacheNotice"
                    ] = "The chart could not be saved for the next startup. Current readings are still displayed."
