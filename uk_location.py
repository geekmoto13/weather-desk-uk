"""UK postcodes, towns and Maidenhead grids; audited daily climate references."""

import re
from urllib.parse import quote, urlencode
from weather_location import coordinates, distance_km, grid_center
from uk_climate import STATIONS, profile
from regional_weather import country_at


def uk_coordinates(lat, lon):
    lat, lon = coordinates(lat, lon)
    country_at(lat, lon)
    return lat, lon


class LocationResolver:
    def __init__(self, fetch, archive):
        self.fetch = fetch
        self.archive = archive

    def search(self, query):
        if (
            not isinstance(query, str)
            or not 2 <= len(query.strip()) <= 100
            or any(ord(c) < 32 for c in query)
        ):
            raise ValueError("Enter a UK postcode, UK or Irish town, or Maidenhead grid.")
        query = query.strip()
        if re.fullmatch(r"[A-Za-z]{2}\d{2}(?:[A-Za-z]{2}(?:\d{2})?)?", query):
            lat, lon = uk_coordinates(*grid_center(query))
            return {
                "matches": [
                    {
                        "name": query.upper() + " · grid-square centre",
                        "latitude": lat,
                        "longitude": lon,
                    }
                ]
            }
        compact = re.sub(r"\s", "", query).upper()
        if re.fullmatch(r"[A-Z]{1,2}\d[A-Z\d]?\d[A-Z]{2}", compact):
            raw = self.fetch("https://api.postcodes.io/postcodes/" + quote(compact))
            r = raw.get("result") or {}
            lat, lon = uk_coordinates(r.get("latitude"), r.get("longitude"))
            return {
                "matches": [
                    {
                        "name": ", ".join(
                            str(v)
                            for v in (r.get("postcode"), r.get("admin_district"), r.get("country"))
                            if v
                        ),
                        "latitude": lat,
                        "longitude": lon,
                    }
                ]
            }
        raw = self.fetch(
            "https://geocoding-api.open-meteo.com/v1/search?"
            + urlencode({"name": query, "count": 30, "language": "en", "format": "json"})
        )
        matches = []
        for row in raw.get("results", []):
            if row.get("country_code") not in ("GB", "IE"):
                continue
            try:
                lat, lon = uk_coordinates(row.get("latitude"), row.get("longitude"))
            except ValueError:
                continue
            name = ", ".join(
                dict.fromkeys(
                    str(row[k]) for k in ("name", "admin2", "admin1", "country") if row.get(k)
                )
            )
            matches.append({"name": name[:100], "latitude": lat, "longitude": lon})
        return {
            "matches": matches,
            "message": (
                None
                if matches
                else "No UK or Republic of Ireland match found. Try a town name, UK postcode or precise grid square."
            ),
        }

    def resolve(self, lat, lon, name=""):
        lat, lon = uk_coordinates(lat, lon)
        country = country_at(lat, lon)
        candidates = sorted(
            (distance_km(lat, lon, s["lat"], s["lon"]), s["id"])
            for s in STATIONS.values()
            if s.get("country", "GB") == country
        )
        d, sid = candidates[0]
        climate = profile(sid, round(d, 1))
        climate["available"] = d <= 300
        return {
            "latitude": lat,
            "longitude": lon,
            "location": str(name or "Your location")[:100],
            "timezone": "Europe/Dublin" if country == "IE" else "Europe/London",
            "countryCode": country,
            "climateStation": climate,
            "locationConfigured": True,
        }
