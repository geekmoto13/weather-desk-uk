"""On-demand daily station archives and matching Boston climate references."""

from __future__ import annotations

from collections import OrderedDict
import copy
import datetime as dt
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import threading
import time
from urllib.parse import urlencode

from annual_rain import AnnualRain
from climate import (
    climate_value,
    daily_almanac,
    iso_date,
    require_rows,
    summarize_precip,
    station_info,
)
from timezones import local_date, canonical_zone, midnight
from climate_rain import RainfallRecords
from local_store import write_private_json

UTC = dt.timezone.utc
EASTERN = {"America/New_York", "US/Eastern", "EST5EDT"}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def day_bounds(day, zone):
    return midnight(day, zone), midnight(day + dt.timedelta(days=1), zone)


def validate_day(value, zone, now=None):
    day = iso_date(value)
    if day is None or day.isoformat() != value or day.year < 1970:
        raise ValueError("Choose a valid date from 1970 onward.")
    now = time.time() if now is None else now
    today = local_date(now, zone)
    if day >= today:
        raise ValueError("Choose a completed day; Today opens the live overview.")
    day_bounds(day, zone)
    return day


def summarize_station(points, day, zone):
    start, end = day_bounds(day, zone)
    unique = {
        p["timestamp"]: p
        for p in points
        if isinstance(p, dict) and finite(p.get("timestamp")) and start <= p["timestamp"] < end
    }
    rows = [unique[t] for t in sorted(unique)]
    if not rows:
        return None
    last = copy.deepcopy(rows[-1])

    def extreme(field, operation):
        candidates = [p for p in rows if finite(p.get(field))]
        if not candidates:
            return {"value": None, "timestamp": None}
        chosen = operation(candidates, key=lambda p: p[field])
        return {"value": chosen[field], "timestamp": chosen["timestamp"]}

    def latest(field):
        candidates = [
            p
            for p in rows
            if finite(p.get(field)) and rows[-1]["timestamp"] - p["timestamp"] <= 3600
        ]
        return candidates[-1][field] if candidates else None

    # Preserve cumulative counters; do not add a daily/monthly counter at every sample.
    for field in ("rainMonthMm", "rainYearMm", "rainWeekMm", "rainEventMm"):
        last[field] = latest(field)
    intervals = [b["timestamp"] - a["timestamp"] for a, b in zip(rows, rows[1:])]
    step = statistics.median(intervals) if intervals else None
    spans = bool(
        step
        and rows[0]["timestamp"] <= start + step * 2
        and rows[-1]["timestamp"] >= end - step * 2
    )
    gaps = max(intervals) if intervals else None
    full_span = spans and gaps <= max(step * 3, 900)
    gusts = [p[k] for p in rows for k in ("gustMph", "maxGustMph") if finite(p.get(k))]
    humidity = [p["humidity"] for p in rows if finite(p.get("humidity"))]
    return {
        "schemaVersion": 1,
        "date": day.isoformat(),
        "timezone": canonical_zone(zone),
        "high": extreme("tempF", max),
        "low": extreme("tempF", min),
        "rainDayMm": extreme("rainDayMm", max)["value"],
        "last": last,
        "maxGustMph": max(gusts) if gusts else None,
        "meanHumidity": statistics.mean(humidity) if humidity else None,
        "pressureHpa": latest("pressureHpa"),
        "coverage": {
            "count": len(rows),
            "first": rows[0]["timestamp"],
            "last": rows[-1]["timestamp"],
            "dayHours": (end - start) / 3600,
            "intervalSeconds": step,
            "largestGapSeconds": gaps,
            "fullSpan": bool(full_span),
        },
        "points": rows,
        "fetchedAt": time.time(),
    }


def fetch_station_day(fetch_page, day, zone, active=lambda: True):
    start, end = day_bounds(day, zone)
    cursor, points, previous_oldest = int(end * 1000) - 1, {}, None
    for _ in range(12):
        if not active():
            return None
        page = fetch_page(cursor)
        if not isinstance(page, list):
            raise ValueError("Unreadable station history")
        usable = [
            p
            for p in page
            if isinstance(p, dict)
            and finite(p.get("timestamp"))
            and p["timestamp"] * 1000 <= cursor + 1
        ]
        if not usable:
            break
        oldest = min(p["timestamp"] for p in usable)
        for point in usable:
            if start <= point["timestamp"] < end:
                points[point["timestamp"]] = point
        if oldest <= start or (previous_oldest is not None and oldest >= previous_oldest):
            break
        previous_oldest, cursor = oldest, math.floor(oldest * 1000) - 1
    if not active():
        return None
    return summarize_station(list(points.values()), day, zone)


