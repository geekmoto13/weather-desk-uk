"""UK station history from GHCN-Daily; calculated, explicitly labelled references.

The query adapter retains the established dashboard's date-window calculations.
It is entirely local: UK queries are never sent to the US RCC-ACIS service.
"""

from __future__ import annotations
import calendar
import copy
import datetime as dt
import json
from pathlib import Path
import threading
import time
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
from ireland_climate import convert_daily, SOURCE as IRISH_SOURCE

ROOT = Path(__file__).parent
CATALOGUE = json.loads((ROOT / "data/uk-climate-stations.json").read_text())
STATIONS = {s["id"]: s for s in CATALOGUE["stations"]}
STATIONS.update(
    {
        s["id"]: s
        for s in json.loads((ROOT / "data/ireland-climate-stations.json").read_text())["stations"]
    }
)
SOURCE = "NOAA NCEI GHCN-Daily · UK observations"


def get_text(url):
    with urlopen(
        Request(url, headers={"User-Agent": "WeatherDeskUK/1.0 (local weather dashboard)"}),
        timeout=20,
    ) as r:
        raw = r.read(12_000_001)
    if len(raw) > 12_000_000:
        raise ValueError("The climate archive is too large.")
    return raw.decode("utf-8", errors="strict")


def profile(sid, distance=None):
    s = STATIONS[sid]
    return {
        "id": sid,
        "sid": sid + " 6",
        "name": s["name"],
        "label": s["name"].title(),
        "timezone": "UTC" if s.get("country") == "IE" else "Europe/London",
        "sourceUrl": s["daily_url"],
        "available": True,
        "recordScale": 254,
        "distanceKm": distance,
        "periods": [
            [s["audit"][field]["first_valid"], s["audit"][field]["latest_valid"]]
            for field in ("TMAX", "TMIN", "PRCP")
        ],
    }


DEFAULT_CLIMATE = profile(CATALOGUE["default_station_id"])


def parse_daily(text, sid):
    """Values are converted once; missing, QC failures and multi-day rain stay absent."""
    days = {}
    seen = False
    for line in text.splitlines():
        if len(line) < 269 or line[:11] != sid:
            continue
        field = line[17:21]
        if field not in ("TMAX", "TMIN", "PRCP"):
            continue
        try:
            y, m = int(line[11:15]), int(line[15:17])
            count = calendar.monthrange(y, m)[1]
        except (ValueError, OverflowError):
            continue
        seen = True
        for n in range(1, count + 1):
            part = line[21 + (n - 1) * 8 : 21 + n * 8]
            try:
                value = int(part[:5])
            except ValueError:
                continue
            if value == -9999 or part[6] != " " or field == "PRCP" and part[5] == "P":
                continue
            if field == "PRCP" and value < 0:
                continue
            if field != "PRCP" and not -1000 <= value <= 700:
                continue
            raw = value / 10
            converted = raw / 25.4 if field == "PRCP" else raw * 1.8 + 32
            days.setdefault(dt.date(y, m, n), {})[field] = (
                "T" if field == "PRCP" and part[5] == "T" else converted
            )
    if not seen:
        raise ValueError("The archive returned a different or unreadable station.")
    return days


def references(days):
    values = {}
    for day, fields in days.items():
        if 1991 <= day.year <= 2020:
            for field, value in fields.items():
                values.setdefault((day.month, day.day, field), []).append(
                    0 if value == "T" else value
                )
    result = {}
    for key, series in values.items():
        needed = 7 if key[:2] == (2, 29) else 24
        if len(series) >= needed:
            result[key] = sum(series) / len(series)
    return result


