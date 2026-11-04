"""Rain-reporting stations only, including saved choices and provider fallbacks."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cwop_weather import PreferredPublicWeather, parse_reports
from rain_observations import RAIN_FIELDS, has_measured_rain
from server import feels_like, pressure_trend
from test_cwop import NOW, xml

SETTINGS = {"latitude": 52.77, "longitude": -1.56, "weatherSource": "auto"}


class RainSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = patch("cwop_weather.time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def provider(self, rows):
        provider = PreferredPublicWeather(
            self.temp.name, Mock(), feels_like, pressure_trend,
            Mock(return_value=xml(rows)),
        )
        key, state = provider._state(SETTINGS)
        state["checked"] = state["officialChecked"] = time.monotonic()
        provider._load(key, SETTINGS, state)
        return provider, state

    def test_explicit_dry_zero_qualifies_in_each_supported_period(self):
        for field in RAIN_FIELDS:
            with self.subTest(field=field):
                self.assertTrue(has_measured_rain({field: 0}))

    def test_missing_invalid_and_non_precipitation_values_cannot_qualify(self):
        for value in (None, True, False, -1, 3001, float("nan"), float("inf"), "0", "T"):
            with self.subTest(value=value):
                self.assertFalse(has_measured_rain({"rainLastHourMm": value}))
        self.assertFalse(has_measured_rain({"tempF": 50, "wxString": "RA", "humidity": 100}))

    def test_skips_closer_temperature_only_station_for_dry_rain_gauge(self):
        provider, state = self.provider([
            ("near", 52.77, -1.56, 0, {"T": 280}),
            ("gauge", 52.79, -1.56, 0, {"T": 281, "PCP1H": 0}),
        ])
        value = provider.snapshot(SETTINGS)
        self.assertEqual(value["stationId"], "gauge")
        self.assertEqual(value["current"]["rainLastHourMm"], 0)
        self.assertTrue(value["rainGaugeRequired"])

    def test_bad_or_unchecked_rain_qc_does_not_qualify(self):
        reports = [(qc, 52.77, -1.56, 0, {"T": 280, "PCP24H": (0, qc)})
                   for qc in ("X", "Q", "B", "Z")]
        reports.append(("good", 52.8, -1.56, 0, {"T": 281, "PCP24H": (0, "C")}))
        provider, state = self.provider(reports)
        self.assertEqual(state["payload"]["stationId"], "good")

    def test_latest_qualifying_report_survives_a_newer_partial_report(self):
        reports = [
            ("gauge", 52.77, -1.56, 300, {"T": 280, "PCP1H": 0.001}),
            ("gauge", 52.77, -1.56, 0, {"T": 281}),
        ]
        rows = parse_reports(xml(reports), 52.77, -1.56, feels_like, NOW, require_rain=True)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["current"]["timestamp"], NOW - 300)
        self.assertEqual(rows[0]["current"]["rainLastHourMm"], 1)

    def test_no_gauge_reports_no_station_instead_of_accepting_temperature_only(self):
        provider, state = self.provider([("near", 52.77, -1.56, 0, {"T": 280})])
        self.assertIsNone(state["payload"])
        self.assertIn("rainfall", state["error"])
        with patch("cwop_weather.threading.Thread"):
            result = provider.snapshot(SETTINGS)
        self.assertFalse(result.get("current"))
        self.assertIsNone(result.get("stationId"))
        self.assertIn("rainfall", result["error"])

    def test_old_temperature_only_cache_is_invalidated_immediately(self):
        provider = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend)
        key, state = provider._state(SETTINGS)
        path = provider._path(key)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({
            "locationKey": key, "source": "cwop", "stationId": "old",
            "current": {"timestamp": NOW, "tempF": 50}, "points": [], "fetchedAt": NOW,
        }))
        again = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend)
        _, state = again._state(SETTINGS)
        self.assertIsNone(state["payload"])
        with patch("cwop_weather.threading.Thread") as thread:
            value = again.snapshot(SETTINGS)
        self.assertFalse(value.get("current"))
        self.assertTrue(value["loading"])
        self.assertEqual(thread.call_count, 2)

    def test_rain_reporting_cache_restarts_without_another_history_import(self):
        provider, state = self.provider([("gauge", 52.77, -1.56, 0, {"T": 280, "PCP24H": 0})])
        fetch = Mock(side_effect=AssertionError("A fresh saved gauge should be reused"))
        again = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend, fetch)
        value = again.snapshot(SETTINGS)
        self.assertEqual(value["stationId"], "gauge")
        self.assertEqual(value["current"]["rainLast24hMm"], 0)
        fetch.assert_not_called()

    def test_official_fallback_cannot_bypass_rain_gauge_requirement(self):
        official = Mock()
        official.snapshot.return_value = {
            "source": "metar", "stationId": "airport", "current": {"timestamp": NOW, "tempF": 50},
        }
        provider = PreferredPublicWeather(self.temp.name, official, feels_like, pressure_trend)
        _, state = provider._state(SETTINGS)
        state["checked"] = state["officialChecked"] = time.monotonic()
        provider._fallback(SETTINGS, state)
        value = provider.snapshot(SETTINGS)
        self.assertFalse(value.get("current"))
        self.assertIsNone(value.get("stationId"))
        self.assertIn("rain-gauge", value["error"])

    def test_explicit_official_mode_does_not_restore_an_ungauged_cached_station(self):
        provider, state = self.provider([("cwop", 52.77, -1.56, 0, {"T": 280, "PCP24H": 0})])
        state["official"] = {"stationId": "oldAirport", "current": {"timestamp": NOW, "tempF": 50}}
        with patch("cwop_weather.threading.Thread"):
            value = provider.snapshot({**SETTINGS, "weatherSource": "noaa"})
        self.assertFalse(value.get("current"))
        self.assertIsNone(value.get("stationId"))


if __name__ == "__main__":
    unittest.main()
