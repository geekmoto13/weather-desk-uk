"""Tokyo observations stay independent, current, cached and explicit about gaps."""
import datetime as dt
import sys
from pathlib import Path
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tokyo_rain import TokyoRain, parse_report, URL

STAMP = dt.datetime(2026, 11, 6, 3, tzinfo=dt.timezone.utc).timestamp()


def report(wx="", ending="NOSIG", **changes):
    row = {"icaoId": "RJTT", "obsTime": STAMP, "temp": 23, "visib": "6+",
           "rawOb": f"METAR RJTT 060300Z 18008KT 9999 {wx} FEW030 23/15 Q1005 {ending}"}
    row.update(changes)
    return row


class TokyoReportTests(unittest.TestCase):
    def test_current_rain_and_drizzle_include_showers_and_freezing_rain(self):
        for wx in ("RA", "-RA", "+SHRA", "TSRA", "DZ", "FZRA", "-DZRA", "RASN"):
            with self.subTest(wx=wx):
                self.assertEqual(parse_report([report(wx)], STAMP + 600)["condition"], "rain")

    def test_no_rain_is_not_a_claim_of_clear_skies(self):
        for wx in ("", "BR", "FG", "HZ", "TS", "BLSN", "DRSN"):
            with self.subTest(wx=wx):
                self.assertEqual(parse_report([report(wx)], STAMP)["condition"], "no_rain")

    def test_recent_rain_trend_and_remarks_are_not_current_rain(self):
        for wx, ending in [("RERA", "NOSIG"), ("", "TEMPO 3000 RA BKN005"),
                           ("", "BECMG 3000 RA"), ("", "RMK RA ENDED")]:
            with self.subTest(wx=wx, ending=ending):
                self.assertEqual(parse_report([report(wx, ending)], STAMP)["condition"], "no_rain")

    def test_vicinity_showers_and_snow_are_distinguished(self):
        for wx in ("VCSH", "VCRA", "VCSN"):
            self.assertEqual(parse_report([report(wx)], STAMP)["condition"], "nearby")
        for wx in ("SN", "-SHSN", "PL", "GR", "GS"):
            self.assertEqual(parse_report([report(wx)], STAMP)["condition"], "wintry")

    def test_unidentified_precipitation_or_missing_weather_is_unknown(self):
        for wx in ("UP", "-SHUP", "//", "///", "////", "SH"):
            self.assertEqual(parse_report([report(wx)], STAMP)["condition"], "unknown")

    def test_report_station_time_and_required_fields_are_validated(self):
        for changes in ({"icaoId": "KBOX"}, {"rawOb": "METAR RJTT 060330Z NIL"},
                        {"rawOb": "RJTT 060300Z NIL"}, {"rawOb": "KBOX 060300Z RA"},
                        {"temp": None}, {"temp": True}, {"temp": float("nan")},
                        {"obsTime": STAMP + 601}, {"visib": None}, {"rawOb": None}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_report([report(**changes)], STAMP)
        self.assertEqual(parse_report([report("+RA", visib=0)], STAMP)["condition"], "rain")

    def test_bad_or_empty_api_responses_are_not_dry(self):
        for rows in ([], None, {}, {"error": "service unavailable"}, [None]):
            with self.assertRaises(ValueError):
                parse_report(rows, STAMP)

    def test_newest_correct_station_is_used_not_foreign_or_older_report(self):
        old = report("RA", obsTime=STAMP - 1800, rawOb="RJTT 060230Z 18008KT 9999 RA 20/15 Q1005")
        foreign = report("RA", icaoId="RJAA", obsTime=STAMP + 60)
        self.assertEqual(parse_report([old, foreign, report()], STAMP)["condition"], "no_rain")

    def test_corrected_and_special_reports_are_supported(self):
        for prefix in ("RJTT", "METAR COR RJTT", "SPECI RJTT"):
            row=report("RA")
            row["rawOb"] = row["rawOb"].replace("METAR RJTT", prefix)
            self.assertEqual(parse_report([row], STAMP)["condition"], "rain")


class TokyoCacheTests(unittest.TestCase):
    def test_one_request_per_ten_minutes_and_returned_objects_are_copies(self):
        clock=[STAMP]; fetch=Mock(return_value=[report()]); service=TokyoRain(fetch, lambda: clock[0])
        result=service.get(); self.assertEqual(result["status"], "ok")
        result["condition"]="rain"
        clock[0]+=599
        self.assertEqual(service.get()["condition"], "no_rain")
        fetch.assert_called_once_with(URL)
        clock[0]+=1; service.get(); self.assertEqual(fetch.call_count, 2)

    def test_failed_refresh_is_off_air_with_last_report_and_retry_backoff(self):
        clock=[STAMP]; fetch=Mock(side_effect=[[report("RA")], OSError("network"), [report()]])
        service=TokyoRain(fetch, lambda: clock[0]); service.get()
        clock[0]+=600; result=service.get()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["condition"], "rain")
        service.get(); self.assertEqual(fetch.call_count,2)
        clock[0]+=120
        self.assertEqual(service.get()["status"], "ok")

    def test_initial_failure_has_no_fabricated_condition(self):
        service=TokyoRain(Mock(side_effect=RuntimeError("provider")), lambda: STAMP)
        result=service.get()
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("condition",result)
        self.assertNotIn("observedAt",result)

    def test_old_report_and_ageing_cached_report_are_marked_stale(self):
        clock=[STAMP+5300]; service=TokyoRain(lambda _: [report()], lambda: clock[0])
        self.assertEqual(service.get()["status"],"ok")
        clock[0]+=101
        self.assertEqual(service.get()["status"],"stale")

    def test_concurrent_page_requests_share_the_same_cached_fetch(self):
        fetch=Mock(return_value=[report()]); service=TokyoRain(fetch, lambda: STAMP)
        with ThreadPoolExecutor(max_workers=6) as pool:
            values=list(pool.map(lambda _: service.get(), range(12)))
        self.assertTrue(all(v["status"]=="ok" for v in values))
        fetch.assert_called_once_with(URL)


if __name__ == "__main__":
    unittest.main()
