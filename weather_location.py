"""Coordinate validation, great-circle distance and Maidenhead grid centres."""

import math
import re


def distance_km(lat, lon, other_lat, other_lon):
    a, b = math.radians(lat), math.radians(other_lat)
    dlat, dlon = b - a, math.radians(other_lon - lon)
    h = math.sin(dlat / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(min(1, math.sqrt(h)))


def coordinates(lat, lon):
    if isinstance(lat, bool) or isinstance(lon, bool):
        raise ValueError("Choose a valid location.")
    try:
        lat, lon = float(lat), float(lon)
    except (ValueError, TypeError):
        raise ValueError("Choose a valid location.") from None
    if (
        not math.isfinite(lat)
        or not math.isfinite(lon)
        or not -90 <= lat <= 90
        or not -180 <= lon <= 180
    ):
        raise ValueError("Choose valid latitude and longitude.")
    return lat, lon


def grid_center(value):
    value = value.strip().upper()
    if not re.fullmatch(r"[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?", value):
        raise ValueError("Use a 4-, 6- or 8-character Maidenhead grid square.")
    lon, lat = (ord(value[0]) - 65) * 20 - 180, (ord(value[1]) - 65) * 10 - 90
    lon += int(value[2]) * 2
    lat += int(value[3])
    width, height = 2, 1
    if len(value) >= 6:
        width, height = 2 / 24, 1 / 24
        lon += (ord(value[4]) - 65) * width
        lat += (ord(value[5]) - 65) * height
    if len(value) == 8:
        width, height = width / 10, height / 10
        lon += int(value[6]) * width
        lat += int(value[7]) * height
    return lat + height / 2, lon + width / 2
