import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
from ireland_sources import (
    parse_observations,
    parse_forecast,
    parse_warnings,
    rain_ledger,
    wall_candidates,
    IrishPublicWeather,
    IrishOutlook,
)
from ireland_climate import convert_daily
from ireland_tides import select_tide
from regional_weather import country_at
from uk_climate import parse_daily
from uk_location import LocationResolver
from timezones import local_date, midnight
from zoneinfo import ZoneInfoNotFoundError

NOW = dt.datetime(2026, 10, 25, 19, 30, tzinfo=dt.timezone.utc).timestamp()


def observations(when=NOW - 1800, rain=0, temp=10, station=3):
    return {
        "collection_id": "observations-swob-nrt-60min",
        "station_id": station,
        "items": [
            {
                "parameter_name": k,
                "observed_at": dt.datetime.fromtimestamp(when, dt.timezone.utc).isoformat(),
                "value_num": v,
                "sensor_source": "suit_a",
            }
            for k, v in [
                ("air_temperature", temp),
                ("relative_humidity", 80),
                ("precipitation_amount", rain),
                ("wind_speed_mean", 10),
                ("wind_gust", 20),
                ("wind_direction", 270),
                ("air_pressure", 995),
            ]
        ],
    }


class IrishLocationTests(unittest.TestCase):
    def test_border_cities_and_west_coast(self):
        for lat, lon in [
            (53.3498, -6.2603),
            (51.8985, -8.4756),
            (53.2707, -9.0568),
            (54.65, -8.11),
            (54.001, -6.405),
        ]:
            self.assertEqual(country_at(lat, lon), "IE")
        for lat, lon in [(54.5973, -5.9301), (54.9966, -7.3086), (54.175, -6.34), (52.77, -1.56)]:
            self.assertEqual(country_at(lat, lon), "GB")

    def test_global_support_is_not_falsely_claimed(self):
        for lat, lon in [(42.46, -70.95), (48.85, 2.35), (float("nan"), -8)]:
            with self.assertRaises(ValueError):
                country_at(lat, lon)

    def test_search_includes_both_countries_and_filters_foreign_places(self):
        fetch = Mock(
            return_value={
                "results": [
                    {"name": "Dublin", "country_code": "IE", "latitude": 53.35, "longitude": -6.26},
                    {"name": "Dublin US", "country_code": "US", "latitude": 40, "longitude": -80},
                    {"name": "Belfast", "country_code": "GB", "latitude": 54.6, "longitude": -5.93},
                ]
            }
        )
        self.assertEqual(len(LocationResolver(fetch, None).search("Dublin")["matches"]), 2)

    def test_irish_climate_is_not_a_nearby_uk_station(self):
        value = LocationResolver(Mock(), None).resolve(53.35, -6.26, "Dublin")
        self.assertEqual(value["countryCode"], "IE")
        self.assertEqual(value["timezone"], "Europe/Dublin")
        self.assertEqual(value["climateStation"]["id"], "MET00000532")

    def test_switch_restart_and_ambient_priority(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = server.Desk(folder)
            desk.settings_update({"latitude": 53.35, "longitude": -6.26, "location": "Dublin"})
            desk.db.close()
            again = server.Desk(folder)
            self.assertEqual(again.status()["providers"]["observations"], "Met Éireann")
            self.assertEqual(again.settings["timezone"], "Europe/Dublin")
            again.api_key = "connected"
            self.assertEqual(again.weather_mode(), "ambient")
            again.settings_update({"latitude": 54.6, "longitude": -5.93, "location": "Belfast"})
            self.assertEqual(again.status()["providers"]["warnings"], "Met Office")
            self.assertEqual(again.settings["timezone"], "Europe/London")
            again.db.close()

    def test_old_irish_location_migrates_from_uk_climate(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "preferences.json").write_text(
                json.dumps({"latitude": 53.35, "longitude": -6.26, "location": "Dublin"})
            )
            desk = server.Desk(folder)
            self.assertEqual(desk.settings["countryCode"], "IE")
            self.assertEqual(
                desk.climate.get(dt.date(2026, 10, 25))["source"].split(" · ")[0], "Met Éireann"
            )
            self.assertEqual(desk.monthly()["source"], "Met Éireann")
            desk.db.close()

    def test_every_live_service_switches_with_location(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = server.Desk(folder)
            desk.irish_weather.snapshot = Mock(
                return_value={"source": "met-eireann", "current": None}
            )
            desk.irish_weather.history = Mock(return_value={"source": "met-eireann", "points": []})
            desk.irish_outlook.forecast = Mock(return_value={"source": "Met Éireann"})
            desk.irish_outlook.warnings = Mock(return_value={"source": "Met Éireann"})
            desk.irish_tides.snapshot = Mock(return_value={"source": "Marine Institute"})
            desk.uk_rain.snapshot = Mock(return_value={"source": "EA", "periods": {}})
            desk.public_weather.snapshot = Mock(return_value={"source": "cwop", "current": None})
            desk.settings_update({"latitude": 53.35, "longitude": -6.26, "location": "Dublin"})
            self.assertEqual(desk.weather()["source"], "met-eireann")
            self.assertEqual(desk.history()["source"], "met-eireann")
            self.assertEqual(desk.forecast()["source"], "Met Éireann")
            self.assertEqual(desk.alerts()["source"], "Met Éireann")
            self.assertEqual(desk.tides()["source"], "Marine Institute")
            desk.uk_rain.snapshot.assert_not_called()
            desk.settings_update({"latitude": 52.77, "longitude": -1.56, "location": "Swadlincote"})
            self.assertEqual(desk.weather()["source"], "cwop")
            desk.uk_rain.snapshot.assert_called_once()
            desk.db.close()

    def test_dublin_windows_timezone_fallback_handles_25_hour_day(self):
        with patch("timezones.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            self.assertEqual(
                midnight(dt.date(2026, 10, 26), "Europe/Dublin")
                - midnight(dt.date(2026, 10, 25), "Europe/Dublin"),
                90000,
            )
            self.assertEqual(local_date(NOW, "Europe/Dublin"), dt.date(2026, 10, 25))

    def test_irish_climate_and_rain_compare_the_same_utc_date(self):
        now = dt.datetime(2026, 7, 1, 23, 30, tzinfo=dt.timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as folder, patch(
            "timezones.time.time", return_value=now
        ):
            desk = server.Desk(folder)
            desk.settings_update({"latitude": 53.35, "longitude": -6.26, "location": "Dublin"})
            self.assertEqual(desk.settings["timezone"], "Europe/Dublin")
            self.assertEqual(desk.settings["climateStation"]["timezone"], "UTC")
            self.assertEqual(desk.climate.get()["date"], "2026-07-01")
            self.assertEqual(desk.rain_records.get()["date"], "2026-07-01")
            desk.db.close()


class IrishObservationTests(unittest.TestCase):
    def test_units_station_pressure_and_derived_dew_point(self):
        row = parse_observations(observations(), server.feels_like, NOW)[0]
        self.assertEqual(row["tempF"], 50)
        self.assertAlmostEqual(row["windMph"], 11.50779448)
        self.assertEqual(row["pressureKind"], "station")
        self.assertEqual(row["pressureHpa"], 995)
        self.assertEqual(row["rainLastHourMm"], 0)
        self.assertIsNone(row["rainRateMmHr"])
        self.assertLess(row["dewF"], row["tempF"])

    def test_missing_negative_nan_and_bool_rain_are_not_zero(self):
        for rain in [None, -99, -1, float("nan"), True]:
            self.assertIsNone(
                parse_observations(observations(rain=rain), server.feels_like, NOW)[0][
                    "rainLastHourMm"
                ]
            )

    def test_duplicate_sensor_values_are_not_summed(self):
        raw = observations(rain=1)
        extra = dict(raw["items"][2], sensor_source="suit_b", value_num=5)
        raw["items"].append(extra)
        self.assertEqual(parse_observations(raw, server.feels_like, NOW)[0]["rainLastHourMm"], 1)
        raw["items"].append(dict(raw["items"][2], value_num=2))
        self.assertIsNone(parse_observations(raw, server.feels_like, NOW)[0]["rainLastHourMm"])

    def test_truncated_feed_is_rejected(self):
        raw = observations()
        raw["numberMatched"] = 9000
        with self.assertRaises(ValueError):
            parse_observations(raw, server.feels_like, NOW)

    def test_nearest_temperature_only_station_is_skipped_and_cache_reopens(self):
        catalogue = {
            "features": [
                {
                    "geometry": {"coordinates": [lon, 53.35]},
                    "properties": {"station_id": sid, "station_name": name},
                }
                for sid, name, lon in [(3, "No gauge", -6.26), (4, "Dry gauge", -6.35)]
            ]
        }

        def fetch(url):
            if "/locations?" in url:
                return catalogue
            return (
                observations(rain=None, station=3) if "/0003?" in url else observations(station=4)
            )

        settings = {"latitude": 53.35, "longitude": -6.26}
        with tempfile.TemporaryDirectory() as folder, patch(
            "ireland_sources.time.time", return_value=NOW
        ):
            provider = IrishPublicWeather(folder, fetch, server.feels_like, server.pressure_trend)
            key, state = provider._state(settings)
            provider._load(key, settings, state)
            state["checked"] = time.monotonic()
            self.assertEqual(provider.snapshot(settings)["stationName"], "Dry gauge")
            reopened = IrishPublicWeather(
                folder, Mock(side_effect=OSError), server.feels_like, server.pressure_trend
            )
            _, loaded = reopened._state(settings)
            loaded["checked"] = time.monotonic()
            self.assertEqual(reopened.snapshot(settings)["current"]["rainLastHourMm"], 0)

    def test_rain_ending_at_midnight_belongs_to_previous_day(self):
        now = dt.datetime(2026, 1, 2, 2, tzinfo=dt.timezone.utc).timestamp()
        rows = [
            {"timestamp": now - h * 3600, "rainLastHourMm": r}
            for h, r in [(0, 2), (1, 1), (2, 100)]
        ]
        ledger = rain_ledger(rows, {}, now)
        self.assertEqual(ledger["periods"]["day"]["mm"], 3)
        self.assertTrue(ledger["periods"]["day"]["complete"])
        self.assertIsNone(ledger["periods"]["year"]["mm"])
        self.assertGreater(ledger["periods"]["year"]["missing"], 0)


class IrishOutlookTests(unittest.TestCase):
    def test_hourly_symbols_cover_preceding_hour(self):
        raw = [
            {"date": "2026-10-25", "time": f"{hour}:00", "weatherDescription": "Cloudy"}
            for hour in range(20, 24)
        ]
        periods = parse_forecast(raw, NOW)
        self.assertEqual(periods[0]["start"], NOW - 1800)
        self.assertEqual(periods[0]["end"], NOW + 1800)

    def test_summer_and_ambiguous_autumn_hour(self):
        july = wall_candidates("2026-07-01", "12:00")
        self.assertEqual(dt.datetime.fromtimestamp(july[0], dt.timezone.utc).hour, 11)
        repeated = wall_candidates("2026-10-25", "01:00")
        self.assertEqual(len(repeated), 2)
        with self.assertRaises(ValueError):
            parse_forecast(
                [{"date": "2026-10-25", "time": "01:00", "weatherDescription": "Fair"}],
                repeated[0] - 1800,
            )

    def test_outage_does_not_substitute_another_forecast(self):
        value = IrishOutlook(Mock(side_effect=OSError)).forecast(53.35, -6.26)
        self.assertEqual(value["source"], "Met Éireann")
        self.assertTrue(value["stale"])
        self.assertFalse(value["periods"])

    def test_warning_expiry_colour_and_visible_text(self):
        def iso(t):
            return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()

        raw = [
            {
                "id": 1,
                "issued": iso(NOW - 600),
                "expiry": iso(NOW + 3600),
                "onset": iso(NOW),
                "level": "orange",
                "headline": "Status Orange - Wind warning for Cork",
                "description": "<p>Strong winds.</p><ul><li>Travel disruption</li></ul>",
            },
            {"id": 2, "issued": iso(NOW - 600), "expiry": iso(NOW - 1)},
        ]
        value = parse_warnings(raw, NOW)
        self.assertEqual(len(value), 1)
        self.assertEqual(value[0]["level"], "orange")
        self.assertEqual(value[0]["description"], "Strong winds.\nTravel disruption")
        self.assertEqual(value[0]["headline"], raw[0]["headline"])


class IrishClimateAndTideTests(unittest.TestCase):
    def test_negative_temperatures_trace_and_estimate_flags(self):
        text = "Station Name: Test\n date - 00 to 00 UTC\ndate,ind,maxtp,ind,mintp,ind,rain\n01-jan-2020,1,-1.0,1,-4.0,2,0.0\n02-jan-2020,2,2.0,3,-2.0,4,10.0\n03-jan-2020,0,3.0,0,1.0,2,10.0\n"
        days = parse_daily(convert_daily(text, "MET00000532"), "MET00000532")
        self.assertAlmostEqual(days[dt.date(2020, 1, 1)]["TMIN"], 24.8)
        self.assertEqual(days[dt.date(2020, 1, 1)]["PRCP"], "T")
        self.assertNotIn(dt.date(2020, 1, 2), days)
        self.assertNotIn("PRCP", days[dt.date(2020, 1, 3)])

    def test_nonmatching_observing_day_is_rejected(self):
        with self.assertRaises(ValueError):
            convert_daily("date - 09 to 09 UTC\ndate,ind,maxtp,ind,mintp,ind,rain\n", "MET00000532")

    def test_irish_tide_datum_quality_and_closest_fresh_coast(self):
        raw = {
            "table": {
                "columnNames": [
                    "station_id",
                    "longitude",
                    "latitude",
                    "time",
                    "Water_Level_OD_Malin",
                    "QC_Flag",
                ],
                "rows": [
                    ["Bad", -6.26, 53.35, "2026-10-25T19:00:00Z", 3, 9],
                    ["Dublin Port", -6.22, 53.34, "2026-10-25T19:00:00Z", 1.3, 0],
                    ["Farther", -8, 53, "2026-10-25T19:00:00Z", 1.5, 1],
                ],
            }
        }
        value = select_tide(raw, 53.35, -6.26, NOW)
        self.assertEqual(value["stationName"], "Dublin Port")
        self.assertEqual(value["datumLabel"], "OD Malin")
        self.assertTrue(value["latest"]["provisional"])


if __name__ == "__main__":
    unittest.main()
