"""Boston precipitation records for identical month/year-to-date windows."""

from __future__ import annotations
import copy
import datetime as dt
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from climate import climate_value, require_rows, station_info, station_label
from timezones import local_date
import re
from local_store import write_private_json


def extremes(points, scale=100):
    if not points:
        return None
    result = {}
    # A trace is above true zero, and below a measurable hundredth of an inch.
    key = lambda point: (point[0], point[2])
    for name, operation in (("wettest", max), ("driest", min)):
        chosen = operation(points, key=key)
        result[name] = {
            "valueIn": chosen[0] / scale,
            "trace": chosen[2],
            "years": sorted(
                year for amount, year, trace in points if (amount, trace) == (chosen[0], chosen[2])
            ),
        }
    result["completePeriods"] = len(points)
    return result


def rainfall_records(raw, today, station=None):
    """ACIS returns one MTD and one YTD sum for this calendar date per year."""
    site = station_info(station)
    scale = site.get("recordScale", 100)
    rows = require_rows(raw, site)
    points = [[], []]
    seen, duplicate = set(), set()
    for row in rows:
        if not isinstance(row, list) or len(row) != 3:
            continue
        try:
            year = int(row[0])
            dt.date(year, today.month, today.day)
        except (ValueError, TypeError, OverflowError):
            continue
        if year < 1700 or year >= today.year:
            continue
        if year in seen:
            duplicate.add(year)
        seen.add(year)
        for i in range(2):
            group = row[i + 1]
            if not isinstance(group, list) or len(group) != 1:
                continue
            value = group[0]
            if (
                not isinstance(value, list)
                or len(value) != 2
                or isinstance(value[1], bool)
                or value[1] not in (0, "0")
            ):
                continue
            amount, trace = climate_value(value[0], precipitation=True)
            if amount is not None:
                points[i].append((round(amount * scale), year, trace))
    points = [[point for point in series if point[1] not in duplicate] for series in points]
    if not any(points):
        raise ValueError("No complete matching precipitation windows were returned.")
    return {
        "schemaVersion": 2,
        "date": today.isoformat(),
        "stationId": site["id"],
        "stationName": station_label(site),
        "throughYear": today.year - 1,
        "startYear": min(p[1] for series in points for p in series),
        "month": extremes(points[0], scale),
        "year": extremes(points[1], scale),
        "monthStart": today.replace(day=1).isoformat(),
        "yearStart": f"{today.year}-01-01",
        "through": today.isoformat(),
        "source": "NOAA / NWS · RCC-ACIS",
        "sourceUrl": site["sourceUrl"],
        "stale": False,
        "error": None,
    }


class RainfallRecords:
    def __init__(self, directory, fetch, station=None):
        self.station = station_info(station)
        self.path = Path(directory) / (
            "boston-rain-records.json"
            if self.station["id"] == "BOSthr"
            else "rain-records-" + re.sub(r"[^A-Za-z0-9]", "", self.station["id"]) + ".json"
        )
        self.fetch, self.lock = fetch, threading.Lock()
        self.cached, self.last_attempt, self.attempt_date, self.last_error = None, 0, None, None
        try:
            if self.path.exists() and self.path.stat().st_size <= 100000:
                value = json.loads(self.path.read_text("utf-8"))
                if (
                    value.get("stationId") == self.station["id"]
                    and value.get("schemaVersion") == 2
                    and isinstance(value.get("date"), str)
                ):
                    self.cached = value
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def get(self, today=None):
        today = today or local_date(zone=self.station["timezone"])
        site = self.station
        if not site.get("available", True):
            return {
                "date": today.isoformat(),
                "stationId": site["id"],
                "month": None,
                "year": None,
                "stale": True,
                "error": "Local precipitation records are unavailable for this reference.",
            }
        with self.lock:
            fresh = (
                self.cached
                and self.cached.get("date") == today.isoformat()
                and 0 <= time.time() - self.cached.get("fetchedAt", 0) < 86400
            )
            retry = self.attempt_date == today and time.monotonic() - self.last_attempt < 300
            if not fresh and not retry:
                self.last_attempt, self.attempt_date = time.monotonic(), today
                try:
                    mmdd = today.strftime("%m-%d")
                    params = {
                        "sid": site["sid"],
                        "sdate": "por",
                        "edate": f"{today.year - 1}-12-31",
                        "meta": "name,valid_daterange",
                        "elems": [
                            {
                                "name": "pcpn",
                                "interval": "dly",
                                "duration": duration,
                                "reduce": {"reduce": "sum", "add": "mcnt"},
                                "maxmissing": 0,
                                "groupby": ["year", mmdd, mmdd],
                                "prec": 2,
                            }
                            for duration in ("mtd", "ytd")
                        ],
                    }
                    raw = self.fetch(
                        "https://data.rcc-acis.org/StnData?"
                        + urlencode({"params": json.dumps(params, separators=(",", ":"))})
                    )
                    result = rainfall_records(raw, today, site)
                    result["fetchedAt"] = time.time()
                    self.cached, self.last_error = result, None
                    try:
                        write_private_json(self.path, result)
                    except OSError:
                        pass
                except Exception:
                    self.last_error = "Local precipitation records could not refresh. They will retry automatically."
            if self.cached:
                result = copy.deepcopy(self.cached)
                result["stale"] = bool(
                    self.last_error
                    or result.get("date") != today.isoformat()
                    or time.time() - result.get("fetchedAt", 0) >= 86400
                )
                result["error"] = self.last_error
                return result
            return {
                "schemaVersion": 2,
                "stationId": site["id"],
                "date": today.isoformat(),
                "month": None,
                "year": None,
                "stale": True,
                "error": self.last_error,
            }
