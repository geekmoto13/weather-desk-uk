"""Public-station archive recall, provider boundaries and optional WU access."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cwop_weather import PreferredPublicWeather, parse_pressure_history, query_url
from wunderground_weather import (WundergroundWeather, WundergroundError,
    nearby_sites, observation, fetch_json, NoRedirect)
from server import feels_like, pressure_trend, Desk
from test_cwop import NOW, xml

SETTINGS = {"latitude": 52.77, "longitude": -1.56, "weatherSource": "cwop"}
KEY = "fixture-key-not-real"
SITE = {"stationId": "ISWADL31", "stationName": "Gresley Station",
        "latitude": 52.766, "longitude": -1.559, "distanceKm": 0.5}


def wu_row(**changes):
    row = {"stationID": SITE["stationId"], "epoch": NOW, "lat": SITE["latitude"],
           "lon": SITE["longitude"], "qcStatus": 1, "humidity": 80, "winddir": 270,
           "solarRadiation": 10, "uv": 0,
           "metric": {"temp": 10, "dewpt": 7, "windSpeed": 16.09344,
                      "windGust": 24.14016, "pressure": 1007.5,
                      "precipRate": 0, "precipTotal": 0}}
    row.update(changes)
    return row


def near_payload():
    return {"location": {"stationId": [SITE["stationId"]], "stationName": [SITE["stationName"]],
            "latitude": [str(SITE["latitude"])], "longitude": [str(SITE["longitude"])],
            "qcStatus": [1], "distanceKm": [0.5]}}


class PressureRecallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        clock = patch("cwop_weather.time.time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def chosen(self):
        return {"stationId": "F1308", "latitude": 52.78, "longitude": -1.23,
                "current": {"timestamp": NOW, "pressureHpa": 1000, "pressureKind": "QNH"}}

    def test_pressure_only_archive_does_not_require_temperature_or_rain(self):
        raw = xml([("F1308", 52.78, -1.23, 10800, {"ALTSE": 100220})])
        points = parse_pressure_history(raw, self.chosen())
        self.assertEqual(len(points), 1)
        self.assertEqual(pressure_trend(self.chosen()["current"], points)["delta"], -2.2)

    def test_archive_does_not_mix_stations_datums_locations_bad_qc_or_wrong_times(self):
        cases = [
            ("OTHER", 52.78, -1.23, 10800, {"ALTSE": 100220}),
            ("F1308", 52.78, -1.23, 10800, {"SLP": 100220}),
            ("F1308", 53, -1.23, 10800, {"ALTSE": 100220}),
            ("F1308", 52.78, -1.23, 10800, {"ALTSE": (100220, "Q")}),
            ("F1308", 52.78, -1.23, 20000, {"ALTSE": 100220}),
            ("F1308", 52.78, -1.23, 10800, {"ALTSE": "NaN"}),
        ]
        self.assertEqual(parse_pressure_history(xml(cases), self.chosen()), [])

    def test_archive_query_uses_observation_relative_utc_time_and_bounded_window(self):
        params = parse_qs(urlsplit(query_url(52.78, -1.23, pressure_time=NOW-10800)).query)
        target = dt.datetime.fromtimestamp(NOW-10800, dt.timezone.utc).strftime("%Y%m%d_%H%M")
        self.assertEqual(params["time"], [target])
        self.assertEqual(params["minbck"], ["-30"])
        self.assertEqual(params["nvars"], ["SLP", "ALTSE"])

    def provider(self, archive, current=None):
        current = current or xml([("F1308", 52.78, -1.23, 0, {"T": 280, "ALTSE": 100000, "PCP1H": 0})])
        fetch = Mock(side_effect=lambda url: current if parse_qs(urlsplit(url).query)["time"] == ["0"] else archive)
        provider = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend, fetch)
        key, state = provider._state(SETTINGS)
        state["checked"] = time.monotonic()
        provider._load(key, SETTINGS, state)
        return provider, state, fetch

    def test_initial_pressure_recall_is_saved_and_reused_after_restart(self):
        p, state, fetch = self.provider(xml([("F1308", 52.78, -1.23, 10800, {"ALTSE": 100220})]))
        self.assertEqual(state["payload"]["trend"]["delta"], -2.2)
        self.assertEqual(fetch.call_count, 2)
        again = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend, Mock())
        self.assertEqual(again.snapshot(SETTINGS)["trend"]["delta"], -2.2)
        again.fetch.assert_not_called()

    def test_failed_archive_keeps_current_and_retries_only_after_backoff(self):
        p, state, fetch = self.provider("<mesonet/>")
        self.assertEqual(state["payload"]["current"]["pressureHpa"], 1000)
        self.assertIsNone(state["payload"]["trend"]["delta"])
        self.assertIn("No matching", state["payload"]["pressureHistoryNote"])
        p._load(p._state(SETTINGS)[0], SETTINGS, state)
        self.assertEqual(fetch.call_count, 3, "Second refresh should fetch current only")

    def test_visible_current_is_installed_before_archive_download(self):
        p = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend)
        key, state = p._state(SETTINGS)
        def fetch(url):
            if parse_qs(urlsplit(url).query)["time"] != ["0"]:
                self.assertEqual(state["payload"]["current"]["pressureHpa"], 1000)
                self.assertTrue(state["payload"]["pressureHistoryLoading"])
                raise TimeoutError()
            return xml([("F1308", 52.78, -1.23, 0, {"T": 280, "ALTSE": 100000, "PCP1H": 0})])
        p.fetch = fetch
        p._load(key, SETTINGS, state)
        self.assertFalse(state["payload"]["pressureHistoryLoading"])
        self.assertIn("could not", state["payload"]["pressureHistoryNote"])

    def test_current_without_pressure_does_not_make_archive_request(self):
        p, state, fetch = self.provider("<mesonet/>", xml([("A", 52.78, -1.23, 0, {"T": 280, "PCP1H": 0})]))
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(state["payload"]["trend"]["state"], "unavailable")

    def test_reset_discards_late_lookup_without_recreating_old_cache(self):
        p = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend)
        key, state = p._state(SETTINGS)
        def fetch(url):
            p.reset()
            return xml([("A", 52.78, -1.23, 0, {"T": 280, "PCP1H": 0})])
        p.fetch = fetch
        p._load(key, SETTINGS, state)
        self.assertFalse(p._path(key).exists())
        self.assertFalse(p.states)

    def test_old_fresh_cache_without_archive_attempt_refreshes_immediately(self):
        p = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend)
        key, state = p._state(SETTINGS)
        path = p._path(key)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"locationKey": key, "source": "cwop", "stationId": "F1308",
            "current": {"timestamp": NOW, "pressureHpa": 1000, "pressureKind": "QNH", "rainLastHourMm": 0},
            "points": [], "trend": {"delta": None}, "fetchedAt": NOW}))
        again = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend)
        with patch("cwop_weather.threading.Thread") as thread:
            value = again.snapshot(SETTINGS)
        self.assertTrue(value["publicSearchLoading"])
        thread.assert_called_once()

    def test_failed_archive_backoff_survives_restart(self):
        p, state, fetch = self.provider("<mesonet/>")
        fetch = Mock(return_value=xml([("F1308", 52.78, -1.23, 0, {"T": 280, "ALTSE": 100000, "PCP1H": 0})]))
        again = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend, fetch)
        key, state = again._state(SETTINGS)
        again._load(key, SETTINGS, state)
        fetch.assert_called_once()


class WundergroundTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        clock = patch("wunderground_weather.time.time", return_value=NOW)
        clock.start()
        self.addCleanup(clock.stop)

    def provider(self, fetch=None):
        return WundergroundWeather(self.temp.name, feels_like, fetch or Mock())

    def test_discovery_rejects_failed_unchecked_malformed_and_distant_sites(self):
        payload = near_payload()
        loc = payload["location"]
        for qc in (0, -1, None, True):
            loc["qcStatus"] = [qc]
            self.assertEqual(nearby_sites(payload, 52.77, -1.56), [])
        loc["qcStatus"] = [1]
        self.assertEqual(nearby_sites(payload, 52.77, -1.56)[0]["stationId"], "ISWADL31")
        loc["stationId"] = ["../../secret"]
        self.assertEqual(nearby_sites(payload, 52.77, -1.56), [])
        self.assertEqual(nearby_sites(near_payload(), 42, -70), [])

    def test_current_units_dry_zero_and_missing_counters(self):
        value = observation(wu_row(), SITE, 52.77, -1.56, feels_like)["current"]
        self.assertEqual(value["tempF"], 50)
        self.assertAlmostEqual(value["windMph"], 10)
        self.assertAlmostEqual(value["gustMph"], 15)
        self.assertEqual(value["pressureHpa"], 1007.5)
        self.assertEqual(value["pressureKind"], "sea-level")
        self.assertEqual(value["rainDayMm"], 0)
        for key in ("rainMonthMm", "rainYearMm", "rainLast24hMm"):
            self.assertIsNone(value[key])

    def test_requires_current_station_match_passed_qc_temperature_and_daily_rain(self):
        for changes in ({"stationID": "OTHER"}, {"epoch": NOW-3601}, {"epoch": NOW+301},
                        {"qcStatus": 0}, {"qcStatus": -1}, {"qcStatus": True}, {"lat": 51}):
            self.assertIsNone(observation(wu_row(**changes), SITE, 52.77, -1.56, feels_like))
        for field in ("temp", "precipTotal"):
            for bad in (None, True, "0", float("nan")):
                row = wu_row(); row["metric"][field] = bad
                self.assertIsNone(observation(row, SITE, 52.77, -1.56, feels_like))

    def test_no_key_means_no_network_requests(self):
        fetch = Mock()
        p = self.provider(fetch)
        self.assertEqual(p.candidates(SETTINGS), [])
        self.assertEqual(p.pressure_history({}), [])
        fetch.assert_not_called()

    def test_saved_session_forgotten_keys_and_status_privacy(self):
        p = self.provider()
        p.update({"action": "connect", "apiKey": KEY, "remember": True})
        self.assertTrue(self.provider().connection()["configured"])
        self.assertNotIn(KEY, json.dumps(p.connection()))
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        self.assertFalse(self.provider().connection()["configured"])
        self.assertTrue(p.connection()["configured"])
        p.update({"action": "forget"})
        self.assertFalse(p.connection()["configured"])

    def test_key_validation_and_failed_save_leave_existing_connection(self):
        p = self.provider()
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        for key in (None, True, "short", "a" * 201, "x?apiKey=oops"):
            with self.assertRaises(WundergroundError):
                p.update({"action": "connect", "apiKey": key})
            self.assertEqual(p.api_key, KEY)

    def test_nearby_search_uses_documented_endpoints_and_caches_directory(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return near_payload() if urlsplit(url).path == "/v3/location/near" else {"observations": [wu_row()]}
        p = self.provider(fetch)
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        self.assertEqual(p.candidates(SETTINGS)[0]["stationId"], "ISWADL31")
        p.candidates(SETTINGS)
        self.assertEqual(sum(urlsplit(u).path == "/v3/location/near" for u in urls), 1)
        for url in urls:
            self.assertEqual(urlsplit(url).netloc, "api.weather.com")
            self.assertEqual(parse_qs(urlsplit(url).query)["apiKey"], [KEY])
        self.assertEqual(parse_qs(urlsplit(urls[1]).query)["units"], ["m"])

    def test_pressure_ranges_are_marked_approximate_and_ambiguous_direction_is_not_invented(self):
        history = wu_row(epoch=NOW-10800, metric={"pressureMin": 1005, "pressureMax": 1005.2})
        p = self.provider(Mock(return_value={"observations": [history]}))
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        chosen = observation(wu_row(), SITE, 52.77, -1.56, feels_like)
        points = p.pressure_history(chosen)
        self.assertTrue(points[0]["pressureApproximate"])
        for pressure, state in ((1006, "rising"), (1004, "falling"), (1005.3, "steady"), (1005.6, "uncertain")):
            result = pressure_trend({"timestamp": NOW, "pressureHpa": pressure}, points)
            self.assertEqual(result["state"], state)
            self.assertTrue(result["approximate"])

    def test_history_rejects_wrong_site_time_qc_and_inconsistent_ranges(self):
        row = wu_row(epoch=NOW-10800, metric={"pressureMin": 1000, "pressureMax": 1002})
        p = self.provider(Mock(return_value={"observations": [row]}))
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        chosen = observation(wu_row(), SITE, 52.77, -1.56, feels_like)
        self.assertEqual(p.pressure_history(chosen), [])
        for changes in ({"epoch": NOW}, {"stationID": "OTHER"}, {"qcStatus": 0}, {"lat": 51}):
            row = wu_row(epoch=NOW-10800, metric={"pressure": 1005}); row.update(changes)
            p.fetch.return_value = {"observations": [row]}
            self.assertEqual(p.pressure_history(chosen), [])

    def test_errors_do_not_disclose_key_or_request_url_and_redirects_are_refused(self):
        p = self.provider(Mock(side_effect=TimeoutError("https://api.weather.com/?apiKey=" + KEY)))
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        with self.assertRaises(WundergroundError) as caught:
            p.candidates(SETTINGS)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://elsewhere.invalid"))
        with patch("wunderground_weather.build_opener") as opener:
            opener.return_value.open.side_effect = HTTPError("https://api.weather.com/?apiKey=" + KEY, 403, "denied", {}, None)
            with self.assertRaises(WundergroundError) as caught:
                fetch_json("https://api.weather.com/?apiKey=" + KEY)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn("API key", str(caught.exception))

    def test_key_change_during_lookup_discards_results(self):
        p = self.provider()
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        def fetch(url):
            p.update({"action": "forget"})
            return near_payload()
        p.fetch = fetch
        self.assertEqual(p.candidates(SETTINGS), [])

    def test_nearer_station_without_daily_rain_is_skipped(self):
        payload = near_payload()
        for name, value in {"stationId": "GOOD2", "stationName": "Farther gauge",
                            "latitude": 52.79, "longitude": -1.56, "qcStatus": 1,
                            "distanceKm": 2}.items():
            payload["location"][name].append(value)
        def fetch(url):
            params = parse_qs(urlsplit(url).query)
            if urlsplit(url).path == "/v3/location/near":
                return payload
            sid = params["stationId"][0]
            if sid == "GOOD2":
                return {"observations": [wu_row(stationID="GOOD2", lat=52.79, lon=-1.56)]}
            row = wu_row(); row["metric"]["precipTotal"] = None
            return {"observations": [row]}
        p = self.provider(fetch)
        p.update({"action": "connect", "apiKey": KEY, "remember": False})
        self.assertEqual(p.candidates(SETTINGS)[0]["stationId"], "GOOD2")

    def test_wu_can_supply_readings_when_cwop_is_unavailable(self):
        nearby = Mock()
        nearby.candidates.return_value = [observation(wu_row(), SITE, 52.77, -1.56, feels_like)]
        nearby.pressure_history.return_value = []
        p = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend,
                                  Mock(side_effect=TimeoutError()), nearby=nearby)
        key, state = p._state(SETTINGS)
        p._load(key, SETTINGS, state)
        self.assertEqual(state["payload"]["source"], "wunderground")
        self.assertIsNone(state["error"])

    def test_pool_picks_nearest_qualifying_source_and_does_not_mix_history(self):
        wu = observation(wu_row(), SITE, 52.77, -1.56, feels_like)
        nearby = Mock(); nearby.candidates.return_value = [wu]; nearby.pressure_history.return_value = []
        p = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend,
             Mock(return_value=xml([("F1308", 52.78, -1.23, 0, {"T": 280, "ALTSE": 100000, "PCP1H": 0})])), nearby=nearby)
        key, state = p._state(SETTINGS)
        state["payload"] = {"stationId": "ISWADL31", "source": "cwop", "points": [
            {"timestamp": NOW-10800, "pressureHpa": 900, "pressureKind": "sea-level"}]}
        p._load(key, SETTINGS, state)
        self.assertEqual(state["payload"]["source"], "wunderground")
        self.assertIsNone(state["payload"]["trend"]["delta"])
        self.assertEqual(len(state["payload"]["points"]), 1)

    def test_wu_failure_preserves_cwop_and_forget_invalidates_saved_wu(self):
        nearby = Mock(); nearby.candidates.side_effect = WundergroundError("Weather Underground could not refresh.")
        p = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend,
             Mock(return_value=xml([("A", 52.78, -1.23, 0, {"T": 280, "PCP1H": 0})])), nearby=nearby)
        key, state = p._state(SETTINGS)
        p._load(key, SETTINGS, state)
        self.assertEqual(state["payload"]["source"], "cwop")
        self.assertIn("Weather Underground", state["payload"]["publicSourceNote"])
        value = observation(wu_row(), SITE, 52.77, -1.56, feels_like)
        value.update(locationKey=key, fetchedAt=NOW, points=[])
        p._path(key).write_text(json.dumps(value))
        no_key = self.provider()
        again = PreferredPublicWeather(self.temp.name, Mock(), feels_like, pressure_trend, nearby=no_key)
        self.assertIsNone(again._state(SETTINGS)[1]["payload"])

    def test_official_mode_does_not_query_optional_provider_and_ambient_keeps_priority(self):
        with tempfile.TemporaryDirectory() as directory, patch("cwop_weather.threading.Thread"):
            d = Desk(directory)
            d.api_key = "fixture"; d.app_key = "fixture"
            self.assertEqual(d.weather_mode(), "ambient")
            d.public_station_settings({"action": "connect", "apiKey": KEY, "remember": False})
            self.assertEqual(d.weather_mode(), "ambient")
            self.assertNotIn(KEY, json.dumps(d.status()))
            d.wunderground.candidates = Mock()
            d.public_weather.snapshot({**SETTINGS, "weatherSource": "noaa"})
            d.wunderground.candidates.assert_not_called()
            d.db.close()


if __name__ == "__main__":
    unittest.main()
