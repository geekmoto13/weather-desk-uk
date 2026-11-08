"""Experimental same-date empirical thirds, not official NOAA daily categories.

One paired high/low daily mean per year, 1991--2020. Quantiles use linear
interpolation at (n-1)*p. At least 80% of the possible years are required:
24/30 for ordinary dates and 7/8 for February 29. No neighbouring dates,
missing observations or fitted probability distributions are substituted.
"""

from __future__ import annotations
import calendar
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import urlencode

from climate import climate_value, require_rows
from local_store import write_private_json

METHOD = "daily-terciles-1991-2020-v1"
START_YEAR, END_YEAR = 1991, 2020
PERIOD = "1991–2020"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def quantile(values, probability):
    """Hyndman--Fan type 7; invariant to conversion between Fahrenheit/Celsius."""
    values = sorted(values)
    if not values or not 0 <= probability <= 1 or not all(finite(v) for v in values):
        raise ValueError("A percentile needs finite observations and a probability from 0 to 1.")
    position = (len(values) - 1) * probability
    left = math.floor(position)
    return values[left] + (values[math.ceil(position)] - values[left]) * (position - left)


def calendar_keys():
    for month in range(1, 13):
        for day in range(1, calendar.monthrange(2000, month)[1] + 1):
            yield f"{month:02d}-{day:02d}"


def cutoffs(raw, site):
    """Exclude unpaired, accumulated, impossible, duplicate or out-of-period days."""
    dates, duplicates = {}, set()
    for row in require_rows(raw, site):
        if not isinstance(row, list) or len(row) != 3:
            continue
        try:
            date = dt.date.fromisoformat(row[0])
        except (ValueError, TypeError):
            continue
        if not START_YEAR <= date.year <= END_YEAR:
            continue
        if date in dates:
            duplicates.add(date)
        high, low = (climate_value(v)[0] for v in row[1:])
        dates[date] = (high + low) / 2 if high is not None and low is not None and high >= low else None
    samples = {}
    for date, mean in dates.items():
        if mean is not None and date not in duplicates:
            samples.setdefault(date.strftime("%m-%d"), []).append(mean)
    result = {}
    for key in calendar_keys():
        values = samples.get(key, [])
        possible = 8 if key == "02-29" else 30
        required = math.ceil(possible * 0.8)
        usable = len(values) >= required
        result[key] = {
            "lowerThirdF": quantile(values, 1 / 3) if usable else None,
            "upperThirdF": quantile(values, 2 / 3) if usable else None,
            "baselineYears": len(values),
            "baselineExpectedYears": possible,
            "baselineRequiredYears": required,
        }
    return result


def temperature_class(mean, normal, lower=None, upper=None):
    departure = round(mean - normal, 4) if finite(mean) and finite(normal) else None
    if not all(finite(v) for v in (mean, lower, upper)) or lower > upper:
        return "missing", departure
    # Both endpoints belong to green. A tiny epsilon only absorbs floating-point
    # roundoff from unit conversion; it is not a meteorological tolerance.
    epsilon = 1e-9
    return ("below" if mean < lower - epsilon else "above" if mean > upper + epsilon else "at"), departure


class TemperatureTerciles:
    def __init__(self, directory, fetch, baseline_fetch=None):
        self.directory = Path(directory) / "temperature-calendar" / "terciles"
        self.fetch = fetch
        self.baseline_fetch = baseline_fetch
        self.lock = threading.Lock()
        self.cache = {}
        self.failures = {}

    @staticmethod
    def identity(site):
        return {
            "method": METHOD,
            "stationId": site["id"],
            "stationSid": site["sid"],
            "stationName": site["name"],
            "period": PERIOD,
            "quantile": "linear-(n-1)*p",
            "minimumCoverage": 0.8,
        }

    @staticmethod
    def valid(saved, identity):
        if not isinstance(saved, dict) or any(saved.get(k) != v for k, v in identity.items()):
            return False
        days = saved.get("days")
        if not isinstance(days, dict) or set(days) != set(calendar_keys()):
            return False
        for key, row in days.items():
            if not isinstance(row, dict):
                return False
            expected = 8 if key == "02-29" else 30
            required = math.ceil(expected * 0.8)
            count = row.get("baselineYears")
            if type(count) is not int or not 0 <= count <= expected:
                return False
            if row.get("baselineExpectedYears") != expected or row.get("baselineRequiredYears") != required:
                return False
            lower, upper = row.get("lowerThirdF"), row.get("upperThirdF")
            if count >= required:
                if not all(finite(v) for v in (lower, upper)) or not -150 <= lower <= upper <= 160:
                    return False
            elif lower is not None or upper is not None:
                return False
        return any(d["lowerThirdF"] is not None for d in days.values())

    def get(self, site):
        identity = self.identity(site)
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:32]
        with self.lock:
            if key in self.cache:
                return self.cache[key]
            path = self.directory / (key + ".json")
            try:
                saved = json.loads(path.read_text())
                if self.valid(saved, identity):
                    self.cache[key] = saved["days"]
                    return saved["days"]
            except (OSError, ValueError, TypeError):
                pass
            failed = self.failures.get(key)
            if failed and time.monotonic() - failed[0] < 300:
                raise ValueError(failed[1])
            try:
                if self.baseline_fetch:
                    raw = self.baseline_fetch(site)
                else:
                    params = {
                        "sid": site["sid"], "sdate": "1991-01-01", "edate": "2020-12-31",
                        "meta": "name",
                        "elems": [{"name": name, "add": "f", "prec": 3} for name in ("maxt", "mint")],
                    }
                    raw = self.fetch("https://data.rcc-acis.org/StnData?" + urlencode({"params": json.dumps(params, separators=(",", ":"))}))
                days = cutoffs(raw, site)
                saved = {**identity, "createdAt": time.time(), "days": days}
                if not self.valid(saved, identity):
                    raise ValueError("Not enough paired 1991–2020 temperatures at this reference station.")
                self.cache[key] = days
                self.failures.pop(key, None)
                try:
                    self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                    write_private_json(path, saved)
                except OSError:
                    pass
                return days
            except Exception as exc:
                message = (str(exc) if isinstance(exc, ValueError) else
                           "Historical temperature thirds unavailable. Retrying in five minutes; no substitute colours are used.")
                self.failures[key] = (time.monotonic(), message)
                raise ValueError(message) from None
