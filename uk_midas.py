"""Local MIDAS Open daily-file imports; observation periods are never mixed with NOAA.

CEDA requires a registered account to download observations. This reader accepts
the downloaded BADC CSVs (or ZIPs of them), keeps their source IDs and ending dates,
and stores accepted version-1 observations in a private SQLite database.
"""

from __future__ import annotations
import base64
import calendar
import csv
import datetime as dt
import io
import math
from pathlib import Path
import re
import sqlite3
import threading
import zipfile

CATALOGUE = "https://catalogue.ceda.ac.uk/uuid/dbd451271eb04662beade68da43546e1/"
TEMP_URL = "https://catalogue.ceda.ac.uk/uuid/1854bb17ec454841b04e243a1352f25a/"
RAIN_URL = "https://catalogue.ceda.ac.uk/uuid/88b5bdf6131e4c1cb7acb8bde984deda/"
NOTE = (
    "MIDAS observations below use 24 hours ending 09:00 UTC on the displayed date, "
    "including in summer. No date throwback is applied. They remain separate from "
    "NOAA calendar-date records and your personal station. Only imported files are "
    "included; these are archive extrema, not certified national records. "
    "Calculated averages require 24 of the 30 years 1991–2020 (7 of 8 leap days)."
)


def quality(raw):
    """Conservative observed-only policy for the documented MESQL quality code."""
    raw = str(raw).strip()
    if not re.fullmatch(r"\d{1,5}", raw):
        return None
    m, e, s, q, l = map(int, raw.zfill(5))
    if m not in (0, 1) or e in (1, 2, 4, 7) or s in (1, 2, 3, 4):
        return None
    return {"trace": m == 1, "corrected": e in (3, 5, 6) or s in (5, 6, 7)}


