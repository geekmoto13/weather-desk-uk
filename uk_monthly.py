"""The Met Office's public monthly station archive, with calculated 1991–2020 means."""

import calendar
import json
from pathlib import Path
import re
from uk_climate import get_text
from uk_sources import Cache
from weather_location import distance_km
from timezones import local_date

CATALOGUE = json.loads(
    (Path(__file__).parent / "data/metoffice-monthly-stations.json").read_text()
)["stations"]


def parse_monthly(text):
    rows = []
    for line in text.splitlines():
        p = line.split()
        if len(p) < 7 or not re.fullmatch(r"\d{4}", p[0]) or not p[1].isdigit():
            continue
        year, month = int(p[0]), int(p[1])
        if not 1 <= month <= 12:
            continue

        def value(raw):
            try:
                return float(raw.rstrip("*#"))
            except ValueError:
                return None

        hi, lo, rain = value(p[2]), value(p[3]), value(p[5])
        rows.append(
            {
                "year": year,
                "month": month,
                "meanC": (hi + lo) / 2 if hi is not None and lo is not None else None,
                "rainMm": rain,
                "estimated": any("*" in v for v in p[2:7]),
                "provisional": "Provisional" in line,
            }
        )
    if not rows:
        raise ValueError("The Met Office monthly file was unreadable.")
    return rows


def summarize(rows, year):
    result = []
    for month in range(1, 13):
        baseline = [r for r in rows if 1991 <= r["year"] <= 2020 and r["month"] == month]

        def average(key):
            values = [r[key] for r in baseline if r[key] is not None]
            return sum(values) / len(values) if len(values) >= 24 else None

        actual = next((r for r in rows if r["year"] == year and r["month"] == month), None)
        points = [
            r for r in rows if r["month"] == month and r["year"] < year and r["rainMm"] is not None
        ]

        def extreme(operation):
            if not points:
                return None
            amount = operation(r["rainMm"] for r in points)
            return {"mm": amount, "years": [r["year"] for r in points if r["rainMm"] == amount]}

        result.append(
            {
                "month": month,
                "name": calendar.month_name[month],
                "meanC": average("meanC"),
                "rainMm": average("rainMm"),
                "observed": actual,
                "wettest": extreme(max),
                "driest": extreme(min),
            }
        )
    return result


class MetOfficeMonthly:
    def __init__(self, fetch_text=get_text):
        self.fetch = fetch_text
        self.cache = Cache(None)

    def get(self, lat, lon):
        d, station = min(
            (distance_km(lat, lon, s["lat"], s["lon"]), s)
            for s in CATALOGUE
            if s["status"] == "open"
        )
        rows = self.cache.get(
            station["id"], 86400, lambda: parse_monthly(self.fetch(station["txt_url"]))
        )
        today = local_date(zone="Europe/London")
        latest = max(rows, key=lambda r: (r["year"], r["month"]))
        return {
            "station": station["name"],
            "distanceKm": round(d, 1),
            "sourceUrl": station["txt_url"],
            "source": "Met Office historic monthly station data",
            "year": today.year,
            "month": today.month,
            "latest": f'{latest["year"]}-{latest["month"]:02d}',
            "months": summarize(rows, today.year),
            "attribution": "Met Office. Contains public sector information licensed under the Open Government Licence v3.0.",
            "note": "Full calendar months; historic extrema use years before the displayed year. Averages are calculated from the 1991–2020 monthly observations; daily references elsewhere use a separate named station.",
        }
