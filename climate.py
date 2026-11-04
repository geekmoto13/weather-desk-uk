"""Boston ThreadEx daily almanac, using the public RCC-ACIS climate service.

Daily observations are preliminary. Records are for a Boston calendar date;
station comparisons use normals to the displayed calendar date.
"""

from __future__ import annotations

import calendar
import copy
import datetime as dt
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from local_store import write_private_json
from timezones import local_date

UTC = dt.timezone.utc
SOURCE_URL = "https://www.weather.gov/wrh/Climate?wfo=box"
BOSTON = {
    "id": "BOSthr",
    "sid": "BOSthr 9",
    "name": "Boston Area",
    "label": "Boston",
    "timezone": "America/New_York",
    "sourceUrl": SOURCE_URL,
}


def station_info(station=None):
    return dict(BOSTON if station is None else station)


def station_label(station=None):
    value = station_info(station)
    return value["name"] + (" (ThreadEx)" if value["sid"].endswith(" 9") else "")


def boston_date(timestamp=None):
    now = dt.datetime.fromtimestamp(time.time() if timestamp is None else timestamp, UTC)
    try:
        return now.astimezone(ZoneInfo("America/New_York")).date()
    except ZoneInfoNotFoundError:
        # Stock Windows Python may have no IANA database. Modern US DST rules,
        # for the current date only; historical observations keep their ACIS dates.
        march = dt.date(now.year, 3, 1)
        november = dt.date(now.year, 11, 1)
        spring = 1 + (6 - march.weekday()) % 7 + 7
        autumn = 1 + (6 - november.weekday()) % 7
        start = dt.datetime(now.year, 3, spring, 7, tzinfo=UTC)
        end = dt.datetime(now.year, 11, autumn, 6, tzinfo=UTC)
        return (now + dt.timedelta(hours=-4 if start <= now < end else -5)).date()


def climate_value(raw, precipitation=False):
    flag = ""
    if isinstance(raw, list):
        if not raw:
            return None, False
        if len(raw) > 1:
            flag = str(raw[1]).strip()
        raw = raw[0]
    value = str(raw).strip()
    if "A" in flag.upper():
        return None, False
    if precipitation and value == "T":
        return 0.0, True
    # An accumulated multi-day observation cannot safely be assigned to one day.
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return None, False
    result = float(value)
    lower, upper = (0, 1000) if precipitation else (-150, 160)
    return (result if math.isfinite(result) and lower <= result <= upper else None), False


def iso_date(raw):
    try:
        return dt.date.fromisoformat(str(raw))
    except (ValueError, TypeError):
        return None


def require_rows(raw, station=None):
    if not isinstance(raw, dict) or raw.get("error") or not isinstance(raw.get("data"), list):
        raise ValueError("The climate service returned no daily data.")
    meta = raw.get("meta", {})
    if not isinstance(meta, dict) or meta.get("name") != station_info(station)["name"]:
        raise ValueError("The climate service returned a different station.")
    return raw["data"]


def records_for_day(raw, today, station=None):
    rows = require_rows(raw, station)
    values = [[], [], []]
    for row in rows:
        if not isinstance(row, list) or len(row) not in (3, 4):
            continue
        try:
            date = dt.date(int(row[0]), today.month, today.day)
        except (ValueError, TypeError, OverflowError):
            continue
        if date >= today:
            continue
        for i in range(len(row) - 1):
            # The query groups exactly one calendar day per year.
            series = row[i + 1]
            if not isinstance(series, list) or len(series) != 1:
                continue
            value, _ = climate_value(series[0], precipitation=i == 2)
            if value is not None:
                values[i].append((value, date.year))
    result = {}
    for key, points, operation in [("high", values[0], max), ("low", values[1], min)]:
        if not points:
            raise ValueError("The climate service returned no temperature records.")
        extreme = operation(v for v, _ in points)
        result[key] = {"valueF": extreme, "years": sorted(y for v, y in points if v == extreme)}
    result["rain"] = None
    if values[2]:
        extreme = max(v for v, _ in values[2])
        result["rain"] = {
            "valueIn": extreme,
            "years": sorted(y for v, y in values[2] if v == extreme),
        }
    ranges = raw.get("meta", {}).get("valid_daterange", [])
    result["periods"] = [
        r
        for r in ranges
        if isinstance(r, list) and len(r) == 2 and iso_date(r[0]) and iso_date(r[1])
    ][:3]
    return result