def parse_file(name, raw):
    if len(raw) > 8_000_000:
        raise ValueError("One MIDAS CSV exceeds 8 MB. Import a smaller set of yearly files.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        raise ValueError("MIDAS files must be UTF-8 CSVs.") from None
    rows = list(csv.reader(io.StringIO(text)))
    metadata = {}
    missing = {}
    header = None
    start = 0
    for i, row in enumerate(rows):
        cells = [s.strip() for s in row]
        if not cells:
            continue
        key = cells[0].lower()
        if len(cells) > 2 and cells[1].lower() == "g":
            metadata[key] = ", ".join(cells[2:])[:200]
        if key in ("missing_value", "_fillvalue") and len(cells) > 2:
            missing[cells[1].lower()] = cells[2]
        lowered = [s.lower() for s in cells]
        if "src_id" in lowered and ("max_air_temp" in lowered or "prcp_amt" in lowered):
            header = lowered
            start = i + 1
            break
    if header is None:
        raise ValueError(
            "Choose MIDAS Open daily-temperature or daily-rain observation CSVs, not station metadata files."
        )
    temperature = "max_air_temp" in header
    needed = (
        {
            "src_id",
            "version_num",
            "ob_end_time",
            "ob_hour_count",
            "max_air_temp_q",
            "min_air_temp_q",
        }
        if temperature
        else {
            "src_id",
            "version_num",
            "ob_date",
            "ob_end_ctime",
            "ob_day_cnt",
            "prcp_amt_q",
            "ob_day_cnt_q",
        }
    )
    if not needed.issubset(header):
        raise ValueError("The MIDAS CSV is missing observation-period or quality columns.")
    release_match = re.search(r"_dv-(\d{6})_", name)
    release = int(release_match[1]) if release_match else 0
    file_station = re.search(r"_(\d{5,6})_([\w-]+)_qcv-", Path(name).name)
    observations = {}
    rejected = 0
    for cells in rows[start:]:
        if cells and cells[0].strip().lower() == "end data":
            break
        if not cells or not any(c.strip() for c in cells):
            continue
        r = dict(zip(header, (v.strip() for v in cells)))
        try:
            sid = str(int(r["src_id"]))
            if sid == "99999" or r.get("version_num") != "1":
                raise ValueError()
            if file_station and int(file_station[1]) != int(sid):
                raise ValueError()
            if temperature:
                end = dt.datetime.fromisoformat(r["ob_end_time"].replace("Z", "+00:00"))
                if end.tzinfo and end.utcoffset() != dt.timedelta(0):
                    raise ValueError()
                if float(r["ob_hour_count"]) != 24 or (end.hour, end.minute, end.second) != (
                    9,
                    0,
                    0,
                ):
                    raise ValueError()
                day = end.date()
            else:
                day = dt.date.fromisoformat(r["ob_date"][:10])
                if (
                    float(r["ob_day_cnt"]) != 1
                    or r["ob_end_ctime"].replace(":", "").zfill(4) != "0900"
                ):
                    raise ValueError()
                if quality(r["ob_day_cnt_q"]) is None:
                    raise ValueError()
            if not 1850 <= day.year <= dt.date.today().year:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            rejected += 1
            continue
        fields = (
            (("max_air_temp", "highC"), ("min_air_temp", "lowC"))
            if temperature
            else (("prcp_amt", "rainMm"),)
        )
        kept = False
        for source, field in fields:
            flag = quality(r.get(source + "_q", ""))
            value = r.get(source, "")
            if not flag or not value or value == missing.get(source):
                continue
            try:
                value = float(value)
            except ValueError:
                continue
            if not math.isfinite(value) or not (
                0 <= value <= 2000 if field == "rainMm" else -90 <= value <= 60
            ):
                continue
            if flag["trace"] and field != "rainMm":
                continue
            item = (0.0 if flag["trace"] else value, flag["trace"], flag["corrected"], release)
            key = (sid, day.isoformat(), field)
            if key in observations and observations[key] != item:
                raise ValueError(
                    "Conflicting MIDAS records for the same station, period and field. Import one release of quality-version-1 files."
                )
            observations[key] = item
            kept = True
        if not kept:
            rejected += 1
    if not observations:
        raise ValueError(
            "No supported, usable version-1 observations were found. Use 24-hour daily files ending at 09:00 UTC."
        )
    station_names = {
        sid: metadata.get("location_name")
        or metadata.get("station_name")
        or (file_station[2].replace("-", " ").title() if file_station else "MIDAS station " + sid)
        for sid, _, _ in observations
    }
    return observations, station_names, rejected


def unpack(files):
    if not isinstance(files, list) or not 1 <= len(files) <= 300:
        raise ValueError("Choose 1–300 MIDAS CSV files, or a ZIP containing them.")
    result = []
    expanded = 0
    for f in files:
        if (
            not isinstance(f, dict)
            or not isinstance(f.get("name"), str)
            or not isinstance(f.get("data"), str)
        ):
            raise ValueError("Unreadable import.")
        try:
            raw = base64.b64decode(f["data"], validate=True)
        except ValueError:
            raise ValueError("Unreadable import data.") from None
        if len(raw) > 12_000_000:
            raise ValueError("Import at most 12 MB at once.")
        if f["name"].lower().endswith(".zip"):
            try:
                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                    members = [
                        v
                        for v in z.infolist()
                        if not v.is_dir()
                        and v.filename.lower().endswith(".csv")
                        and "__MACOSX/" not in v.filename
                    ]
                    if not members or len(members) > 300:
                        raise ValueError("A MIDAS ZIP must contain 1–300 observation CSV files.")
                    for m in members:
                        expanded += m.file_size
                        if m.file_size > 8_000_000 or expanded > 64_000_000:
                            raise ValueError(
                                "The expanded import is too large. Choose fewer yearly files."
                            )
                        result.append((m.filename, z.read(m)))
            except (zipfile.BadZipFile, RuntimeError):
                raise ValueError("The ZIP could not be read.") from None
        elif f["name"].lower().endswith(".csv"):
            expanded += len(raw)
            result.append((f["name"], raw))
        else:
            raise ValueError("Choose CSV or ZIP files downloaded from MIDAS Open.")
    if len(result) > 300 or expanded > 64_000_000:
        raise ValueError("Import fewer yearly files at once.")
    return result


class MidasArchive:
    def __init__(self, directory):
        self.path = Path(directory) / "midas-open.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS stations (sid TEXT PRIMARY KEY,name TEXT)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS observations (sid TEXT,day TEXT,field TEXT,value REAL,trace INTEGER,corrected INTEGER,release INTEGER,PRIMARY KEY(sid,day,field))"
            )
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def connect(self):
        return sqlite3.connect(self.path)

    def import_files(self, payload):
        staged = {}
        names = {}
        rejected = 0
        for name, raw in unpack(payload.get("files")):
            observations, labels, n = parse_file(name, raw)
            rejected += n
            names.update(labels)
            for key, value in observations.items():
                old = staged.get(key)
                if old and old[3] == value[3] and old != value:
                    raise ValueError(
                        "Conflicting duplicate observations. Choose one release of the files."
                    )
                if not old or value[3] >= old[3]:
                    staged[key] = value
        with self.lock, self.connect() as db:
            db.executemany(
                "INSERT INTO stations VALUES (?,?) ON CONFLICT(sid) DO UPDATE SET name=excluded.name",
                names.items(),
            )
            db.executemany(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?) ON CONFLICT(sid,day,field) DO UPDATE SET value=excluded.value,trace=excluded.trace,corrected=excluded.corrected,release=excluded.release WHERE excluded.release>=observations.release",
                [(*key, *value) for key, value in staged.items()],
            )
        return {**self.status(), "importedValues": len(staged), "excludedRows": rejected}

    def status(self):
        with self.lock, self.connect() as db:
            rows = db.execute(
                "SELECT s.sid,s.name,min(o.day),max(o.day),count(*),max(o.release) FROM stations s JOIN observations o USING(sid) GROUP BY s.sid ORDER BY s.name"
            ).fetchall()
        return {
            "stations": [
                {
                    "id": r[0],
                    "name": r[1],
                    "first": r[2],
                    "latest": r[3],
                    "values": r[4],
                    "release": r[5],
                }
                for r in rows
            ],
            "note": NOTE,
            "sourceUrl": CATALOGUE,
            "attribution": "Met Office MIDAS Open, CEDA. Open Government Licence v3.0. Data citations are linked in this view.",
        }

    def view(self, sid, selected):
        try:
            day = dt.date.fromisoformat(selected)
        except (ValueError, TypeError):
            raise ValueError("Choose a valid date.") from None
        if not 1850 <= day.year <= dt.date.today().year:
            raise ValueError("Choose a date from 1850 through this year.")
        with self.lock, self.connect() as db:
            station = db.execute("SELECT name FROM stations WHERE sid=?", (sid,)).fetchone()
            rows = db.execute(
                "SELECT day,field,value,trace,corrected FROM observations WHERE sid=? ORDER BY day",
                (sid,),
            ).fetchall()
        if not station:
            raise ValueError("Import and select a MIDAS station first.")
        days = {}
        flags = {}
        for date, field, value, trace, corrected in rows:
            days.setdefault(dt.date.fromisoformat(date), {})[field] = value
            flags.setdefault(date, []).append(
                ("trace" if trace else "corrected" if corrected else "observed")
            )

        def total(start, end, field):
            series = [
                days.get(start + dt.timedelta(days=i), {}).get(field)
                for i in range((end - start).days + 1)
            ]
            return sum(series) if series and all(v is not None for v in series) else None

        def daily_average(month, n, field):
            series = [
                v[field]
                for d, v in days.items()
                if 1991 <= d.year <= 2020 and (d.month, d.day) == (month, n) and field in v
            ]
            return (
                sum(series) / len(series)
                if len(series) >= (7 if (month, n) == (2, 29) else 24)
                else None
            )

        def extreme(values, maximum):
            values = [(y, v) for y, v in values if v is not None]
            if not values:
                return None
            value = (max if maximum else min)(v for _, v in values)
            return {"value": value, "years": [y for y, v in values if abs(v - value) < 1e-8]}

        same = [
            (d, v) for d, v in days.items() if (d.month, d.day) == (day.month, day.day) and d < day
        ]
        records = {
            f: extreme([(d.year, v.get(f)) for d, v in same], f != "lowC")
            for f in ("highC", "lowC", "rainMm")
        }
        hi = daily_average(day.month, day.day, "highC")
        lo = daily_average(day.month, day.day, "lowC")
        month_rows = []
        years = sorted(set(d.year for d in days))

        def monthly(year, m):
            end = dt.date(year, m, calendar.monthrange(year, m)[1])
            start = end.replace(day=1)
            high, low = total(start, end, "highC"), total(start, end, "lowC")
            return {
                "meanC": (
                    (high + low) / (2 * end.day) if high is not None and low is not None else None
                ),
                "rainMm": total(start, end, "rainMm"),
            }

        for m in range(1, 13):
            end = dt.date(day.year, m, calendar.monthrange(day.year, m)[1])
            start = end.replace(day=1)
            baselines = [monthly(y, m) for y in range(1991, 2021)]

            def average(field):
                values = [r[field] for r in baselines if r[field] is not None]
                return sum(values) / len(values) if len(values) >= 24 else None

            past = [(y, monthly(y, m)["rainMm"]) for y in years if y < day.year]
            month_rows.append(
                {
                    "month": calendar.month_name[m],
                    **monthly(day.year, m),
                    "normalMeanC": average("meanC"),
                    "normalRainMm": average("rainMm"),
                    "wettest": extreme(past, True),
                    "driest": extreme(past, False),
                    "days": sum(start <= d <= end for d in days),
                    "expected": end.day,
                }
            )
        ytd = total(dt.date(day.year, 1, 1), day, "rainMm")
        prior_ytd = []
        for y in years:
            if y >= day.year:
                continue
            try:
                end = dt.date(y, day.month, day.day)
            except ValueError:
                continue
            prior_ytd.append((y, total(dt.date(y, 1, 1), end, "rainMm")))
        observed = days.get(day, {})
        return {
            "station": station[0],
            "stationId": sid,
            "date": day.isoformat(),
            "first": min(days).isoformat(),
            "latest": max(days).isoformat(),
            "observed": observed,
            "flags": sorted(set(flags.get(day.isoformat(), []))),
            "normal": {
                "highC": hi,
                "lowC": lo,
                "meanC": (hi + lo) / 2 if hi is not None and lo is not None else None,
                "rainMm": daily_average(day.month, day.day, "rainMm"),
            },
            "records": records,
            "months": month_rows,
            "yearRainMm": ytd,
            "yearRainWettest": extreme(prior_ytd, True),
            "yearRainDriest": extreme(prior_ytd, False),
            "note": NOTE,
            "temperatureSourceUrl": TEMP_URL,
            "rainSourceUrl": RAIN_URL,
            "normalPeriod": "1991–2020 (calculated from imported observations)",
        }