def completed_almanac(series, records, day, station=None):
    result = daily_almanac(series, records, day, station)
    rows = {
        iso_date(row[0]): row
        for row in require_rows(series, station)
        if isinstance(row, list) and len(row) == 7 and iso_date(row[0])
    }
    current = rows.get(day)
    high, _ = climate_value(current[1]) if current else (None, False)
    low, _ = climate_value(current[2]) if current else (None, False)
    rain, trace = climate_value(current[3], True) if current else (None, False)
    result["observed"] = {
        "date": day.isoformat(),
        "highF": high,
        "lowF": low,
        "precipIn": rain,
        "trace": trace,
    }
    # A completed selected day belongs in its own MTD/YTD totals.
    result["precipitation"] = {
        "through": day.isoformat(),
        "day": summarize_precip(rows, day, day),
        "month": summarize_precip(rows, day.replace(day=1), day),
        "year": summarize_precip(rows, dt.date(day.year, 1, 1), day),
    }
    result["completedDay"] = True
    result["fetchedAt"] = time.time()
    return result


def historical_climate(directory, day, fetch, force=False, station=None):
    site = station_info(station)
    path = Path(directory) / ("history-" + site["id"] + ".json")
    saved = None
    try:
        if path.exists() and path.stat().st_size <= 100000:
            candidate = json.loads(path.read_text("utf-8"))
            if (
                candidate.get("date") == day.isoformat()
                and candidate.get("stationId") == site["id"]
                and candidate.get("completedDay")
            ):
                saved = candidate
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    if (
        saved
        and not force
        and finite(saved.get("fetchedAt"))
        and 0 <= time.time() - saved["fetchedAt"] < 86400
    ):
        return saved
    try:
        common = {"sid": site["sid"], "meta": "name,valid_daterange"}

        def query(params):
            return fetch(
                "https://data.rcc-acis.org/StnData?"
                + urlencode({"params": json.dumps(params, separators=(",", ":"))})
            )

        series = query(
            {
                **common,
                "sdate": f"{day.year}-01-01",
                "edate": day.isoformat(),
                "elems": [{"name": n, "add": "f", "prec": 3} for n in ("maxt", "mint", "pcpn")]
                + [{"name": n, "normal": 91, "prec": 3} for n in ("maxt", "mint", "pcpn")],
            }
        )
        mmdd = day.strftime("%m-%d")
        records = query(
            {
                **common,
                "sdate": "por",
                "edate": (day - dt.timedelta(days=1)).isoformat(),
                "elems": [
                    {
                        "name": n,
                        "interval": "dly",
                        "duration": "dly",
                        "groupby": ["year", mmdd, mmdd],
                    }
                    for n in ("maxt", "mint", "pcpn")
                ],
            }
        )
        result = completed_almanac(series, records, day, site)
        try:
            write_private_json(path, result)
        except OSError:
            pass
        return result
    except Exception:
        return {
            **(saved or {"date": day.isoformat(), "stationId": site["id"]}),
            "stale": True,
            "error": "Local climate could not load for this date. Retry when the service is available.",
        }


