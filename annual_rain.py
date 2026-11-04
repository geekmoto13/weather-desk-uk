"""Calendar YTD = completed monthly station counters + live month to date.

Only month-end windows are requested. Completed months persist separately for
station, timezone and year. The old daily archive is left untouched and unused.
No credentials enter this module or its database.
"""

from __future__ import annotations
import datetime as dt
from decimal import Decimal, ROUND_HALF_UP
import math
import os
from pathlib import Path
import sqlite3
import threading
import time

UTC = dt.timezone.utc


def local_day(stamp, zone):
    from timezones import local_date

    return local_date(stamp, zone)


def month_start(year, month, zone):
    from timezones import midnight

    return midnight(dt.date(year, month, 1), zone)


def inches(mm):
    return (
        mm / 25.4
        if isinstance(mm, (float, int))
        and not isinstance(mm, bool)
        and math.isfinite(mm)
        and mm >= 0
        else None
    )


def monthly_inches(value):
    """Use the same 0.01-inch monthly amounts shown on Ambient's dashboard."""
    return (
        float(Decimal(str(value)).quantize(Decimal(".01"), rounding=ROUND_HALF_UP))
        if value is not None
        else None
    )


def completed_month(points, year, month, zone):
    """Last monthly counter near local month end; missing data is never zero.

    An optional short boundary pair recovers rain after the final old-month
    sample: lifetime change minus the new month's rain. It never checks the
    daily/lifetime agreement across the rest of the year.
    """
    y, m = (year + 1, 1) if month == 12 else (year, month + 1)
    end = month_start(y, m, zone)
    usable = []
    for p in points:
        stamp, amount = p.get("timestamp"), inches(p.get("rainMonthMm"))
        if (
            isinstance(stamp, (int, float))
            and not isinstance(stamp, bool)
            and math.isfinite(stamp)
            and amount is not None
        ):
            usable.append((stamp, amount, inches(p.get("rainTotalMm"))))
    before = [p for p in usable if end - 3600 <= p[0] < end]
    if not before:
        return None
    stamp, amount, total = max(before, key=lambda p: p[0])
    method = "month-end counter"
    for after, new_month, new_total in sorted(
        (p for p in usable if end <= p[0] <= end + 1800), key=lambda p: p[0]
    ):
        if total is None or new_total is None:
            continue
        change = new_total - total
        # Skip a lifetime reset or monthly counter that has not rolled over.
        # Independent API rounding must not invalidate an observed month.
        if change < -0.01 or new_month > change + 0.01:
            continue
        extra = change - new_month
        if extra > 0.004:
            amount += extra
            method = "month-end counter + midnight rain"
        break
    return {"month": month, "inches": monthly_inches(amount), "timestamp": stamp, "method": method}


