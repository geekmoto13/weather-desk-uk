"""Unit-safe values for forecast cards, independent of station observations."""
import math
import re


def numeric(value, low, high):
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    return result if math.isfinite(result) and low <= result <= high else None


def temperature_f(value, unit="F"):
    if isinstance(value, dict):
        unit, value = value.get("unitCode", ""), value.get("value")
    value = numeric(value, -130, 160)
    if value is None:
        return None
    if unit in ("C", "wmoUnit:degC"):
        value = value * 9 / 5 + 32
    elif unit not in ("F", "wmoUnit:degF"):
        return None
    return numeric(value, -150, 180)


def wind_mph(value):
    if isinstance(value, dict):
        unit = value.get("unitCode")
        factor = {"wmoUnit:km_h-1": 1 / 1.609344, "wmoUnit:m_s-1": 2.2369362921,
                  "wmoUnit:kn": 1.150779448, "wmoUnit:mi_h-1": 1}.get(unit)
        speed = numeric(value.get("value"), 0, 500)
        return numeric(speed * factor, 0, 300) if factor is not None and speed is not None else None
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)(?:\s+to\s+(\d+(?:\.\d+)?))?\s+(mph|km/h|kt|knots|m/s)\s*", value, re.I)
    if not match:
        return None
    low, high = float(match[1]), float(match[2] or match[1])
    if high < low:
        return None
    factor = {"mph": 1, "km/h": 1 / 1.609344, "kt": 1.150779448,
              "knots": 1.150779448, "m/s": 2.2369362921}[match[3].lower()]
    return numeric(high * factor, 0, 300)
