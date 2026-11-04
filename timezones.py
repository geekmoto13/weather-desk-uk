"""Local calendar dates, including common US zones on stock Windows Python."""

from __future__ import annotations
import datetime as dt
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = dt.timezone.utc
ALIASES = {
    "US/Eastern": "America/New_York",
    "EST5EDT": "America/New_York",
    "US/Central": "America/Chicago",
    "CST6CDT": "America/Chicago",
    "US/Mountain": "America/Denver",
    "MST7MDT": "America/Denver",
    "US/Pacific": "America/Los_Angeles",
    "PST8PDT": "America/Los_Angeles",
    "GB": "Europe/London",
    "GB-Eire": "Europe/London",
    "Europe/Belfast": "Europe/London",
}
US_ZONES = {
    "America/New_York": (-5, True),
    "America/Detroit": (-5, True),
    "America/Indiana/Indianapolis": (-5, True),
    "America/Indiana/Vincennes": (-5, True),
    "America/Indiana/Marengo": (-5, True),
    "America/Indiana/Petersburg": (-5, True),
    "America/Indiana/Vevay": (-5, True),
    "America/Indiana/Winamac": (-5, True),
    "America/Kentucky/Louisville": (-5, True),
    "America/Kentucky/Monticello": (-5, True),
    "America/Chicago": (-6, True),
    "America/Menominee": (-6, True),
    "America/Indiana/Knox": (-6, True),
    "America/Indiana/Tell_City": (-6, True),
    "America/North_Dakota/Center": (-6, True),
    "America/North_Dakota/New_Salem": (-6, True),
    "America/North_Dakota/Beulah": (-6, True),
    "America/Denver": (-7, True),
    "America/Boise": (-7, True),
    "America/Phoenix": (-7, False),
    "America/Los_Angeles": (-8, True),
    "America/Anchorage": (-9, True),
    "America/Juneau": (-9, True),
    "America/Sitka": (-9, True),
    "America/Metlakatla": (-9, True),
    "America/Yakutat": (-9, True),
    "America/Nome": (-9, True),
    "America/Adak": (-10, True),
    "Pacific/Honolulu": (-10, False),
    "America/Puerto_Rico": (-4, False),
    "America/St_Thomas": (-4, False),
    "Pacific/Guam": (10, False),
    "Pacific/Saipan": (10, False),
    "Pacific/Pago_Pago": (-11, False),
    "UTC": (0, False),
    "Etc/UTC": (0, False),
}


def canonical_zone(zone):
    return ALIASES.get(zone, zone)


def _rules(zone, year):
    value = US_ZONES.get(canonical_zone(zone))
    if value is None or value[1] and year < 2007:
        raise ValueError("Timezone history for this date needs the IANA timezone database.")
    return value


def _changes(year):
    march, november = dt.date(year, 3, 1), dt.date(year, 11, 1)
    return (
        march + dt.timedelta(days=(6 - march.weekday()) % 7 + 7),
        november + dt.timedelta(days=(6 - november.weekday()) % 7),
    )


def _british_changes(year):
    if year < 1996:
        raise ValueError("UK timezone history before 1996 needs the IANA timezone database.")
    march, october = dt.date(year, 3, 31), dt.date(year, 10, 31)
    return march - dt.timedelta(days=(march.weekday() + 1) % 7), october - dt.timedelta(
        days=(october.weekday() + 1) % 7
    )


def local_date(stamp=None, zone="America/New_York"):
    now = dt.datetime.fromtimestamp(time.time() if stamp is None else stamp, UTC)
    zone = canonical_zone(zone)
    try:
        return now.astimezone(ZoneInfo(zone)).date()
    except ZoneInfoNotFoundError:
        if zone in ("Europe/London", "Europe/Dublin"):
            spring, autumn = _british_changes(now.year)
            start = dt.datetime.combine(spring, dt.time(1), UTC)
            end = dt.datetime.combine(autumn, dt.time(1), UTC)
            return (now + dt.timedelta(hours=int(start <= now < end))).date()
        offset, dst = _rules(zone, now.year)
        if dst:
            spring, autumn = _changes(now.year)
            start = dt.datetime.combine(spring, dt.time(2), UTC) - dt.timedelta(hours=offset)
            end = dt.datetime.combine(autumn, dt.time(2), UTC) - dt.timedelta(hours=offset + 1)
            offset += int(start <= now < end)
        return (now + dt.timedelta(hours=offset)).date()


def midnight(day, zone):
    zone = canonical_zone(zone)
    try:
        return dt.datetime.combine(day, dt.time(), ZoneInfo(zone)).timestamp()
    except ZoneInfoNotFoundError:
        if zone in ("Europe/London", "Europe/Dublin"):
            spring, autumn = _british_changes(day.year)
            return (
                dt.datetime.combine(day, dt.time(), UTC)
                - dt.timedelta(hours=int(spring < day <= autumn))
            ).timestamp()
        offset, dst = _rules(zone, day.year)
        if dst:
            spring, autumn = _changes(day.year)
            offset += int(spring < day <= autumn)
        return (dt.datetime.combine(day, dt.time(), UTC) - dt.timedelta(hours=offset)).timestamp()


def validate_zone(zone):
    local_date(zone=zone)
    return canonical_zone(zone)
