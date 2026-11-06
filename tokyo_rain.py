"""A small, independent Tokyo weather report for the Radio Ham Easter egg."""
from __future__ import annotations

import datetime as dt
import math
import re
import threading
import time

URL = "https://aviationweather.gov/api/data/metar?ids=RJTT&format=json"
SOURCE = "NOAA Aviation Weather Center · Tokyo Haneda (RJTT)"
MAX_AGE = 90 * 60
REFRESH = 10 * 60
RETRY = 2 * 60
UTC = dt.timezone.utc
WEATHER = re.compile(
    r"[+-]?(?:VC|RE)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS){0,3}"
)


def number(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def parse_report(rows, now):
    """Use present weather only: recent weather, trends and remarks aren't rain now."""
    if not isinstance(rows, list):
        raise ValueError("No Tokyo observation")
    candidates = [r for r in rows if isinstance(r, dict) and r.get("icaoId") == "RJTT"
                  and number(r.get("obsTime"))]
    if not candidates:
        raise ValueError("No Tokyo observation")
    row = max(candidates, key=lambda r: r["obsTime"])
    stamp = row["obsTime"]
    if stamp <= 0 or stamp > now + 300 or now - stamp > 86400:
        raise ValueError("Invalid Tokyo observation time")
    raw = row.get("rawOb")
    if not isinstance(raw, str) or len(raw) > 2048:
        raise ValueError("Missing Tokyo report")
    raw = raw.strip().upper().rstrip("=")
    header = re.match(r"^(?:(?:METAR|SPECI) (?:COR )?)?RJTT (\d{6})Z(?: |$)", raw)
    if not header or header[1] != dt.datetime.fromtimestamp(stamp, UTC).strftime("%d%H%M"):
        raise ValueError("Tokyo report identity or time differs")
    body = re.split(r"\b(?:RMK|NOSIG|BECMG|TEMPO|PROB\d{2})\b", raw[header.end():], maxsplit=1)[0]
    tokens = body.split()
    if "NIL" in tokens or not number(row.get("temp")) or row.get("visib") in (None, ""):
        raise ValueError("Incomplete Tokyo observation")
    # Missing weather instruments must not become a no-rain report.
    unknown = any(t in {"//", "///", "////"} for t in tokens)
    groups = [t for t in tokens if WEATHER.fullmatch(t)]
    current = [t.lstrip("+-") for t in groups if not t.lstrip("+-").startswith(("RE", "VC"))]
    if any("RA" in t or "DZ" in t for t in current):
        condition = "rain"
    elif any("UP" in t or t == "SH" for t in current) or unknown:
        condition = "unknown"
    elif any(any(p in t for p in ("SN", "SG", "IC", "PL", "GR", "GS"))
             and not t.startswith(("BL", "DR")) for t in current):
        condition = "wintry"
    elif any(t.lstrip("+-").startswith("VC") and
             any(p in t for p in ("RA", "DZ", "SH", "SN", "UP")) for t in groups):
        condition = "nearby"
    else:
        condition = "no_rain"
    return {"condition": condition, "observedAt": stamp, "temperatureC": row["temp"],
            "weatherCode": " ".join(groups), "rawReport": raw}


class TokyoRain:
    """One small, cached public request, separate from local station data and locks."""
    def __init__(self, fetch, clock=time.time):
        self.fetch, self.clock = fetch, clock
        self.lock = threading.Lock()
        self.report = None
        self.next_check = self.checked_at = 0
        self.failed = False

    def get(self):
        with self.lock:
            now = self.clock()
            if now >= self.next_check:
                try:
                    self.report = parse_report(self.fetch(URL), now)
                    self.failed = False
                except Exception:
                    # A nonessential Easter egg must never interrupt the dashboard.
                    self.failed = True
                self.checked_at = self.clock()
                self.next_check = self.checked_at + (RETRY if self.failed else REFRESH)
            now = self.clock()
            result = dict(self.report or {})
            status = "unavailable" if self.failed or not self.report else "ok"
            if status == "ok" and (now - result["observedAt"] > MAX_AGE or result["observedAt"] > now + 300):
                status = "stale"
            result.update(status=status, checkedAt=self.checked_at, source=SOURCE,
                          sourceUrl=URL, maxAgeSeconds=MAX_AGE)
            return result
