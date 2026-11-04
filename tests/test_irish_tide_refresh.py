import copy
import datetime as dt
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ireland_tides import IrishTides
from ireland_sources import IrishPublicWeather
from server import feels_like, pressure_trend

NOW = dt.datetime(2026, 10, 25, 20, 40, tzinfo=dt.timezone.utc).timestamp()
SETTINGS = {"latitude": 52.14, "longitude": -10.27}


def tide_table():
    return {
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
                ["Dingle Harbour", -10.27732, 52.13, "2026-10-25T20:30:00Z", -1.50, 0],
                ["Dingle Harbour", -10.27732, 52.13, "2026-10-25T20:35:00Z", -1.48, 0],
                ["Dublin Port", -6.22, 53.34, "2026-10-25T20:35:00Z", 1.1, 1],
            ],
        }
    }


class IrishTideRefreshTests(unittest.TestCase):
    def test_first_response_is_loading_and_does_not_wait_for_network(self):
        fetch = Mock()
        provider = IrishTides(fetch)
        with patch("ireland_tides.threading.Thread") as worker:
            value = provider.snapshot(SETTINGS)
            self.assertTrue(value["loading"])
            worker.return_value.start.assert_called_once()
            fetch.assert_not_called()

    def test_small_discovery_then_only_selected_ports_history(self):
        raw = tide_table()
        point = copy.deepcopy(raw)
        point["table"]["rows"] = point["table"]["rows"][:2]
        fetch = Mock(side_effect=[raw, point])
        with patch("ireland_tides.time.time", return_value=NOW):
            provider = IrishTides(fetch)
            key, state = provider._state(SETTINGS)
            provider._load(key, SETTINGS, state)
        urls = [call.args[0] for call in fetch.call_args_list]
        self.assertIn("now-90minutes", urls[0])
        self.assertNotIn("now-24hours", urls[0])
        self.assertIn("now-24hours", urls[1])
        self.assertIn("station_id=%22Dingle%20Harbour%22", urls[1])
        self.assertEqual(state["payload"]["stationName"], "Dingle Harbour")
        self.assertEqual(state["payload"]["datumLabel"], "OD Malin")
        self.assertFalse(state["payload"]["historyPartial"])

    def test_failed_refresh_preserves_chart_and_marks_last_known_reading(self):
        fetch = Mock(return_value=tide_table())
        with patch("ireland_tides.time.time", return_value=NOW):
            provider = IrishTides(fetch)
            key, state = provider._state(SETTINGS)
            provider._load(key, SETTINGS, state)
            old = copy.deepcopy(state["payload"])
            provider.cache.items.clear()
            fetch.side_effect = OSError("Connection timed out")
            provider._load(key, SETTINGS, state)
            state["checked"] = time.monotonic()
            value = provider.snapshot(SETTINGS)
        self.assertEqual(value["observations"], old["observations"])
        self.assertEqual(value["latest"], old["latest"])
        self.assertTrue(value["stale"])
        self.assertIn("Connection timed out", value["error"])

    def test_cached_chart_reopens_after_restart(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "ireland_tides.time.time", return_value=NOW
        ):
            first = IrishTides(Mock(return_value=tide_table()), folder)
            key, state = first._state(SETTINGS)
            first._load(key, SETTINGS, state)
            reopened = IrishTides(Mock(side_effect=OSError), folder)
            _, saved = reopened._state(SETTINGS)
            saved["checked"] = time.monotonic()
            value = reopened.snapshot(SETTINGS)
        self.assertEqual(value["stationName"], "Dingle Harbour")
        self.assertEqual(len(value["observations"]), 2)

    def test_failed_history_uses_fresh_short_window_without_claiming_full_history(self):
        fetch = Mock(side_effect=[tide_table(), OSError("history timed out")])
        with patch("ireland_tides.time.time", return_value=NOW):
            provider = IrishTides(fetch)
            key, state = provider._state(SETTINGS)
            state["payload"] = {
                "stationName": "Another port",
                "observations": [{"timestamp": NOW - 300, "heightM": 12}],
            }
            provider._load(key, SETTINGS, state)
            state["checked"] = time.monotonic()
            value = provider.snapshot(SETTINGS)
        self.assertTrue(value["historyPartial"])
        self.assertEqual(value["stationName"], "Dingle Harbour")
        self.assertEqual(len(value["observations"]), 2)
        self.assertIn("24-hour history request failed", value["error"])
        self.assertFalse(value["stale"])

    def test_initial_outage_has_an_explicit_reason(self):
        provider = IrishTides(Mock(side_effect=OSError("service unavailable")))
        key, state = provider._state(SETTINGS)
        provider._load(key, SETTINGS, state)
        state["checked"] = time.monotonic()
        value = provider.snapshot(SETTINGS)
        self.assertEqual(value["observations"], [])
        self.assertTrue(value["stale"])
        self.assertIn("service unavailable", value["error"])

    def test_disk_cache_error_does_not_hide_live_data(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "ireland_tides.time.time", return_value=NOW
        ), patch("ireland_tides.write_private_json", side_effect=OSError):
            provider = IrishTides(Mock(return_value=tide_table()), folder)
            key, state = provider._state(SETTINGS)
            provider._load(key, SETTINGS, state)
            self.assertEqual(state["payload"]["stationName"], "Dingle Harbour")
            self.assertIn("could not be saved", state["payload"]["cacheNotice"])

    def test_reports_older_than_90_minutes_stay_stale(self):
        with patch(
            "ireland_sources.time.time", return_value=NOW
        ), tempfile.TemporaryDirectory() as folder:
            provider = IrishPublicWeather(folder, Mock(), feels_like, pressure_trend)
            key, state = provider._state(SETTINGS)
            state["payload"] = {
                "current": {"timestamp": NOW - 5401, "rainLastHourMm": 0},
                "stationName": "Valentia",
            }
            state["checked"] = time.monotonic()
            value = provider.snapshot(SETTINGS)
            self.assertTrue(value["stale"])
            self.assertEqual(value["current"]["timestamp"], NOW - 5401)
            self.assertEqual(value["staleAfterSeconds"], 5400)


if __name__ == "__main__":
    unittest.main()
