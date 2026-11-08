"""Completed local days, exact normal departures, and experimental daily thirds."""

from __future__ import annotations
import calendar
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from climate import climate_value, require_rows
from historical_days import fetch_station_day
from local_store import write_private_json
from timezones import local_date
from temperature_terciles import METHOD, PERIOD, TemperatureTerciles, temperature_class


def station_temperature(summary):
    if not summary:
        return None, None, None, "No station observations returned."
    coverage = summary.get("coverage", {})
    points = [
        p
        for p in summary.get("points", [])
        if isinstance(p.get("tempF"), (int, float)) and not isinstance(p.get("tempF"), bool)
    ]
    good = (
        coverage.get("fullSpan")
        and (coverage.get("intervalSeconds") or 99999) <= 900
        and len(points) >= 60
    )
    if good:
        stamps = sorted(p["timestamp"] for p in points)
        good = (
            stamps[0] - coverage["first"] <= 1800
            and coverage["last"] - stamps[-1] <= 1800
            and max((b - a for a, b in zip(stamps, stamps[1:])), default=99999) <= 1800
        )
    high = summary.get("high", {}).get("value")
    low = summary.get("low", {}).get("value")
    if not good or high is None or low is None:
        return high, low, None, "Station temperature coverage is incomplete."
    return high, low, (high + low) / 2, None


