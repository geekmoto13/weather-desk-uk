"""Location-based provider selection. Unsupported countries are never guessed."""

from functools import lru_cache
import json
from pathlib import Path
from uk_sources import contains
from weather_location import coordinates

_IRELAND = json.loads((Path(__file__).parent / "data/ireland-boundary.geojson").read_text())


@lru_cache(maxsize=4096)
def country_at(latitude, longitude):
    lat, lon = coordinates(latitude, longitude)
    if contains(_IRELAND["features"][0]["geometry"], lat, lon):
        return "IE"
    if 49 <= lat <= 61.2 and -8.8 <= lon <= 2.1:
        return "GB"
    raise ValueError(
        "This edition supports UK and Republic of Ireland locations. Choose a town or a more precise grid square."
    )


def is_ireland(settings):
    return country_at(settings["latitude"], settings["longitude"]) == "IE"


def provider_profile(settings):
    irish = is_ireland(settings)
    return {
        "countryCode": "IE" if irish else "GB",
        "countryName": "Republic of Ireland" if irish else "United Kingdom",
        "observations": "Met Éireann" if irish else "CWOP / NOAA Aviation Weather Center",
        "forecast": "Met Éireann" if irish else "Open-Meteo / DWD ICON",
        "warnings": "Met Éireann" if irish else "Met Office",
        "climate": "Met Éireann daily archive" if irish else "NOAA NCEI / Met Office",
        "officialUrl": "https://www.met.ie/" if irish else "https://www.metoffice.gov.uk/",
    }
