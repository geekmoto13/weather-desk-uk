"""Elapsed time since measured rain, preserving source identity and data gaps."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import threading

from local_store import write_private_json
from timezones import local_date, midnight


def valid(value, low=0, high=1e12):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and low <= value <= high)


def rain_time(value, through):
    """Ambient lastRain is an ISO timestamp; naive/future/invalid times stay unknown."""
    if not isinstance(value, str) or not valid(through):
        return None
    try:
        when = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        stamp = when.timestamp() if when.tzinfo else None
        # Ambient observation times may be rounded down to the minute.
        return stamp if valid(stamp, 1, through+59) else None
    except (ValueError, OverflowError, OSError):
        return None


def interval_rain_since(rows, interval_seconds, now):
    """Count backwards through complete dry intervals; no gaps become dry zeros."""
    stamps = [t for t in rows if valid(t, 1, now)]
    if not stamps:
        return {"status": "unavailable", "seconds": None, "asOf": None}
    end = max(stamps)
    cursor = end
    def amount(t):
        value = rows.get(t)
        return value[0] if isinstance(value, (tuple, list)) else value
    while cursor in rows and valid(amount(cursor), 0, 3000):
        if amount(cursor) > 0:
            return {"status": "known", "seconds": end-cursor, "asOf": end,
                    "lastRainAt": cursor, "lastRainStart": cursor-interval_seconds,
                    "approximate": True, "resolutionSeconds": interval_seconds}
        cursor -= interval_seconds
    seconds = end-cursor
    return {"status": "at_least" if seconds else "unavailable", "seconds": seconds or None,
            "asOf": end, "lastRainAt": None, "approximate": False,
            "resolutionSeconds": interval_seconds}


class RainMemory:
    """Keep the last wet bounds and contiguous dry coverage for each real station."""
    def __init__(self, directory):
        self.directory = Path(directory) / "rain-since"
        self.lock = threading.RLock()
        self.states = {}

    @staticmethod
    def _dry(state, start, end):
        if end <= start:
            return
        a, b = state.get("dryStart"), state.get("dryEnd")
        if a is not None and start <= b and end >= a:
            state["dryStart"], state["dryEnd"] = min(start, a), max(end, b)
        elif b is None or end > b:
            state["dryStart"], state["dryEnd"] = start, end
        # A zero rolling window can narrow a preceding positive rolling window.
        high, low = state.get("wetEnd"), state.get("wetStart")
        if high is not None and start < high <= end:
            state["wetEnd"] = max(low, start)

    @staticmethod
    def _wet(state, start, end):
        old_end = state.get("wetEnd")
        if old_end is None or end >= old_end:
            state["wetStart"] = max(start, state.get("wetStart", start))
            state["wetEnd"] = end

    def snapshot(self, identity, source, current, history, now, timezone):
        key = hashlib.sha256(identity.encode()).hexdigest()[:24]
        path = self.directory / (key + ".json")
        with self.lock:
            if key not in self.states:
                try:
                    raw = json.loads(path.read_text())
                    state = raw["state"] if raw.get("identity") == identity and isinstance(raw.get("state"), dict) else {}
                except (OSError, ValueError, KeyError, TypeError):
                    state = {}
                self.states[key] = state
            state = copy.deepcopy(self.states[key])
            before = copy.deepcopy(state)
            current = current or {}
            through = current.get("timestamp")
            native = current.get("lastRainAt")
            if valid(through, 1, now+300) and valid(native, 1, min(through+59, now+59)):
                through = max(through, native)
                state.update(wetStart=native, wetEnd=native, dryStart=native, dryEnd=through,
                             nativeThrough=through)
                result = {"status": "known", "seconds": max(0, through-native),
                          "asOf": through, "lastRainAt": native, "lastRainStart": native,
                          "approximate": False}
            else:
                rows = {p["timestamp"]: p for p in history or [] if isinstance(p, dict)
                        and valid(p.get("timestamp"), 1, now+300)}
                if valid(through, 1, now+300):
                    rows[through] = current
                previous = state.get("lastRow")
                for stamp, row in sorted(rows.items()):
                    if stamp <= state.get("nativeThrough", 0):
                        continue
                    rain = [(seconds, row.get(field)) for field, seconds in (
                        ("rainLastHourMm", 3600), ("rainLast3hMm", 10800),
                        ("rainLast6hMm", 21600), ("rainLast24hMm", 86400))
                        if valid(row.get(field), 0, 3000)]
                    positives = [(span, amount) for span, amount in rain if amount > 0]
                    if positives and any(amount == 0 and span >= min(p[0] for p in positives) for span, amount in rain):
                        # Contradictory overlapping totals do not establish a dry period.
                        continue
                    if positives:
                        span = min(span for span, _ in positives)
                        self._wet(state, stamp-span, stamp)
                    day = row.get("stationLocalDay")
                    zone = row.get("stationTimezone")
                    if not day and zone:
                        try:
                            day = local_date(stamp, zone=zone).isoformat()
                        except (ValueError, TypeError):
                            pass
                    daily = row.get("rainDayMm")
                    if valid(daily, 0, 3000):
                        same_day = previous and day and day == previous.get("day") and previous["timestamp"] < stamp
                        if same_day and valid(previous.get("daily"), 0, 3000) and daily < previous["daily"]:
                            state["resetDay"] = day
                        if same_day and valid(previous.get("daily"), 0, 3000):
                            if daily > previous["daily"]:
                                self._wet(state, previous["timestamp"], stamp)
                            elif daily == previous["daily"]:
                                self._dry(state, previous["timestamp"], stamp)
                        elif daily > 0 and not positives and not same_day:
                            try:
                                start = midnight(dt.date.fromisoformat(day), zone) if zone and day else stamp-26*3600
                            except (ValueError, TypeError):
                                start = stamp-26*3600
                            self._wet(state, start, stamp)
                        if daily == 0 and zone and day and state.get("resetDay") != day:
                            try:
                                self._dry(state, midnight(dt.date.fromisoformat(day), zone), stamp)
                            except (ValueError, TypeError):
                                pass
                        previous = {"timestamp": stamp, "daily": daily, "day": day}
                    for span, amount in rain:
                        if amount == 0:
                            self._dry(state, stamp-span, stamp)
                if previous and previous.get("timestamp", 0) >= (state.get("lastRow") or {}).get("timestamp", 0):
                    state["lastRow"] = previous
                a, b = state.get("dryStart"), state.get("dryEnd")
                wet, low = state.get("wetEnd"), state.get("wetStart")
                if wet is not None and (b is None or wet >= b):
                    result = {"status": "known", "seconds": 0, "asOf": wet,
                              "lastRainAt": wet, "lastRainStart": low, "approximate": True}
                elif wet is not None and a is not None and a <= wet <= b:
                    result = {"status": "known", "seconds": b-wet, "asOf": b,
                              "lastRainAt": wet, "lastRainStart": low, "approximate": wet != low}
                elif a is not None:
                    result = {"status": "at_least", "seconds": b-a, "asOf": b,
                              "lastRainAt": None, "approximate": False}
                else:
                    result = {"status": "unavailable", "seconds": None, "asOf": through}
            if state != before:
                self.states[key] = state
                try:
                    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    write_private_json(path, {"identity": identity, "state": state})
                except OSError:
                    pass
        result["source"] = source
        return result