def summarize_precip(rows, start, end):
    days = (end - start).days + 1 if end is not None and end >= start else 0
    actual, normal, missing, missing_normal, trace = [], [], 0, 0, False
    for n in range(days):
        date = start + dt.timedelta(days=n)
        row = rows.get(date)
        observed, is_trace = climate_value(row[3], True) if row else (None, False)
        expected, _ = climate_value(row[6], True) if row else (None, False)
        missing += observed is None
        missing_normal += expected is None
        if observed is not None:
            actual.append(observed)
        if expected is not None:
            normal.append(expected)
        trace |= is_trace
    observed = round(sum(actual), 3) if days and not missing else None
    expected = round(sum(normal), 3) if days and not missing_normal else None
    departure = (
        round(observed - expected, 3) if observed is not None and expected is not None else None
    )
    return {
        "observedIn": observed,
        "normalIn": expected,
        "departureIn": departure,
        "percentNormal": (
            round(observed / expected * 100, 1) if departure is not None and expected > 0 else None
        ),
        "trace": trace and observed == 0,
        "days": days,
        "missingDays": missing,
        "missingNormalDays": missing_normal,
        "start": start.isoformat(),
        "through": end.isoformat() if days else None,
    }


def daily_almanac(series, records, today, station=None):
    site = station_info(station)
    rows = {}
    for row in require_rows(series, station):
        if not isinstance(row, list) or len(row) != 7:
            continue
        day = iso_date(row[0])
        if day and day.year == today.year and day <= today:
            rows[day] = row
    if today not in rows:
        raise ValueError("Today's climate normals are not available.")
    current = rows[today]
    high, _ = climate_value(current[4])
    low, _ = climate_value(current[5])
    rain, _ = climate_value(current[6], True)
    if high is None or low is None or rain is None:
        raise ValueError("Today's climate normals are incomplete.")
    # Never include a partial observation for today in a full-day comparison.
    eligible = [
        d for d, row in rows.items() if d < today and climate_value(row[3], True)[0] is not None
    ]
    cutoff = max(eligible) if eligible else None
    start_year, start_month = dt.date(today.year, 1, 1), today.replace(day=1)
    precip = {
        "through": cutoff.isoformat() if cutoff else None,
        "monthName": calendar.month_name[today.month],
        "day": summarize_precip(rows, cutoff or today, cutoff),
        "month": summarize_precip(rows, start_month, cutoff),
        "year": summarize_precip(rows, start_year, cutoff),
    }
    month_normal = summarize_precip(rows, start_month, today)
    year_normal = summarize_precip(rows, start_year, today)
    monthly_normals = []
    for month in range(1, today.month + 1):
        start = dt.date(today.year, month, 1)
        through = (
            today
            if month == today.month
            else dt.date(today.year, month, calendar.monthrange(today.year, month)[1])
        )
        normal = summarize_precip(rows, start, through)
        monthly_normals.append(
            {
                "month": month,
                "start": start.isoformat(),
                "through": through.isoformat(),
                "normalIn": normal["normalIn"],
                "days": normal["days"],
                "missingNormalDays": normal["missingNormalDays"],
                "partial": month == today.month,
            }
        )
    return {
        "schemaVersion": 2,
        "date": today.isoformat(),
        "stationId": site["id"],
        "stationName": station_label(site),
        "timezone": site["timezone"],
        "normalPeriod": "1991–2020",
        "normal": {
            "highF": high,
            "lowF": low,
            "precipIn": rain,
            "rainMonthToDateIn": month_normal["normalIn"],
            "rainYearToDateIn": year_normal["normalIn"],
            "through": today.isoformat(),
            "rainMonths": monthly_normals,
        },
        "records": records_for_day(records, today, station),
        "precipitation": precip,
        "source": "NOAA / NWS · RCC-ACIS",
        "sourceUrl": site["sourceUrl"],
        "preliminary": True,
        "stale": False,
        "error": None,
    }