class HistoricalDays:
    def __init__(self, directory, fetch, station=None):
        self.climate_station = station_info(station)
        self.directory = Path(directory)
        self.path = self.directory / "history-days.sqlite3"
        self.fetch = fetch
        self.lock = threading.RLock()
        self.states = OrderedDict()
        self.selected = None
        # Separate jobs share the established monthly cache, without live-year cancellation.
        self.annual = AnnualRain(directory)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS days (station TEXT, zone TEXT, day TEXT, payload TEXT, PRIMARY KEY(station,zone,day))"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def _load(self, key):
        try:
            with self._db() as db:
                row = db.execute(
                    "SELECT payload FROM days WHERE station=? AND zone=? AND day=?", key
                ).fetchone()
            value = json.loads(row[0]) if row else None
            return (
                value
                if isinstance(value, dict)
                and value.get("date") == key[2]
                and value.get("timezone") == key[1]
                else None
            )
        except (ValueError, TypeError, sqlite3.Error):
            return None

    def is_current(self, key):
        with self.lock:
            return self.selected == key

    def cancel(self):
        with self.lock:
            self.selected = None
            for state in self.states.values():
                state["cancel"].set()
        self.annual.cancel()

    def get(self, day, zone, station, fetch_page, active, force=False):
        zone = canonical_zone(zone)
        key = (station, zone, day.isoformat())
        with self.lock:
            if self.selected != key:
                for other, state in self.states.items():
                    if other != key:
                        state["cancel"].set()
                self.selected = key
            state = self.states.get(key)
            if state is None or force or state["cancel"].is_set():
                if state:
                    state["cancel"].set()
                cached = self._load(key) if station else None
                state = {
                    "cancel": threading.Event(),
                    "stationData": cached,
                    "fromCache": bool(cached),
                    "climate": None,
                    "records": None,
                    "loadingParts": {
                        "station": bool(station and (force or not cached)),
                        "climate": True,
                        "records": True,
                    },
                    "stationError": None,
                }
                self.states[key] = state

                def current():
                    return not state["cancel"].is_set() and active()

                if state["loadingParts"]["station"]:
                    thread = threading.Thread(
                        target=self._station,
                        args=(key, state, day, fetch_page, current),
                        daemon=True,
                    )
                    state["stationThread"] = thread
                    thread.start()
                thread = threading.Thread(
                    target=self._climate,
                    args=(key, state, day, current, force, dict(self.climate_station)),
                    daemon=True,
                )
                state["climateThread"] = thread
                thread.start()
            self.states.move_to_end(key)
            while len(self.states) > 12:
                old_key, old = self.states.popitem(last=False)
                old["cancel"].set()
            return {
                "date": day.isoformat(),
                "timezone": zone,
                "station": station,
                **copy.deepcopy(
                    {
                        name: state[name]
                        for name in (
                            "stationData",
                            "fromCache",
                            "climate",
                            "records",
                            "loadingParts",
                            "stationError",
                        )
                    }
                ),
                "loading": any(state["loadingParts"].values()),
                "connected": bool(station),
            }

    def _station(self, key, state, day, fetch_page, active):
        try:
            value = fetch_station_day(fetch_page, day, key[1], active)
            if not active():
                return
            with self.lock:
                if value:
                    try:
                        with self._db() as db:
                            db.execute(
                                "INSERT INTO days VALUES (?,?,?,?) ON CONFLICT(station,zone,day) DO UPDATE SET payload=excluded.payload",
                                (*key, json.dumps(value, allow_nan=False)),
                            )
                    except (OSError, sqlite3.Error):
                        state["stationError"] = (
                            "Readings loaded, but the local day cache could not be saved."
                        )
                    state["stationData"], state["fromCache"] = value, False
                else:
                    state["stationError"] = (
                        "Ambient returned no readings for this day. The station may have been offline, not installed yet, or outside Ambient's retained history."
                    )
        except Exception:
            if active():
                with self.lock:
                    state["stationError"] = (
                        "Station history could not load. Check your connection and retry; saved readings remain available."
                    )
        finally:
            with self.lock:
                state["loadingParts"]["station"] = False

    def _climate(self, key, state, day, active, force, site):
        directory = self.directory / "history-climate" / day.isoformat()
        try:
            try:
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            except OSError:
                pass

            def fetch(url):
                if not active():
                    raise ValueError("Selection changed")
                return self.fetch(url)

            value = historical_climate(directory, day, fetch, force, site)
            if active():
                with self.lock:
                    state["climate"] = value
                    state["loadingParts"]["climate"] = False
            if not active():
                return
            records = RainfallRecords(directory, fetch, site)
            if force and records.cached:
                records.cached["fetchedAt"] = 0
            value = records.get(day)
            if active():
                with self.lock:
                    state["records"] = value
        except Exception:
            if active():
                with self.lock:
                    state["climate"] = state["climate"] or {
                        "date": day.isoformat(),
                        "error": "Local climate is unavailable for this date.",
                    }
        finally:
            with self.lock:
                state["loadingParts"]["climate"] = state["loadingParts"]["records"] = False
