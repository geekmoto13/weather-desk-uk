"""Public stations qualify through an actual reported precipitation amount.

The feed is evidence of measured rainfall, not a hardware inventory. An explicit
zero is a valid observation; a missing, negative, non-finite or boolean value is
not. Calendar counters are never inferred from rolling accumulation periods.
"""

import math

RAIN_FIELDS = (
    "rainLastHourMm",
    "rainLast3hMm",
    "rainLast6hMm",
    "rainLast24hMm",
    "rainDayMm",
)


def has_measured_rain(current):
    if not isinstance(current, dict):
        return False
    return any(
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 <= value <= 3000
        for value in (current.get(field) for field in RAIN_FIELDS)
    )
