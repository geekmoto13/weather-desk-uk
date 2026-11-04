"""UK airport fallback requires measured rain; EA ledger keeps its own source."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rain_observations import has_measured_rain
from uk_sources import UKPublicWeather, metar_reading
from server import Desk, feels_like, pressure_trend

SETTINGS = {"latitude": 52.77, "longitude": -1.56}


def report(name="EGNX", lat=52.78, **values):
    return {"icaoId": name, "name": name, "lat": lat, "lon": -1.56,
            "obsTime": time.time() - 300, "temp": 12, **values}


class PublicRainTests(unittest.TestCase):
    def test_awc_accumulations_are_inches_and_not_calendar_or_rate_counters(self):
        current = metar_reading(report(precip=0, pcp3hr=.1, pcp6hr=.2, pcp24hr=1), feels_like)
        self.assertEqual(current["rainLastHourMm"], 0)
        self.assertAlmostEqual(current["rainLast3hMm"], 2.54)
        self.assertAlmostEqual(current["rainLast6hMm"], 5.08)
        self.assertEqual(current["rainLast24hMm"], 25.4)
        self.assertTrue(has_measured_rain(current))
        for field in ("rainDayMm", "rainMonthMm", "rainYearMm", "rainRateMmHr"):
            self.assertIsNone(current[field])

    def test_rain_weather_code_and_invalid_amounts_do_not_qualify(self):
        for amount in (None, True, False, -1, "T", float("nan"), float("inf"), 9999):
            with self.subTest(amount=amount):
                self.assertFalse(has_measured_rain(metar_reading(report(precip=amount, wxString="+RA"), feels_like)))

    def test_nearer_airport_without_rain_is_skipped_and_zero_is_accepted(self):
        fetch = Mock(return_value=[report(), report("EGBB", lat=52.8, precip=0)])
        with tempfile.TemporaryDirectory() as folder:
            provider = UKPublicWeather(folder, fetch, None, feels_like, pressure_trend)
            value = provider.snapshot(SETTINGS)
            self.assertEqual(value["stationId"], "EGBB")
            self.assertEqual(value["current"]["rainLastHourMm"], 0)
            self.assertTrue(value["rainGaugeRequired"])
            self.assertIn("hours=2", fetch.call_args.args[0])

    def test_search_continues_outwards_when_inner_area_has_no_gauged_station(self):
        fetch = Mock(side_effect=[[report()], [report("EGGA", lat=52.9, pcp24hr=.2)]])
        with tempfile.TemporaryDirectory() as folder:
            provider = UKPublicWeather(folder, fetch, None, feels_like, pressure_trend)
            self.assertEqual(provider.snapshot(SETTINGS)["stationId"], "EGGA")
            self.assertEqual(fetch.call_count, 2)

    def test_no_qualifying_station_does_not_restore_old_temperature_only_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            provider = UKPublicWeather(folder, Mock(return_value=[report()]), None, feels_like, pressure_trend)
            path = provider.directory / "52.7700,-1.5600.json"
            path.write_text(json.dumps({"stationId": "EGOLD", "current": {"timestamp": time.time(), "tempF": 50}, "points": []}))
            result = provider.snapshot(SETTINGS)
            self.assertFalse(result.get("current"))
            self.assertIsNone(result.get("stationId"))
            self.assertIn("measured rainfall", result["error"])

    def test_station_rain_is_retained_separately_when_ea_ledger_is_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = Desk(folder)
            try:
                current = metar_reading(report(precip=.1), feels_like)
                public = {"source": "metar", "stationId": "EGGA", "current": current}
                desk.public_weather.snapshot = Mock(return_value=public)
                desk.uk_rain.snapshot = Mock(return_value={"periods": {"day": {"mm": 8, "complete": True}}, "rateMmHr": 0})
                value = desk.weather()
                self.assertEqual(value["current"]["rainDayMm"], 8)
                self.assertAlmostEqual(value["stationRain"]["rainLastHourMm"], 2.54)
                self.assertIsNone(value["stationRain"]["rainDayMm"])
            finally:
                desk.db.close()


if __name__ == "__main__":
    unittest.main()
