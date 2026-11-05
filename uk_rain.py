"""Measured English rainfall from EA Hydrology; one gauge, persisted 15-minute data.

EA naive timestamps are GMT, including during BST. Rainfall intervals are labelled
by their ending time. All displayed day/week/month/year windows are computed from
the same 15-minute series, using Europe/London calendar boundaries. Missing or bad
intervals remain gaps; partial totals are lower bounds, never exact counters.
"""

from __future__ import annotations
import copy
import csv
import datetime as dt
import io
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from timezones import local_date, midnight
from weather_location import distance_km
from rain_since import interval_rain_since

BASE = "https://environment.data.gov.uk/hydrology"
UTC = dt.timezone.utc
NOTE = (
    "Environment Agency gauge, separate from the current-weather station and your home. "
    "Calendar days start at local midnight (GMT/BST); weeks start Monday; "
    "years start January 1. Totals include published 15-minute intervals only. "
    "A ≥ subtotal has missing/rejected intervals. Unchecked data are provisional. "
    "Rate is the latest 15-minute amount multiplied by four, not an instantaneous sensor rate."
)


def get_csv(url):
    with urlopen(
        Request(
            url,
            headers={
                "User-Agent": "WeatherDeskUK/2.0 (local weather dashboard)",
                "Accept": "text/csv",
            },
        ),
        timeout=45,
    ) as response:
        raw = response.read(12_000_001)
    if len(raw) > 12_000_000:
        raise ValueError("The rain service returned too much data.")
    return raw.decode("utf-8-sig")


def stamp(value):
    date = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return int(date.replace(tzinfo=date.tzinfo or UTC).timestamp())


def parse_readings(text, measure):
    rows = {}
    reader = csv.DictReader(io.StringIO(text))
    if not {"measure", "dateTime", "value", "quality"}.issubset(reader.fieldnames or []):
        raise ValueError("Unreadable EA rainfall file.")
    for r in reader:
        if str(r.get("measure", "")).replace("http://", "https://") != measure:
            raise ValueError("The rain service returned a different gauge.")
        try:
            t = stamp(r["dateTime"])
            v = float(r["value"])
        except (ValueError, TypeError):
            continue
        quality = r.get("quality", "")
        complete = r.get("completeness", "")
        valid = (
            math.isfinite(v)
            and 0 <= v <= 250
            and t % 900 == 0
            and quality in ("Good", "Unchecked")
            and complete in ("", "Complete")
            and r.get("invalid", "") in ("", "0", "0.0")
            and r.get("missing", "") in ("", "0", "0.0")
        )
        item = (v if valid else None, quality)
        if t in rows and rows[t] != item:
            raise ValueError("Conflicting EA readings for one interval.")
        rows[t] = item
    if not rows:
        raise ValueError("No rainfall intervals were returned.")
    return rows


