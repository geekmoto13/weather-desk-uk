"""Recall pressure from the selected airport without relaxing current selection."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import feels_like, pressure_trend
from uk_sources import UKPublicWeather

NOW = 1792893600
SETTINGS = {"latitude": 52.77, "longitude": -1.56}


def row(age=0, **changes):
    value = {"icaoId": "EGNX", "name": "East Midlands", "lat": 52.833, "lon": -1.333,
             "temp": 12, "precip": 0, "obsTime": NOW-age, "altim": 1000}
    value.update(changes)
    return value


class AirportPressureTests(unittest.TestCase):
    def load(self, archive):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        fetch = Mock(side_effect=[[row()], archive])
        provider = UKPublicWeather(directory.name, fetch, None, feels_like, pressure_trend)
        with patch("uk_sources.time.time", return_value=NOW):
            value = provider.snapshot(SETTINGS)
        return value, fetch

    def test_selected_airport_pressure_is_recalled_without_temperature_or_rain(self):
        value, fetch = self.load([row(10800, altim=1002.2, temp=None, precip=None)])
        self.assertEqual(value["trend"]["delta"], -2.2)
        params = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params["ids"], ["EGNX"])
        self.assertEqual(params["hours"], ["5"])

    def test_archive_cannot_mix_airports_datums_or_locations(self):
        for older in (row(10800, icaoId="EGBB", altim=1002),
                      row(10800, slp=1002), row(10800, lat=51), row(18000, altim=1002)):
            with self.subTest(older=older):
                value, fetch = self.load([older])
                self.assertIsNone(value["trend"]["delta"])
                self.assertEqual(value["current"]["pressureHpa"], 1000)

    def test_history_failure_keeps_current_observation(self):
        value, fetch = self.load(TimeoutError())
        self.assertEqual(value["stationId"], "EGNX")
        self.assertIsNone(value["trend"]["delta"])
        self.assertIsNone(value["error"])


if __name__ == "__main__":
    unittest.main()