class BostonClimate:
    def __init__(self, directory, fetch, station=None):
        self.station = station_info(station)
        self.path = Path(directory) / (
            "boston-climate.json"
            if self.station["id"] == "BOSthr"
            else "climate-" + re.sub(r"[^A-Za-z0-9]", "", self.station["id"]) + ".json"
        )
        self.fetch = fetch
        self.lock = threading.Lock()
        self.cached = None
        self.last_attempt = 0
        self.attempt_date = None
        self.last_error = None
        try:
            if self.path.exists() and self.path.stat().st_size <= 100000:
                value = json.loads(self.path.read_text("utf-8"))
                if (
                    value.get("stationId") == self.station["id"]
                    and iso_date(value.get("date"))
                    and isinstance(value.get("records"), dict)
                ):
                    self.cached = value
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def query(self, params):
        return self.fetch(
            "https://data.rcc-acis.org/StnData?"
            + urlencode({"params": json.dumps(params, separators=(",", ":"))})
        )

    def get(self, today=None):
        today = today or local_date(zone=self.station["timezone"])
        site = self.station
        if not site.get("available", True):
            return {
                "date": today.isoformat(),
                "stationId": site["id"],
                "stationName": site["name"],
                "timezone": site["timezone"],
                "records": None,
                "precipitation": None,
                "stale": True,
                "error": "No suitable local climate station is available. Retry the location lookup in Settings.",
            }
        with self.lock:
            same_day = self.cached and self.cached["date"] == today.isoformat()
            cached_normal = (self.cached or {}).get("normal")
            monthly_normals = (
                cached_normal.get("rainMonths") if isinstance(cached_normal, dict) else None
            )
            fresh = (
                same_day
                and self.cached.get("schemaVersion") == 2
                and isinstance(monthly_normals, list)
                and len(monthly_normals) == today.month
                and 0 <= time.time() - self.cached.get("fetchedAt", 0) < 1800
            )
            retry = self.attempt_date == today and time.monotonic() - self.last_attempt < 300
            if not fresh and not retry:
                self.last_attempt, self.attempt_date = time.monotonic(), today
                try:
                    common = {"sid": site["sid"], "meta": "name,valid_daterange"}
                    series = self.query(
                        {
                            **common,
                            "sdate": f"{today.year}-01-01",
                            "edate": today.isoformat(),
                            "elems": [
                                {"name": n, "add": "f", "prec": 3} for n in ("maxt", "mint", "pcpn")
                            ]
                            + [
                                {"name": n, "normal": 91, "prec": 3}
                                for n in ("maxt", "mint", "pcpn")
                            ],
                        }
                    )
                    mmdd = today.strftime("%m-%d")
                    records = self.query(
                        {
                            **common,
                            "sdate": "por",
                            "edate": (today - dt.timedelta(days=1)).isoformat(),
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
                    result = daily_almanac(series, records, today, site)
                    result["fetchedAt"] = time.time()
                    self.cached, self.last_error = result, None
                    try:
                        write_private_json(self.path, result)
                    except OSError:
                        pass  # A disk-cache failure does not invalidate live public data.
                except Exception:
                    # Upstream errors never become fabricated normals or zero rain.
                    self.last_error = "Local climate data could not refresh. The service will retry automatically."
            if self.cached:
                result = copy.deepcopy(self.cached)
                result["stale"] = bool(
                    self.last_error
                    or result["date"] != today.isoformat()
                    or time.time() - result.get("fetchedAt", 0) > 1800
                )
                result["error"] = self.last_error
                return result
            return {
                "date": today.isoformat(),
                "stationId": site["id"],
                "stationName": station_label(site),
                "records": None,
                "precipitation": None,
                "stale": True,
                "error": self.last_error or "Local climate data is not available yet.",
                "sourceUrl": site["sourceUrl"],
            }
