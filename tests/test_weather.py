import copy
import datetime as dt
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server


def reading(**overrides):
    row = {"dateutc": int(time.time()*1000), "tempf": 68, "humidity": 50,
           "winddir": 270, "windspeedmph": 10, "windgustmph": 15,
           "baromrelin": 29.92, "baromabsin": 28,
           "dailyrainin": .1, "eventrainin": .2, "weeklyrainin": 1,
           "monthlyrainin": 2, "hourlyrainin": 0, "solarradiation": 0, "uv": 0}
    row.update(overrides)
    return row


def device(mac="AA:BB:CC:DD:EE:FF", **values):
    return {"macAddress": mac, "info": {"name": "Test station"}, "lastData": reading(**values)}


class NormalizationTests(unittest.TestCase):
    def test_yearly_rain_counter_and_missing_diagnostics(self):
        self.assertEqual(server.normalize(reading(yearlyrainin=0))["rainYearMm"], 0)
        self.assertAlmostEqual(server.normalize(reading(yearlyrainin="12.34"))["rainYearMm"], 313.436)
        for value in (None, ""):
            self.assertEqual(server.normalize(reading(yearlyrainin=value))["rainYearStatus"], "missing")
        for value in (-1, "NaN", "fault", True):
            self.assertEqual(server.normalize(reading(yearlyrainin=value))["rainYearStatus"], "invalid")
        data = server.normalize(reading(totalrainin=22.5))
        self.assertIsNone(data["rainYearMm"], "The lifetime total is not the calendar-year total")
        self.assertAlmostEqual(data["rainTotalMm"],22.5*25.4)
        self.assertEqual(data["rainYearStatus"], "missing")

    def test_conversions_relative_pressure_and_rain(self):
        data = server.normalize(reading())
        self.assertAlmostEqual(data["pressureHpa"], 1013.20749, places=4)
        self.assertAlmostEqual(data["rainDayMm"], 2.54)
        self.assertAlmostEqual(data["rainWeekMm"], 25.4)
        self.assertAlmostEqual(data["dewF"], 48.7, delta=.2)

    def test_missing_is_not_zero(self):
        data = server.normalize({})
        for key in ("tempF", "feelsF", "dewF", "humidity", "windMph", "pressureHpa",
                    "rainDayMm", "rainWeekMm", "solar", "uv", "timestamp"):
            self.assertIsNone(data[key], key)
        zero = server.normalize(reading(tempf=0, humidity=0, windspeedmph=0))
        self.assertEqual(zero["tempF"], 0)
        self.assertEqual(zero["humidity"], 0)
        self.assertEqual(zero["solar"], 0)
        self.assertEqual(zero["rainRateMmHr"], 0)

    def test_invalid_nonfinite_out_of_range_values(self):
        data = server.normalize(reading(humidity=101, windspeedmph="NaN", tempf=True,
                                        solarradiation=-1, baromrelin="Infinity", dailyrainin=-1))
        for key in ("humidity", "windMph", "tempF", "solar", "pressureHpa", "rainDayMm"):
            self.assertIsNone(data[key], key)

    def test_provider_feels_and_dew_are_preferred(self):
        data = server.normalize(reading(feelsLike=66, dewPoint=49))
        self.assertEqual(data["feelsF"], 66)
        self.assertEqual(data["dewF"], 49)
        self.assertEqual(data["feelsSource"], "Ambient Weather")

    def test_wind_chill_and_heat_index_validity(self):
        chill, source = server.feels_like(30, 50, 10)
        self.assertAlmostEqual(chill, 21.25, delta=.1)
        self.assertIn("wind chill", source)
        self.assertEqual(server.feels_like(60, 50, 15)[0], 60)
        self.assertEqual(server.feels_like(45, 50, 2)[0], 45)
        heat, source = server.feels_like(90, 70, 0)
        self.assertAlmostEqual(heat, 105.92, delta=.1)
        self.assertIn("heat index", source)

    def test_iso_observation_time(self):
        data = server.normalize({"date": "2026-10-21T19:00:00Z"})
        self.assertEqual(data["timestamp"], dt.datetime(2026,10,21,19,tzinfo=server.UTC).timestamp())

    def test_no_absolute_pressure_substitution(self):
        data = server.normalize({"baromabsin": 30})
        self.assertIsNone(data["pressureHpa"])