class UKArchive:
    def __init__(self, directory, fetch_text=get_text):
        self.directory = Path(directory) / "uk-ghcn"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fetch_text = fetch_text
        self.cache = {}
        self.attempt = {}
        self.lock = threading.RLock()

    def load(self, sid):
        if sid not in STATIONS:
            raise ValueError("Select a supported UK climate reference.")
        with self.lock:
            if sid in self.cache and time.monotonic() - self.cache[sid]["checked"] < 86400:
                return self.cache[sid]
            path = self.directory / (sid + ".dly")
            text = None
            if path.exists():
                text = path.read_text()
            elif (ROOT / "data" / ("seed-" + sid + ".dly")).exists():
                text = (ROOT / "data" / ("seed-" + sid + ".dly")).read_text()
                # The public seed gives the initial location immediate historical references.
                # It is refreshed on the next run/day, and its actual last date is always shown.
                path.write_text(text)
                path.chmod(0o600)
            if text is None or time.time() - path.stat().st_mtime > 86400:
                if time.monotonic() - self.attempt.get(sid, -1e10) > 300:
                    self.attempt[sid] = time.monotonic()
                    try:
                        candidate = self.fetch_text(STATIONS[sid]["daily_url"])
                        if STATIONS[sid].get("format") == "met-daily-csv":
                            candidate = convert_daily(candidate, sid)
                        parse_daily(candidate, sid)
                        path.write_text(candidate)
                        path.chmod(0o600)
                        text = candidate
                    except Exception:
                        if text is None:
                            raise ValueError(
                                "The UK climate archive could not be downloaded. Retry shortly."
                            ) from None
            if text is None:
                raise ValueError("The UK climate archive is unavailable.")
            days = parse_daily(text, sid)
            item = {
                "days": days,
                "normal": references(days),
                "checked": time.monotonic(),
                "through": max(days).isoformat(),
                "first": min(days).isoformat(),
            }
            self.cache[sid] = item
            return item

    def metadata(self, sid):
        item = self.load(sid)
        return {
            "archiveThrough": item["through"],
            "archiveStart": item["first"],
            "referenceMethod": "Arithmetic daily averages, 1991–2020; minimum 24 of 30 years (7 of 8 leap days).",
            "source": IRISH_SOURCE if STATIONS[sid].get("country") == "IE" else SOURCE,
            "normalPeriod": "1991–2020 (calculated)",
        }

    def temperature_baseline(self, site):
        """Reuse the named UK/Irish archive; never send this request to US ACIS."""
        sid = site["id"]
        item = self.load(sid)
        return {
            "meta": {"name": STATIONS[sid]["name"]},
            "data": [
                [day.isoformat(), fields.get("TMAX", "M"), fields.get("TMIN", "M")]
                for day, fields in sorted(item["days"].items())
                if 1991 <= day.year <= 2020
            ],
        }

    def query(self, url):
        params = json.loads(parse_qs(urlsplit(url).query)["params"][0])
        sid = params["sid"].split()[0]
        item = self.load(sid)
        days, normals = item["days"], item["normal"]
        elements = params["elems"]
        fields = {"maxt": "TMAX", "mint": "TMIN", "pcpn": "PRCP"}

        def observed(day, name):
            if name == "avgt":
                values = [days.get(day, {}).get(f) for f in ("TMAX", "TMIN")]
                return sum(values) / 2 if all(isinstance(v, (int, float)) for v in values) else "M"
            return days.get(day, {}).get(fields[name], "M")

        def normal(day, name):
            if name == "avgt":
                values = [normals.get((day.month, day.day, f)) for f in ("TMAX", "TMIN")]
                return sum(values) / 2 if all(v is not None for v in values) else "M"
            return normals.get((day.month, day.day, fields[name]), "M")

        rows = []
        end = dt.date.fromisoformat(params.get("edate", params.get("date")))
        if elements[0].get("groupby"):
            mm, dd = map(int, elements[0]["groupby"][1].split("-"))
            for year in range(min(days).year, end.year + 1):
                try:
                    day = dt.date(year, mm, dd)
                except ValueError:
                    continue
                if day > end:
                    continue
                row = [str(year)]
                for elem in elements:
                    duration = elem.get("duration", "dly")
                    if duration == "dly":
                        row.append([observed(day, elem["name"])])
                        continue
                    start = day.replace(day=1) if duration == "mtd" else dt.date(year, 1, 1)
                    series = [
                        observed(start + dt.timedelta(days=n), elem["name"])
                        for n in range((day - start).days + 1)
                    ]
                    missing = sum(v == "M" for v in series)
                    value = "M" if missing else sum(0 if v == "T" else v for v in series)
                    if value == 0 and "T" in series:
                        value = "T"
                    row.append([[value, missing]])
                rows.append(row)
        else:
            start = dt.date.fromisoformat(params.get("sdate", params.get("date")))
            if (end - start).days > 370:
                raise ValueError("Request one calendar year at a time.")
            for n in range((end - start).days + 1):
                day = start + dt.timedelta(days=n)
                rows.append(
                    [day.isoformat()]
                    + [
                        (normal(day, e["name"]) if e.get("normal") else observed(day, e["name"]))
                        for e in elements
                    ]
                )
        return {
            "meta": {"name": STATIONS[sid]["name"], "valid_daterange": profile(sid)["periods"]},
            "data": rows,
        }

    def enrich(self, value, sid):
        result = copy.deepcopy(value)
        try:
            result.update(self.metadata(sid))
        except Exception:
            pass
        return result


class ClimateView:
    def __init__(self, inner, archive):
        self.inner = inner
        self.archive = archive

    def get(self, day=None):
        return self.archive.enrich(self.inner.get(day), self.inner.station["id"])

    def __getattr__(self, name):
        return getattr(self.inner, name)