class TemperatureCalendar:
    def __init__(self, directory, fetch, baseline_fetch=None):
        self.directory = Path(directory) / "temperature-calendar"
        self.fetch = fetch
        self.terciles = TemperatureTerciles(directory, fetch, baseline_fetch)
        self.lock = threading.RLock()
        self.states = {}
        self.selected = None

    def cancel(self):
        with self.lock:
            self.selected = None
            for state in self.states.values():
                state["cancel"].set()

    def snapshot(
        self,
        month,
        site,
        zone,
        station="",
        fetch_page=None,
        active=lambda: True,
        cached_day=lambda day: None,
    ):
        today = local_date(zone=zone)
        if not isinstance(month, str) or not re.fullmatch(r"\d{4}-\d{2}", month):
            raise ValueError("Choose a calendar month.")
        try:
            first = dt.date.fromisoformat(month + "-01")
        except ValueError:
            raise ValueError("Choose a valid calendar month.") from None
        if first.year < 1970 or first > today.replace(day=1):
            raise ValueError("Choose a month from 1970 through this month.")
        key = (month, site["id"], zone, station, today.isoformat())
        with self.lock:
            if self.selected != key:
                for other, state in self.states.items():
                    if other != key:
                        state["cancel"].set()
                self.selected = key
            state = self.states.get(key)
            if (
                state is None
                or state["cancel"].is_set()
                or not state["loading"]
                and time.monotonic() - state["checked"]
                > (300 if state["payload"].get("error") else 1800)
            ):
                rows = []
                for n in range(1, calendar.monthrange(first.year, first.month)[1] + 1):
                    day = first.replace(day=n)
                    rows.append(
                        {
                            "date": day.isoformat(),
                            "day": n,
                            "status": (
                                "future"
                                if day > today
                                else "pending" if day == today else "missing"
                            ),
                            "highF": None,
                            "lowF": None,
                            "meanF": None,
                            "normalF": None,
                            "departureF": None,
                            "lowerThirdF": None,
                            "upperThirdF": None,
                            "baselineYears": None,
                            "baselineExpectedYears": 8 if day.strftime("%m-%d") == "02-29" else 30,
                            "baselineRequiredYears": 7 if day.strftime("%m-%d") == "02-29" else 24,
                            "note": (
                                "Day in progress"
                                if day == today
                                else "Future day" if day > today else "Loading daily observations"
                            ),
                        }
                    )
                state = {
                    "cancel": threading.Event(),
                    "loading": True,
                    "checked": time.monotonic(),
                    "payload": {
                        "month": month,
                        "today": today.isoformat(),
                        "timezone": zone,
                        "stationId": site["id"],
                        "stationName": site["name"],
                        "source": "ambient" if station else "noaa",
                        "normalPeriod": PERIOD,
                        "comparisonMethod": METHOD,
                        "comparisonNote": "Experimental daily adaptation of historical thirds; not official NOAA daily categories.",
                        "baselineError": None,
                        "phase": "Loading daily observations and normals…",
                        "days": rows,
                        "completed": 0,
                        "total": sum(row["date"] < today.isoformat() for row in rows),
                        "error": None,
                    },
                }
                identity = hashlib.sha256(
                    "|".join((month, site["id"], zone, station)).encode()
                ).hexdigest()[:32]
                state["cachePath"] = self.directory / (identity + "-terciles-v1-month.json")
                try:
                    saved = json.loads(state["cachePath"].read_text())
                    if (
                        saved.get("month"),
                        saved.get("stationId"),
                        saved.get("timezone"),
                        saved.get("source"),
                        saved.get("comparisonMethod"),
                    ) == (month, site["id"], zone, "ambient" if station else "noaa", METHOD):
                        cached = {
                            d["date"]: d
                            for d in saved.get("days", [])
                            if isinstance(d, dict) and d.get("status") in ("above", "at", "below")
                            and temperature_class(d.get("meanF"), d.get("normalF"), d.get("lowerThirdF"), d.get("upperThirdF"))[0] == d["status"]
                        }
                        for day in rows:
                            if day["date"] < today.isoformat() and day["date"] in cached:
                                day.update(cached[day["date"]])
                except (OSError, ValueError, TypeError, KeyError):
                    pass
                self.states[key] = state

                def current():
                    return not state["cancel"].is_set() and active()

                threading.Thread(
                    target=self._load,
                    args=(state, first, dict(site), zone, station, fetch_page, current, cached_day),
                    daemon=True,
                ).start()
            result = copy.deepcopy(state["payload"])
            result["loading"] = state["loading"]
            if len(self.states) > 8:
                for old in list(self.states):
                    if old != key and not self.states[old]["loading"]:
                        self.states.pop(old)
                        if len(self.states) <= 8:
                            break
            return result

    def _load(self, state, first, site, zone, station, fetch_page, active, cached_day):
        try:
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not site.get("available", True):
                raise ValueError("No suitable local climate normal is available for this location.")
            month = first.strftime("%Y-%m")
            last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
            path = self.directory / (site["id"] + "-" + month + "-noaa.json")
            raw = None
            try:
                saved = json.loads(path.read_text())
                if (
                    saved.get("stationId") == site["id"]
                    and 0 <= time.time() - saved.get("fetchedAt", 0) < 1800
                ):
                    raw = saved.get("raw")
            except (OSError, ValueError, TypeError):
                pass
            if not raw:
                params = {
                    "sid": site["sid"],
                    "sdate": first.isoformat(),
                    "edate": last.isoformat(),
                    "meta": "name",
                    "elems": [{"name": n, "add": "f", "prec": 3} for n in ("maxt", "mint")]
                    + [{"name": "avgt", "normal": 91, "prec": 3}],
                }
                raw = self.fetch(
                    "https://data.rcc-acis.org/StnData?"
                    + urlencode({"params": json.dumps(params, separators=(",", ":"))})
                )
                require_rows(raw, site)
                if active():
                    try:
                        write_private_json(
                            path, {"stationId": site["id"], "fetchedAt": time.time(), "raw": raw}
                        )
                    except OSError:
                        pass
            rows = {r[0]: r for r in require_rows(raw, site) if isinstance(r, list) and len(r) == 4}
            if not active():
                return
            for day in state["payload"]["days"]:
                row = rows.get(day["date"])
                normal = climate_value(row[3])[0] if row else None
                with self.lock:
                    day["normalF"] = normal
            with self.lock:
                state["payload"]["phase"] = "Loading 1991–2020 historical thirds…"
            baseline = {}
            baseline_error = None
            try:
                baseline = self.terciles.get(site)
            except ValueError as exc:
                baseline_error = str(exc)
            if not active():
                return
            with self.lock:
                state["payload"]["baselineError"] = baseline_error
                state["payload"]["phase"] = "Loading completed daily observations…"
                for day in state["payload"]["days"]:
                    band = baseline.get(day["date"][5:])
                    if band:
                        day.update(band)
            for day in reversed(state["payload"]["days"]):
                if not active():
                    return
                if day["status"] in ("future", "pending"):
                    continue
                error = None
                high = low = mean = None
                service_failed = False
                if station:
                    date = dt.date.fromisoformat(day["date"])
                    identity = hashlib.sha256(
                        (station + "|" + zone + "|" + day["date"]).encode()
                    ).hexdigest()[:32]
                    cached_path = self.directory / (identity + ".json")
                    saved = None
                    try:
                        value = json.loads(cached_path.read_text())
                        if (
                            value.get("station") == station
                            and value.get("timezone") == zone
                            and value.get("date") == day["date"]
                            and (
                                value.get("meanF") is not None
                                or time.time() - value.get("fetchedAt", 0) < 3600
                            )
                        ):
                            saved = value
                    except (OSError, ValueError, TypeError):
                        pass
                    if saved:
                        high, low, mean, error = (
                            saved.get(n) for n in ("highF", "lowF", "meanF", "note")
                        )
                    else:
                        try:
                            summary = cached_day(date)
                            high, low, mean, error = station_temperature(summary)
                            if mean is None:
                                summary = fetch_station_day(fetch_page, date, zone, active)
                                high, low, mean, error = station_temperature(summary)
                            if active():
                                try:
                                    write_private_json(
                                        cached_path,
                                        {
                                            "station": station,
                                            "timezone": zone,
                                            "date": day["date"],
                                            "highF": high,
                                            "lowF": low,
                                            "meanF": mean,
                                            "note": error,
                                            "fetchedAt": time.time(),
                                        },
                                    )
                                except OSError:
                                    pass
                        except Exception:
                            error = "Station day could not load. Saved days remain available; retry after the service recovers."
                            service_failed = True
                else:
                    row = rows.get(day["date"])
                    high = climate_value(row[1])[0] if row else None
                    low = climate_value(row[2])[0] if row else None
                    mean = (high + low) / 2 if high is not None and low is not None else None
                    if mean is None:
                        error = "The daily archive has not reported this day yet; it can lag several weeks."
                status, departure = temperature_class(mean, day["normalF"], day["lowerThirdF"], day["upperThirdF"])
                if mean is not None and status == "missing":
                    error = baseline_error or (
                        f"Historical thirds unavailable: {day['baselineYears'] or 0} of "
                        f"{day['baselineExpectedYears']} reference years; at least "
                        f"{day['baselineRequiredYears']} required."
                    )
                elif day["normalF"] is None and not error:
                    error = "Daily normal unavailable; exact departure cannot be calculated."
                if not active():
                    return
                with self.lock:
                    day.update(
                        {
                            "highF": high,
                            "lowF": low,
                            "meanF": mean,
                            "departureF": departure,
                            "status": status,
                            "note": error,
                        }
                    )
                    state["payload"]["completed"] += 1
                    if service_failed:
                        state["payload"][
                            "error"
                        ] = "Station history paused. Retrying in five minutes; already saved days are retained."
                if service_failed:
                    break
        except Exception as exc:
            if active():
                with self.lock:
                    state["payload"]["error"] = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "Calendar data could not load. It will retry automatically."
                    )
        finally:
            if active():
                try:
                    write_private_json(state["cachePath"], state["payload"])
                except OSError:
                    pass
            with self.lock:
                state["loading"] = False
                state["payload"]["phase"] = None