class TrendTests(unittest.TestCase):
    def test_three_hour_direction(self):
        current = {"timestamp": 20000, "pressureHpa": 1015}
        for old, expected in [(1014, "rising"), (1016, "falling"), (1014.8, "steady")]:
            result = server.pressure_trend(current, [{"timestamp": 9200, "pressureHpa": old}])
            self.assertEqual(result["state"], expected)
            self.assertEqual(result["hours"], 3)

    def test_reject_wrong_span_and_missing_readings(self):
        current = {"timestamp": 20000, "pressureHpa": 1015}
        result = server.pressure_trend(current, [{"timestamp": 18000, "pressureHpa": 1000}])
        self.assertIsNone(result["delta"])
        result = server.pressure_trend({"timestamp": 20000, "pressureHpa": None}, [])
        self.assertEqual(result["state"], "unavailable")


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.desk = server.Desk(self.temp.name)

    def tearDown(self):
        self.desk.db.close()
        self.temp.cleanup()

    def connect(self, devices=None, mac=""):
        with patch.object(self.desk, "ambient", return_value=devices or [device()]):
            return self.desk.connect({"apiKey": "test-api-value", "applicationKey": "test-app-value", "macAddress": mac})

    def test_demo_never_stored(self):
        self.desk.settings['weatherSource']='demo'
        self.assertTrue(self.desk.weather()["demo"])
        self.desk.history()
        self.assertEqual(self.desk.db.execute("SELECT count(*) FROM observations").fetchone()[0], 0)

    def test_annual_history_adapter_adds_completed_month_to_live_month(self):
        self.desk.settings["timezone"]="America/New_York"
        from annual_rain import month_start
        end = month_start(2026, 2, "America/New_York")
        live = reading(dateutc=int((end+86400)*1000), monthlyrainin=.2, tz="US/Eastern")
        self.connect([{"macAddress": "AA:BB:CC:DD:EE:FF", "lastData": live}])
        past = reading(dateutc=int((end-300)*1000), monthlyrainin=1.6, tz="US/Eastern")
        requests = []
        def fetch(path, **params):
            requests.append((path, params))
            return [past]
        with patch.object(self.desk, "ambient", side_effect=fetch):
            self.desk.annual()
            self.desk.annual_rain.jobs[(self.desk.mac, "America/New_York", 2026)][1].join(3)
            result = self.desk.annual()
        self.assertTrue(result["complete"])
        self.assertAlmostEqual(result["mm"]/25.4, 1.8)
        self.assertEqual(result["start"], "2026-01-01")
        self.assertEqual(result["through"], "2026-02-02")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][1]["_credentials"], ("test-api-value", "test-app-value"))
        self.assertEqual(requests[0][1]["limit"], 288)
        self.assertNotIn("test-api-value", json.dumps(result))

    def test_annual_history_rejects_a_mismatched_daily_reset_timezone(self):
        self.connect([device(tz="UTC")])
        with patch.object(self.desk, "ambient") as fetch:
            result = self.desk.annual()
        self.assertFalse(result["complete"])
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("timezone", result["error"])
        fetch.assert_not_called()

    def test_available_annual_counter_never_starts_an_annual_history_download(self):
        self.connect([device(yearlyrainin=26.72)])
        with patch.object(self.desk.annual_rain, "ensure") as ensure, patch.object(self.desk, "ambient") as fetch:
            result = self.desk.annual()
        self.assertEqual(result["status"], "reported")
        ensure.assert_not_called();fetch.assert_not_called()

    def test_multi_station_requires_selection_and_does_not_switch(self):
        self.connect()
        previous = self.desk.mac
        result = self.connect([device(), device("00:11:22:33:44:55")])
        self.assertTrue(result["needsSelection"])
        self.assertEqual(self.desk.mac, previous)
        self.assertEqual(self.desk.api_key, "test-api-value")

    def test_station_selection_and_history_isolation(self):
        devices = [device(), device("00:11:22:33:44:55", tempf=80)]
        self.connect(devices, "00:11:22:33:44:55")
        self.assertEqual(self.desk.weather()["current"]["tempF"], 80)
        self.assertEqual(len(self.desk.saved_history()), 1)
        self.connect([device(tempf=40)])
        self.assertEqual(len(self.desk.saved_history()), 1)
        self.assertEqual(self.desk.saved_history()[0]["tempF"], 40)

    def test_failed_connection_keeps_previous_session(self):
        self.connect()
        before = (self.desk.api_key, self.desk.app_key, self.desk.mac, copy.deepcopy(self.desk.cache))
        with patch.object(self.desk, "ambient", side_effect=server.WeatherError("Service unavailable")):
            with self.assertRaises(server.WeatherError):
                self.desk.connect({"apiKey": "new-api-value", "applicationKey": "new-app-value"})
        self.assertEqual((self.desk.api_key, self.desk.app_key, self.desk.mac, self.desk.cache), before)

    def test_failed_refresh_keeps_reading_and_marks_stale(self):
        self.connect()
        old_stamp = self.desk.cache["timestamp"]
        self.desk.cache_at -= 61
        with patch.object(self.desk, "ambient", side_effect=server.WeatherError("Service unavailable")) as upstream:
            first = self.desk.weather()
            second = self.desk.weather()
        self.assertTrue(first["stale"])
        self.assertEqual(first["current"]["timestamp"], old_stamp)
        self.assertEqual(second["error"], "Service unavailable")
        self.assertEqual(upstream.call_count, 1)

    def test_credentials_excluded_from_status_preferences_and_history(self):
        self.connect()
        self.desk.settings_update({"title": "Local test", "apiKey": "do-not-save"})
        public = json.dumps(self.desk.status())
        files = (Path(self.temp.name)/"preferences.json").read_text()
        history = json.dumps(self.desk.saved_history())
        for output in (public, files, history):
            self.assertNotIn("test-api-value", output)
            self.assertNotIn("test-app-value", output)
            self.assertNotIn("do-not-save", output)
        self.desk.disconnect()
        self.assertEqual(self.desk.api_key, "")
        with patch.object(self.desk.public_weather,"snapshot",return_value={"source":"noaa","demo":False}):
            self.assertEqual(self.desk.weather()["source"],"noaa")

    def test_history_deduplication_and_partial_values(self):
        self.connect()
        row = server.normalize(reading())
        self.desk.store([row, row])
        records = self.desk.saved_history()
        self.assertEqual(len({r["timestamp"] for r in records}), len(records))

    def test_uk_tides_use_observed_coastal_provider(self):
        payload={'observed':True,'observations':[],'loading':True,'predictionsUrl':'https://easytide.admiralty.co.uk/'}
        with patch.object(self.desk.uk_tides,'snapshot',return_value=payload) as tide:
            value=self.desk.tides()
        self.assertEqual(value,payload)
        self.assertEqual(tide.call_args.args[0]['latitude'],self.desk.settings['latitude'])



class LocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = server.create_server(port=0, data_dir=cls.temp.name)
        cls.server.desk.settings['weatherSource']='demo'
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.server.desk.db.close()
        cls.temp.cleanup()

    def test_assets_and_demo_api(self):
        with urlopen(self.url) as response:
            self.assertIn(b"weatherCard", response.read())
            self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        with urlopen(self.url+"/api/weather") as response:
            self.assertTrue(json.load(response)["demo"])

    def test_radio_api_is_independent_of_station_and_never_serves_private_cache(self):
        saved = {"settings": {"mode": "SSB"}, "sources": {}, "path": {"value": None, "stale": True}}
        with patch.object(self.server.desk.radio_space, "snapshot", return_value=saved) as snapshot:
            with urlopen(self.url+"/api/radio") as response:
                self.assertEqual(json.load(response), saved)
                self.assertEqual(response.headers["Cache-Control"], "no-store")
            snapshot.assert_called_once()
        for path in ("/radio_space.py", "/private/radio/preferences.json", "/private/radio/hamqsl.json", "/private/radio/daily.json"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(self.url+path)
            self.assertEqual(caught.exception.code, 404)
        with urlopen(self.url) as response:
            policy = response.headers["Content-Security-Policy"]
        self.assertIn("https://prop.kc2g.com", policy)
        self.assertIn("https://services.swpc.noaa.gov", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("connect-src 'self'", policy)

    def test_radio_settings_require_token_and_origin_then_persist(self):
        from radio_space import RadioDesk
        body = b'{"target":"custom","customGrid":"IO91wm","mode":"CW","power":50}'
        headers = {"Content-Type": "application/json"}
        for extra in ({}, {"X-Weather-Token": self.server.desk.token, "Origin": "https://evil.example"}):
            with self.assertRaises(HTTPError) as caught:
                urlopen(Request(self.url+"/api/radio/settings", data=body, headers={**headers, **extra}))
            self.assertEqual(caught.exception.code, 403)
        request = Request(self.url+"/api/radio/settings", data=body,
            headers={**headers, "X-Weather-Token": self.server.desk.token, "Origin": self.url})
        with urlopen(request) as response:
            prefs = json.load(response)["settings"]
        self.assertEqual((prefs["mode"], prefs["customGrid"], prefs["power"]), ("CW", "IO91WM", 50))
        self.assertEqual(RadioDesk(self.temp.name).prefs, prefs)

    def test_invalid_radio_preferences_leave_saved_settings_unchanged(self):
        before = copy.deepcopy(self.server.desk.radio_space.prefs)
        request = Request(self.url+"/api/radio/settings", data=b'{"power":0}',
            headers={"Content-Type":"application/json", "X-Weather-Token":self.server.desk.token, "Origin":self.url})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.server.desk.radio_space.prefs, before)

    def test_every_page_script_and_stylesheet_can_be_loaded(self):
        from html.parser import HTMLParser
        from urllib.parse import parse_qs, urlsplit
        class Assets(HTMLParser):
            def __init__(self):
                super().__init__()
                self.paths = []
            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "script" and attrs.get("src"):
                    self.paths.append(attrs["src"])
                if tag == "link" and attrs.get("rel") == "stylesheet":
                    self.paths.append(attrs["href"])
        with urlopen(self.url) as response:
            page = response.read().decode()
        parser = Assets()
        parser.feed(page)
        with urlopen(self.url+"/api/status") as response:
            build = json.load(response)["build"]
        for path in parser.paths:
            with self.subTest(path=path), urlopen(self.url+path) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(len(response.read()), 0)
            version = parse_qs(urlsplit(path).query).get("v")
            if version:
                self.assertEqual(version, [build])

    def test_private_and_path_traversal_not_served(self):
        for path in ("/server.py", "/climate.py", "/local_store.py", "/private/station.json", "/private/preferences.json", "/../server.py", "/%2e%2e/server.py"):
            with self.assertRaises(HTTPError) as caught:
                urlopen(self.url+path)
            self.assertEqual(caught.exception.code, 404)

    def test_climate_route_keeps_saved_date_and_status(self):
        saved = {"date": "2026-10-21", "stationId": "BOSthr", "stale": True,
                 "error": "Climate refresh unavailable", "records": {"high": {"valueF": 83, "years": [2024]}}}
        with patch.object(self.server.desk.climate, "get", return_value=saved):
            with urlopen(self.url+"/api/climate") as response:
                result = json.load(response)
        self.assertEqual(result, saved)

    def test_historical_route_uses_date_refresh_and_rejects_invalid_days(self):
        saved = {"date":"2026-10-08","stationData":None,"loading":True}
        with patch.object(self.server.desk,"past_day",return_value=saved) as history:
            with urlopen(self.url+"/api/day?date=2026-10-08&refresh=1") as response:
                self.assertEqual(json.load(response),saved)
                self.assertEqual(response.headers['Cache-Control'],'no-store')
            history.assert_called_once_with('2026-10-08',True)
        for date in ('','tomorrow','2026-02-30','2999-01-01'):
            with self.subTest(date=date),self.assertRaises(HTTPError) as caught:
                urlopen(self.url+'/api/day?date='+date)
            self.assertEqual(caught.exception.code,400)
        for path in ('/historical_days.py','/private/history-days.sqlite3'):
            with self.assertRaises(HTTPError) as caught:urlopen(self.url+path)
            self.assertEqual(caught.exception.code,404)

    def test_local_alert_route_and_retired_audio_assets(self):
        with patch.object(self.server.desk.warnings, "fetch", return_value={"type": "FeatureCollection", "features": []}):
            with urlopen(self.url+"/api/alerts") as response:
                result = json.load(response)
        self.assertEqual(result["source"], "Met Office Weather Warnings")
        self.assertFalse(result["stale"])
        self.assertEqual(result["alerts"], [])
        self.assertIsNotNone(result["checkedAt"])
        for path in ("/music.js", "/music-engine.js", "/music.css", "/music/chills.mp3", "/nws_alerts.py"):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                urlopen(self.url+path)
            self.assertEqual(caught.exception.code, 404)

    def test_untrusted_host_rejected(self):
        request = Request(self.url+"/api/status", headers={"Host": "evil.example"})
        with self.assertRaises(HTTPError) as caught:
            urlopen(request)
        self.assertEqual(caught.exception.code, 403)

    def test_settings_requires_token_and_same_origin(self):
        for headers in ({}, {"X-Weather-Token": self.server.desk.token, "Origin": "https://evil.example"}):
            request = Request(self.url+"/api/settings", data=b'{"title":"Changed"}', headers={"Content-Type":"application/json", **headers})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code, 403)
        request = Request(self.url+"/api/settings", data=b'{"title":"My desk"}',
                          headers={"Content-Type":"application/json", "X-Weather-Token":self.server.desk.token, "Origin":self.url})
        with urlopen(request) as response:
            self.assertEqual(json.load(response)["settings"]["title"], "My desk")


if __name__ == "__main__":
    unittest.main()