class AnnualRain:
    def __init__(self, directory):
        self.path = Path(directory) / "monthly-rain.sqlite3"
        self.lock = threading.RLock()
        self.jobs, self.retries, self.errors = {}, {}, {}
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS months (station TEXT, zone TEXT, year INTEGER, month INTEGER, amount REAL, stamp REAL, method TEXT, PRIMARY KEY(station,zone,year,month))"
            )
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def _save(self, key, result):
        with self.lock, self._db() as db:
            db.execute(
                "INSERT INTO months VALUES (?,?,?,?,?,?,?) ON CONFLICT(station,zone,year,month) DO UPDATE SET amount=excluded.amount,stamp=excluded.stamp,method=excluded.method WHERE excluded.stamp>=months.stamp",
                (*key, result["month"], result["inches"], result["timestamp"], result["method"]),
            )

    def cancel(self):
        with self.lock:
            for event, _ in self.jobs.values():
                event.set()

    def ensure(self, station, zone, current, fetch_page, active=lambda: True):
        try:
            day = local_day(current.get("timestamp"), zone)
        except (ValueError, TypeError, OSError, OverflowError):
            return
        key = (station, zone, day.year)
        with self.lock, self._db() as db:
            found = {
                row[0]
                for row in db.execute(
                    "SELECT month FROM months WHERE station=? AND zone=? AND year=?", key
                )
            }
            missing = [m for m in range(1, day.month) if m not in found]
            job = self.jobs.get(key)
            if not missing or (job and job[1].is_alive()) or self.retries.get(key, 0) > time.time():
                return
            event = threading.Event()
            thread = threading.Thread(
                target=self._import, args=(key, missing, fetch_page, active, event), daemon=True
            )
            self.jobs[key] = (event, thread)
            self.errors.pop(key, None)
            thread.start()

    def _import(self, key, months, fetch_page, active, event):
        station, zone, year = key
        unavailable = []
        for month in months:
            if event.is_set() or not active():
                return
            end = month_start(year, month + 1, zone)
            try:
                # One compact query per missing month. The half hour after
                # midnight supplies optional rollover evidence.
                points = fetch_page(int((end + 1800) * 1000))
                if not isinstance(points, list):
                    raise ValueError("Invalid response")
                if event.is_set() or not active():
                    return
                result = completed_month(
                    [p for p in points if isinstance(p, dict)], year, month, zone
                )
                if result:
                    self._save(key, result)
                else:
                    unavailable.append(month)
            except Exception:
                # Never expose an upstream exception with a key-bearing URL.
                with self.lock:
                    self.errors[key] = (
                        "Monthly station history paused; it will retry automatically."
                    )
                    self.retries[key] = time.time() + 60
                return
        with self.lock:
            if unavailable:
                names = ", ".join(dt.date(year, m, 1).strftime("%b") for m in unavailable)
                self.errors[key] = (
                    f"No month-end monthly counter for {names}. Available months stay saved; missing months will retry."
                )
                self.retries[key] = time.time() + 300
            else:
                self.errors.pop(key, None)
                self.retries.pop(key, None)

    def result(self, station, zone, current):
        try:
            day = local_day(current.get("timestamp"), zone)
        except (ValueError, TypeError, OSError, OverflowError):
            return {
                "complete": False,
                "status": "waiting",
                "error": "Waiting for a station observation.",
            }
        key = (station, zone, day.year)
        with self.lock, self._db() as db:
            rows = db.execute(
                "SELECT month,amount,stamp,method FROM months WHERE station=? AND zone=? AND year=? AND month<? ORDER BY month",
                (*key, day.month),
            ).fetchall()
            stored = {
                m: {"month": m, "inches": amount, "timestamp": stamp, "method": method}
                for m, amount, stamp, method in rows
            }
            months = [
                stored.get(
                    m, {"month": m, "inches": None, "timestamp": None, "method": "unavailable"}
                )
                for m in range(1, day.month)
            ]
            months.append(
                {
                    "month": day.month,
                    "inches": monthly_inches(inches(current.get("rainMonthMm"))),
                    "timestamp": current["timestamp"],
                    "method": "live month to date",
                }
            )
            complete = all(m["inches"] is not None for m in months)
            job = self.jobs.get(key)
            building = bool(job and job[1].is_alive())
            error = self.errors.get(key)
            if months[-1]["inches"] is None:
                error = "Ambient did not supply the current month's rainfall counter."
            return {
                "station": station,
                "timezone": zone,
                "year": day.year,
                "method": "monthly-sum",
                "start": f"{day.year}-01-01",
                "through": day.isoformat(),
                "complete": complete,
                "mm": math.fsum(m["inches"] for m in months) * 25.4 if complete else None,
                "timestamp": current["timestamp"] if complete else None,
                "months": months,
                "loadedMonths": sum(m["inches"] is not None for m in months),
                "expectedMonths": day.month,
                "status": (
                    "complete"
                    if complete
                    else "building" if building else "paused" if error else "building"
                ),
                "error": None if complete else error,
            }