def period(rows, start, end):
    if end < start:
        return {
            "mm": None,
            "complete": False,
            "missing": 0,
            "expected": 0,
            "observed": 0,
            "unchecked": 0,
            "start": int(start),
            "through": int(end),
        }
    first = int(start // 900 * 900) + 900
    last = int(end // 900 * 900)
    expected = max(0, (last - first) // 900 + 1)
    known = []
    unchecked = 0
    for t in range(first, last + 1, 900):
        value, quality = rows.get(t, (None, None))
        if value is not None:
            known.append(value)
            unchecked += quality == "Unchecked"
    return {
        "mm": round(math.fsum(known), 3) if known or expected == 0 else None,
        "complete": len(known) == expected,
        "missing": expected - len(known),
        "expected": expected,
        "observed": len(known),
        "unchecked": unchecked,
        "start": int(start),
        "through": last,
    }


def totals(rows, now):
    valid_times = [t for t, (v, _) in rows.items() if v is not None and t <= now and t % 900 == 0]
    if not valid_times:
        raise ValueError("The gauge has no usable observations.")
    end = max(t for t in rows if t <= now)
    today = local_date(now, zone="Europe/London")
    day_start = midnight(today, "Europe/London")
    starts = {
        "day": today,
        "week": today - dt.timedelta(days=today.weekday()),
        "month": today.replace(day=1),
        "year": today.replace(month=1, day=1),
    }
    periods = {
        name: (
            period(rows, midnight(day, "Europe/London"), end)
            if end >= midnight(day, "Europe/London")
            else {
                "mm": None,
                "complete": False,
                "missing": 0,
                "expected": 0,
                "observed": 0,
                "unchecked": 0,
                "start": int(midnight(day, "Europe/London")),
                "through": end,
            }
        )
        for name, day in starts.items()
    }
    months = []
    for m in range(1, today.month + 1):
        begin = dt.date(today.year, m, 1)
        after = dt.date(today.year + 1, 1, 1) if m == 12 else dt.date(today.year, m + 1, 1)
        item = period(
            rows, midnight(begin, "Europe/London"), min(end, midnight(after, "Europe/London"))
        )
        months.append({"month": m, **item})
    # Rain event: at least six complete dry hours establish its start.
    dry = 0
    event_start = None
    for t in range(
        end,
        int(max(end - 8 * 86400, midnight(today.replace(month=1, day=1), "Europe/London"))),
        -900,
    ):
        value, _ = rows.get(t, (None, None))
        dry = dry + 1 if value == 0 else 0
        if dry >= 24:
            event_start = t + 23 * 900
            break
    periods["event"] = (
        period(rows, event_start, end)
        if event_start is not None
        else {
            "mm": None,
            "complete": False,
            "missing": 0,
            "observed": 0,
            "expected": 0,
            "unchecked": 0,
            "through": end,
        }
    )
    return {
        "periods": periods,
        "months": months,
        "timestamp": end,
        "through": local_date(end, zone="Europe/London").isoformat(),
        "year": today.year,
        "start": f"{today.year}-01-01",
        "timezone": "Europe/London",
        "rateMmHr": rows[end][0] * 4 if rows[end][0] is not None and now - end <= 7200 else None,
        "stale": now - end > 7200,
        "note": NOTE,
        "dayStart": int(day_start),
        "rainSince": interval_rain_since(rows, 900, now),
    }


class UKRain:
    def __init__(self, directory, fetch_json, fetch_csv=get_csv):
        self.fetch = fetch_json
        self.fetch_csv = fetch_csv
        self.lock = threading.RLock()
        self.work_lock = threading.Lock()
        self.states = {}
        self.pending = set()
        self.attempts = {}
        self.path = Path(directory) / "ea-rainfall.sqlite3"
        with self.connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS readings (measure TEXT,stamp INTEGER,value REAL,quality TEXT,PRIMARY KEY(measure,stamp))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS locations (key TEXT PRIMARY KEY,data TEXT,checked REAL)"
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def connect(self):
        return sqlite3.connect(self.path, timeout=30)

    def snapshot(self, settings):
        lat, lon = settings["latitude"], settings["longitude"]
        key = f"{lat:.4f},{lon:.4f}"
        now = time.time()
        with self.lock:
            state = self.states.get(key)
            if state is None:
                with self.connect() as db:
                    row = db.execute(
                        "SELECT data,checked FROM locations WHERE key=?", (key,)
                    ).fetchone()
                state = (
                    json.loads(row[0])
                    if row
                    else {"source": "Environment Agency Hydrology", "note": NOTE, "error": None}
                )
                if row:
                    state["checkedAt"] = row[1]
                self.states[key] = state
            if key not in self.pending and now - self.attempts.get(key, 0) >= 900:
                self.pending.add(key)
                self.attempts[key] = now
                threading.Thread(target=self._refresh, args=(key, lat, lon), daemon=True).start()
            value = copy.deepcopy(self.states[key])
            value["loading"] = key in self.pending
        # Cached totals have to be re-windowed at midnight/year boundaries.
        if value.get("measure"):
            with self.connect() as db:
                rows = {
                    t: (v, q)
                    for t, v, q in db.execute(
                        "SELECT stamp,value,quality FROM readings WHERE measure=? AND stamp>=?",
                        (
                            value["measure"],
                            min(now-370*86400, midnight(
                                local_date(now, zone="Europe/London").replace(month=1, day=1),
                                "Europe/London",
                            )),
                        ),
                    )
                }
            if rows:
                try:
                    value.update(totals(rows, now))
                except ValueError:
                    pass
            else:
                for field in ("periods", "months", "rateMmHr"):
                    value.pop(field, None)
        return value

    def _refresh(self, key, lat, lon):
        try:
            # EA requests are sequential, including while changing locations.
            with self.work_lock:
                current = self.states.get(key, {})
                if (
                    not current.get("measure")
                    or time.time() - current.get("discoveredAt", 0) > 86400
                ):
                    raw = self.fetch(
                        BASE
                        + "/id/stations.json?"
                        + urlencode(
                            {
                                "observedProperty": "rainfall",
                                "status.label": "Active",
                                "lat": lat,
                                "long": lon,
                                "dist": 30,
                                "_limit": 100,
                            }
                        )
                    )
                    candidates = []
                    for s in raw.get("items", []):
                        try:
                            d = distance_km(lat, lon, float(s["lat"]), float(s["long"]))
                        except (ValueError, TypeError, KeyError):
                            continue
                        measures = s.get("measures", [])
                        if isinstance(measures, dict):
                            measures = [measures]
                        for m in measures:
                            uri = m.get("@id", "").replace("http://", "https://")
                            if (
                                d <= 30
                                and m.get("period") == 900
                                and m.get("parameter") == "rainfall"
                                and re.fullmatch(
                                    r"https://environment\.data\.gov\.uk/hydrology/id/measures/[A-Za-z0-9_-]+-rainfall-t-900-mm-qualified",
                                    uri,
                                )
                            ):
                                candidates.append(
                                    (d, str(s.get("label", "EA rainfall gauge")), uri, s)
                                )
                    if not candidates:
                        raise ValueError(
                            "No supported Environment Agency rain gauge was found within 30 km. This rainfall source covers England; other UK nations need a different gauge feed."
                        )
                    d, name, measure, s = min(candidates, key=lambda x: (x[0], x[1]))
                    current = {
                        "source": "Environment Agency Hydrology",
                        "stationName": name,
                        "distanceKm": round(d, 1),
                        "measure": measure,
                        "sourceUrl": measure,
                        "discoveredAt": time.time(),
                        "note": NOTE,
                    }
                measure = current["measure"]
                today = local_date(zone="Europe/London")
                start = min(midnight(today.replace(month=1, day=1), "Europe/London"), time.time()-30*86400)
                with self.connect() as db:
                    last = db.execute(
                        "SELECT max(stamp) FROM readings WHERE measure=? AND stamp>=?",
                        (measure, start),
                    ).fetchone()[0]
                # The first request imports this year's small station-specific CSV.
                # Subsequent runs retain it and recheck only seven recent days.
                since = max(start, last - 7 * 86400) if last else start
                iso = lambda t: dt.datetime.fromtimestamp(t, UTC).isoformat().replace("+00:00", "Z")
                url = (
                    measure
                    + "/readings.csv?"
                    + urlencode(
                        {
                            "mineq-dateTime": iso(since),
                            "max-dateTime": iso(time.time() + 1),
                            "_view": "full",
                            "_limit": 40000,
                        }
                    )
                )
                rows = parse_readings(self.fetch_csv(url), measure)
                if len(rows) >= 40000:
                    raise ValueError(
                        "Rainfall response reached its row limit. Retrying a smaller window is required."
                    )
                now = time.time()
                rows = {t: v for t, v in rows.items() if start <= t <= now}
                if not rows:
                    raise ValueError("The gauge returned no readings for this year.")
                with self.connect() as db:
                    db.executemany(
                        "INSERT INTO readings VALUES (?,?,?,?) ON CONFLICT(measure,stamp) DO UPDATE SET value=excluded.value,quality=excluded.quality",
                        [(measure, t, *v) for t, v in rows.items()],
                    )
                    saved = {
                        t: (v, q)
                        for t, v, q in db.execute(
                            "SELECT stamp,value,quality FROM readings WHERE measure=? AND stamp>=?",
                            (measure, start),
                        )
                    }
                    value = {**current, **totals(saved, now), "checkedAt": now, "error": None}
                    db.execute(
                        "INSERT INTO locations VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data,checked=excluded.checked",
                        (key, json.dumps(value), now),
                    )
                with self.lock:
                    self.states[key] = value
        except Exception as e:
            with self.lock:
                self.states[key] = {
                    **self.states.get(key, {}),
                    "source": "Environment Agency Hydrology",
                    "note": NOTE,
                    "error": (
                        str(e)
                        if isinstance(e, ValueError)
                        else "The EA rain-gauge service could not refresh. Saved values retain their original time."
                    ),
                }
        finally:
            with self.lock:
                self.pending.discard(key)
