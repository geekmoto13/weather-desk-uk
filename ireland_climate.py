"""Met Éireann daily CSV adapter. All supported daily elements are 00–24 UTC.

The compact local .dly representation is a display/cache format, not a claim
that these Met Éireann files were supplied by NOAA. Original source URLs and
the conversion are attributed in the UI and bundled data notes.
"""

import calendar
import csv
import datetime as dt
import io
import math
from timezones import local_date
from weather_location import distance_km

SOURCE = "Met Éireann · daily observations (00:00–24:00 UTC)"


def convert_daily(text, sid):
    start = text.find("date,ind,")
    if start < 0 or "00 to 00 UTC" not in text[:start]:
        raise ValueError("Met Éireann daily CSV has an unsupported observing day or header.")
    reader = csv.reader(io.StringIO(text[start:]))
    header = next(reader)
    indexes = {key: header.index(key) for key in ("maxtp", "mintp", "rain")}
    months = {}
    for row in reader:
        if not row or len(row) != len(header):
            continue
        try:
            a, b, c = row[0].lower().split("-")
            month = [
                "jan",
                "feb",
                "mar",
                "apr",
                "may",
                "jun",
                "jul",
                "aug",
                "sep",
                "oct",
                "nov",
                "dec",
            ].index(b) + 1
            day = dt.date(int(c), month, int(a))
        except (ValueError, IndexError):
            continue
        for field, code in [("maxtp", "TMAX"), ("mintp", "TMIN"), ("rain", "PRCP")]:
            index = indexes[field]
            try:
                value = float(row[index])
            except ValueError:
                continue
            flag = row[index - 1].strip()
            # Temperature indicator 1 means a valid negative temperature,
            # whereas 2/3 are estimates. Rain indicator 2 may mean a trace OR a
            # sum: accept only an explicit zero; never use a positive sum.
            accepted = (
                flag in ("0", "1") if field != "rain" else flag == "0" or flag == "2" and value == 0
            )
            low, high = (0, 3000) if field == "rain" else (-80, 60)
            if not accepted or not math.isfinite(value) or not low <= value <= high:
                continue
            cells = months.setdefault((day.year, day.month, code), {})
            encoded = f"{round(value * 10):5d}{'T' if field == 'rain' and flag == '2' else ' '}  "
            if day.day in cells and cells[day.day] != encoded:
                cells[day.day] = "-9999   "
            else:
                cells[day.day] = encoded
    if not months:
        raise ValueError("No usable Met Éireann daily observations were returned.")
    return (
        "\n".join(
            sid
            + f"{year:04d}{month:02d}"
            + code
            + "".join(cells.get(n, "-9999   ") for n in range(1, 32))
            for (year, month, code), cells in sorted(months.items())
        )
        + "\n"
    )


class IrishMonthly:
    def __init__(self, archive, stations):
        self.archive, self.stations = archive, stations

    def get(self, lat, lon):
        distance, station = min(
            (distance_km(lat, lon, s["lat"], s["lon"]), sid)
            for sid, s in self.stations.items()
            if s.get("country") == "IE"
        )
        site = self.stations[station]
        item = self.archive.load(station)
        days = item["days"]
        today = local_date(zone="UTC")
        months = []
        for month in range(1, 13):
            rows = {}
            for year in range(int(item["first"][:4]), int(item["through"][:4]) + 1):
                entries = [
                    days.get(dt.date(year, month, n), {})
                    for n in range(1, calendar.monthrange(year, month)[1] + 1)
                ]
                rain = [0 if e.get("PRCP") == "T" else e.get("PRCP") for e in entries]
                temps = [
                    (e["TMAX"] + e["TMIN"]) / 2
                    for e in entries
                    if isinstance(e.get("TMAX"), (float, int))
                    and isinstance(e.get("TMIN"), (float, int))
                ]
                rows[year] = {
                    "rainMm": (
                        sum(rain) * 25.4 if all(isinstance(v, (float, int)) for v in rain) else None
                    ),
                    "meanC": (
                        (sum(temps) / len(temps) - 32) / 1.8 if len(temps) == len(entries) else None
                    ),
                }
            normal = {
                key: [
                    rows[y][key]
                    for y in range(1991, 2021)
                    if y in rows and rows[y][key] is not None
                ]
                for key in ("meanC", "rainMm")
            }
            available = [(r["rainMm"], y) for y, r in rows.items() if r["rainMm"] is not None]

            def record(maximum):
                if not available:
                    return None
                value = (max if maximum else min)(r[0] for r in available)
                return {"mm": value, "years": [y for v, y in available if abs(v - value) < 0.00001]}

            months.append(
                {
                    "month": month,
                    "name": calendar.month_name[month],
                    **{
                        key: sum(series) / len(series) if len(series) >= 24 else None
                        for key, series in normal.items()
                    },
                    "observed": rows.get(today.year),
                    "wettest": record(True),
                    "driest": record(False),
                }
            )
        return {
            "station": site["name"],
            "distanceKm": round(distance, 1),
            "month": today.month,
            "year": today.year,
            "months": months,
            "latest": item["through"],
            "sourceUrl": site["daily_url"],
            "source": "Met Éireann",
            "note": "Calculated from Met Éireann 00:00–24:00 UTC daily data. Full months only; each normal needs at least 24 of 30 baseline years. These are calculated 1991–2020 averages, not published official normals.",
        }
