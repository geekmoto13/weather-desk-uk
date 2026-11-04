"""Realistic ACIS response shapes, matching date windows, missing data and cache rollover."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import climate


def series(today):
    start = today.replace(month=1, day=1)
    data = []
    for n in range((today-start).days+1):
        day = start + dt.timedelta(days=n)
        # Today deliberately contains a large partial value: it must be excluded.
        amount = "99.00" if day == today else "0.10"
        data.append([day.isoformat(), ["50", " "], ["30", " "], [amount, " "], "48.0", "29.0", "0.200"])
    return {"meta": {"name": "Boston Area"}, "data": data}


def records():
    return {"meta": {"name": "Boston Area", "valid_daterange": [["1872-03-01", "2026-01-03"]]*2},
            "data": [["1872", ["80"], ["20"]], ["1920", ["85"], ["-2"]],
                     ["1979", ["85"], ["3"]], ["2026", ["110"], ["-99"]]]}


class ClimateTests(unittest.TestCase):
    def test_personal_station_normals_include_today_without_using_boston_observations(self):
        today = dt.date(2026, 1, 4)
        raw = series(today)
        for row in raw["data"]:
            row[3] = ["M", " "]
        data = climate.daily_almanac(raw, records(), today)
        self.assertEqual(data["normal"]["rainMonthToDateIn"], .8)
        self.assertEqual(data["normal"]["rainYearToDateIn"], .8)
        self.assertEqual(data["normal"]["through"], today.isoformat())
        self.assertEqual(data["schemaVersion"], 2)

    def test_monthly_normals_use_full_past_months_and_live_month_to_date(self):
        today = dt.date(2026, 3, 3)
        data = climate.daily_almanac(series(today), records(), today)
        months = data['normal']['rainMonths']
        self.assertEqual([m['month'] for m in months], [1, 2, 3])
        self.assertEqual([m['normalIn'] for m in months], [6.2, 5.6, .6])
        self.assertEqual([m['through'] for m in months], ['2026-01-31', '2026-02-28', '2026-03-03'])
        self.assertEqual([m['partial'] for m in months], [False, False, True])
        self.assertEqual(months[-1]['normalIn'], data['normal']['rainMonthToDateIn'])
        self.assertAlmostEqual(sum(m['normalIn'] for m in months), data['normal']['rainYearToDateIn'])

    def test_missing_normal_does_not_create_a_false_dry_month(self):
        today = dt.date(2026, 2, 3)
        raw = series(today)
        raw['data'][4][6] = 'M'
        months = climate.daily_almanac(raw, records(), today)['normal']['rainMonths']
        self.assertIsNone(months[0]['normalIn'])
        self.assertEqual(months[0]['missingNormalDays'], 1)
        self.assertEqual(months[1]['normalIn'], .6)

    def test_monthly_normals_do_not_depend_on_boston_observed_rain(self):
        today = dt.date(2026, 2, 3)
        raw = series(today)
        for row in raw['data']: row[3] = ['M', ' ']
        months = climate.daily_almanac(raw, records(), today)['normal']['rainMonths']
        self.assertEqual([m['normalIn'] for m in months], [6.2, .6])

    def test_monthly_normals_include_february_29_and_january_needs_only_one_row(self):
        leap = dt.date(2028, 3, 1)
        months = climate.daily_almanac(series(leap), records(), leap)['normal']['rainMonths']
        self.assertEqual(months[1]['days'], 29)
        self.assertEqual(months[1]['through'], '2028-02-29')
        self.assertEqual(months[1]['normalIn'], 5.8)
        new_year = dt.date(2026, 1, 1)
        jan = climate.daily_almanac(series(new_year), records(), new_year)['normal']['rainMonths']
        self.assertEqual(len(jan), 1); self.assertEqual(jan[0]['normalIn'], .2)

    def test_v9_cache_loads_new_monthly_fields_without_waiting_30_minutes(self):
        today = dt.date(2026, 2, 3)
        with tempfile.TemporaryDirectory() as folder:
            old = climate.daily_almanac(series(today), records(), today)
            old['normal'].pop('rainMonths')
            old['fetchedAt'] = time.time()
            (Path(folder)/'boston-climate.json').write_text(json.dumps(old))
            fetch = Mock(side_effect=[series(today), records()])
            result = climate.BostonClimate(folder, fetch).get(today)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(len(result['normal']['rainMonths']), 2)

    def test_daily_precipitation_record_includes_ties_and_skips_trace_or_missing(self):
        raw = records()
        for row, amount in zip(raw["data"], ["T", "1.53", "1.53", "99.00"]):
            row.append([amount])
        result = climate.records_for_day(raw, dt.date(2026, 1, 4))
        self.assertEqual(result["rain"], {"valueIn": 1.53, "years": [1920, 1979]})
        raw["data"][1][3] = ["M"]
        self.assertEqual(climate.records_for_day(raw, dt.date(2026, 1, 4))["rain"]["years"], [1979])

    def test_legacy_cache_fetches_new_record_and_normal_fields_immediately(self):
        today = dt.date(2026, 1, 4)
        with tempfile.TemporaryDirectory() as folder:
            old = climate.daily_almanac(series(today), records(), today)
            old.pop("schemaVersion")
            old["fetchedAt"] = time.time()
            (Path(folder)/"boston-climate.json").write_text(json.dumps(old))
            fetch = Mock(side_effect=[series(today), records()])
            data = climate.BostonClimate(folder, fetch).get(today)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(data["schemaVersion"], 2)

    def test_precipitation_uses_identical_completed_day_windows(self):
        today = dt.date(2026, 1, 4)
        data = climate.daily_almanac(series(today), records(), today)
        rain = data["precipitation"]
        self.assertEqual(rain["through"], "2026-01-03")
        self.assertEqual(rain["month"]["observedIn"], .3)
        self.assertEqual(rain["month"]["normalIn"], .6)
        self.assertEqual(rain["month"]["departureIn"], -.3)
        self.assertEqual(rain["year"]["percentNormal"], 50)
        self.assertEqual(rain["day"]["observedIn"], .1)

    def test_record_ties_negative_temperature_and_current_day_exclusion(self):
        record = climate.records_for_day(records(), dt.date(2026, 1, 4))
        self.assertEqual(record["high"], {"valueF": 85, "years": [1920, 1979]})
        self.assertEqual(record["low"], {"valueF": -2, "years": [1920]})

    def test_missing_day_does_not_turn_into_zero_or_partial_total(self):
        today = dt.date(2026, 1, 4)
        raw = series(today)
        raw["data"][1][3] = ["M", " "]
        data = climate.daily_almanac(raw, records(), today)["precipitation"]["year"]
        self.assertIsNone(data["observedIn"])
        self.assertIsNone(data["departureIn"])
        self.assertEqual(data["normalIn"], .6)
        self.assertEqual(data["missingDays"], 1)

    def test_missing_latest_day_moves_both_cutoffs_back(self):
        today = dt.date(2026, 1, 4)
        raw = series(today)
        raw["data"][2][3] = ["M", " "]
        data = climate.daily_almanac(raw, records(), today)["precipitation"]
        self.assertEqual(data["through"], "2026-01-02")
        self.assertEqual(data["year"]["normalIn"], .4)
        self.assertEqual(data["year"]["observedIn"], .2)

    def test_trace_zero_and_accumulated_flags_remain_distinct(self):
        self.assertEqual(climate.climate_value(["T", " "], True), (0, True))
        self.assertEqual(climate.climate_value(["0.00", " "], True), (0, False))
        self.assertEqual(climate.climate_value(["1.00", "A"], True), (None, False))
        self.assertEqual(climate.climate_value(["T", "A"], True), (None, False))
        self.assertEqual(climate.climate_value("-0.01", True), (None, False))
        today = dt.date(2026, 1, 2)
        raw = series(today); raw["data"][0][3] = ["T", " "]
        self.assertTrue(climate.daily_almanac(raw, records(), today)["precipitation"]["year"]["trace"])

    def test_january_first_has_no_completed_current_year_totals(self):
        today = dt.date(2026, 1, 1)
        p = climate.daily_almanac(series(today), records(), today)["precipitation"]
        self.assertIsNone(p["through"])
        self.assertIsNone(p["year"]["observedIn"])
        self.assertEqual(p["year"]["days"], 0)

    def test_first_of_month_keeps_new_month_pending_and_year_current(self):
        today = dt.date(2026, 2, 1)
        p = climate.daily_almanac(series(today), records(), today)["precipitation"]
        self.assertEqual(p["through"], "2026-01-31")
        self.assertEqual(p["month"]["days"], 0)
        self.assertIsNone(p["month"]["normalIn"])
        self.assertEqual(p["year"]["days"], 31)

    def test_leap_day_records_ignore_non_leap_years(self):
        r = records(); r["data"].append(["2000", ["77"], ["4"]])
        r["data"][2] = ["1979", ["100"], ["-90"]]
        result = climate.records_for_day(r, dt.date(2028, 2, 29))
        self.assertNotIn(1979, result["high"]["years"])
        self.assertEqual(result["high"]["valueF"], 85)

    def test_bad_station_and_missing_normals_fail_without_substitution(self):
        today = dt.date(2026, 1, 4)
        raw = series(today); raw["meta"]["name"] = "Another Area"
        with self.assertRaises(ValueError): climate.daily_almanac(raw, records(), today)
        raw = series(today); raw["data"][-1][4] = "M"
        with self.assertRaises(ValueError): climate.daily_almanac(raw, records(), today)

    def test_query_parameters_live_cache_and_disk_restart(self):
        today = dt.date(2026, 1, 4)
        fetch = Mock(side_effect=[series(today), records()])
        with tempfile.TemporaryDirectory() as folder:
            desk = climate.BostonClimate(folder, fetch)
            result = desk.get(today)
            self.assertIsNone(result["error"])
            self.assertEqual(desk.get(today), result)
            self.assertEqual(fetch.call_count, 2)
            params = [json.loads(parse_qs(urlsplit(call.args[0]).query)["params"][0]) for call in fetch.call_args_list]
            self.assertEqual(params[0]["sid"], "BOSthr 9")
            self.assertEqual(params[1]["edate"], "2026-01-03")
            self.assertEqual(params[1]["elems"][0]["groupby"], ["year", "01-04", "01-04"])
            self.assertEqual(climate.BostonClimate(folder, Mock(side_effect=AssertionError("Use fresh saved data"))).get(today), result)

    def test_new_day_failure_keeps_explicit_old_date_and_retries_throttled(self):
        today = dt.date(2026, 1, 4)
        with tempfile.TemporaryDirectory() as folder:
            climate.BostonClimate(folder, Mock(side_effect=[series(today), records()])).get(today)
            fetch = Mock(side_effect=OSError("offline"))
            restarted = climate.BostonClimate(folder, fetch)
            result = restarted.get(today+dt.timedelta(days=1))
            self.assertTrue(result["stale"])
            self.assertEqual(result["date"], "2026-01-04")
            self.assertEqual(result["precipitation"]["through"], "2026-01-03")
            restarted.get(today+dt.timedelta(days=1))
            self.assertEqual(fetch.call_count, 1)

    def test_service_failure_without_cache_has_no_fabricated_numbers(self):
        with tempfile.TemporaryDirectory() as folder:
            result = climate.BostonClimate(folder, Mock(side_effect=OSError("offline"))).get(dt.date(2026, 1, 4))
        self.assertIsNone(result["records"])
        self.assertIsNone(result["precipitation"])
        self.assertTrue(result["stale"])

    def test_boston_date_windows_without_timezone_database(self):
        checks = [("2026-01-01T04:59:00+00:00", "2025-12-31"),
                  ("2026-01-01T05:00:00+00:00", "2026-01-01"),
                  ("2026-07-01T03:59:00+00:00", "2026-06-30"),
                  ("2026-07-01T04:00:00+00:00", "2026-07-01")]
        with patch.object(climate, "ZoneInfo", side_effect=ZoneInfoNotFoundError("No database")):
            for value, expected in checks:
                self.assertEqual(climate.boston_date(dt.datetime.fromisoformat(value).timestamp()).isoformat(), expected)


if __name__ == "__main__":
    unittest.main()
