"""Statistical boundaries, coverage, cache migration and actual calendar integration."""
import copy
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from temperature_calendar import TemperatureCalendar
from temperature_terciles import METHOD, TemperatureTerciles, cutoffs, quantile, temperature_class

SITE = {"id": "TEST", "sid": "TEST 6", "name": "Test reference", "available": True}
ZONE = "America/New_York"


def baseline(site=SITE, keys=("01-01", "01-02", "01-03", "01-04")):
    rows = []
    for year in range(1991, 2021):
        mean = 46 + year - 1991
        for key in keys:
            try:
                date = dt.date.fromisoformat(f"{year}-{key}")
            except ValueError:
                continue
            rows.append([date.isoformat(), [mean + 10, " "], [mean - 10, " "]])
    return {"meta": {"name": site["name"]}, "data": rows}


def month(site=SITE):
    return {"meta": {"name": site["name"]}, "data": [
        ["2026-01-01", [70, " "], [50, " "], 55],
        ["2026-01-02", [80, " "], [60, " "], 55],
        ["2026-01-03", [60, " "], [40, " "], 55],
        ["2026-01-04", ["M", " "], [40, " "], 55],
    ]}


def wait_loaded(cal):
    deadline = time.monotonic() + 3
    while any(s["loading"] for s in cal.states.values()) and time.monotonic() < deadline:
        time.sleep(.005)
    assert not any(s["loading"] for s in cal.states.values()), "calendar worker did not finish"


class StatisticalTests(unittest.TestCase):
    def test_linear_percentiles_known_example(self):
        self.assertEqual(quantile([3, 0], 1 / 3), 1)
        self.assertEqual(quantile([3, 0], 2 / 3), 2)
        self.assertEqual(quantile([5, 5, 5], 1 / 3), 5)
        for values, p in [([], .5), ([float("nan")], .5), ([True], .5), ([1, 2], 2)]:
            with self.assertRaises(ValueError): quantile(values, p)

    def test_boundaries_ties_and_exact_departure_are_independent(self):
        for mean, expected in [(0, "below"), (40, "at"), (50, "at"), (60, "at"), (61, "above")]:
            self.assertEqual(temperature_class(mean, 45, 40, 60), (expected, mean - 45))
        self.assertEqual(temperature_class(50, 45, 50, 50), ("at", 5))
        self.assertEqual(temperature_class(50, None, 40, 60), ("at", None))
        self.assertEqual(temperature_class(0, 0, 0, 0), ("at", 0))
        for value in [None, True, float("nan"), float("inf")]:
            self.assertEqual(temperature_class(value, 40, 30, 50)[0], "missing")
        self.assertEqual(temperature_class(50, 50)[0], "missing")
        self.assertEqual(temperature_class(50, 50, 60, 40)[0], "missing")

    def test_fahrenheit_celsius_conversion_does_not_change_category(self):
        values = [31 + n * 2.3 for n in range(30)]
        c = lambda f: (f - 32) * 5 / 9
        lo, hi = (quantile(values, p) for p in (1 / 3, 2 / 3))
        clo, chi = (quantile([c(v) for v in values], p) for p in (1 / 3, 2 / 3))
        for value in [lo - .01, lo, (lo + hi) / 2, hi, hi + .01]:
            self.assertEqual(temperature_class(value, 50, lo, hi)[0], temperature_class(c(value), c(50), clo, chi)[0])

    def test_coverage_requires_24_paired_years_not_24_separate_highs_and_lows(self):
        raw = baseline(keys=("01-01",))
        raw["data"] = raw["data"][:24]
        self.assertEqual(cutoffs(raw, SITE)["01-01"]["baselineYears"], 24)
        self.assertIsNotNone(cutoffs(raw, SITE)["01-01"]["lowerThirdF"])
        raw["data"][0][1] = ["M", " "]
        row = cutoffs(raw, SITE)["01-01"]
        self.assertEqual(row["baselineYears"], 23)
        self.assertIsNone(row["lowerThirdF"])

    def test_leap_day_uses_its_real_smaller_sample_without_interpolation(self):
        raw = baseline(keys=("02-29",))
        day = cutoffs(raw, SITE)["02-29"]
        self.assertEqual((day["baselineYears"], day["baselineExpectedYears"], day["baselineRequiredYears"]), (8, 8, 7))
        raw["data"].pop()
        self.assertIsNotNone(cutoffs(raw, SITE)["02-29"]["lowerThirdF"])
        raw["data"].pop()
        self.assertIsNone(cutoffs(raw, SITE)["02-29"]["lowerThirdF"])

    def test_invalid_dates_flags_reversed_pairs_and_duplicates_are_excluded(self):
        raw = baseline(keys=("01-01",))
        raw["data"][0][1] = [80, "A"]
        raw["data"][1][1:3] = [20, 40]
        raw["data"].append(copy.deepcopy(raw["data"][2]))
        raw["data"] += [["1990-01-01", 100, 90], ["2021-01-01", 100, 90], ["2000-02-30", 100, 90], [None, 3, 1]]
        row = cutoffs(raw, SITE)["01-01"]
        self.assertEqual(row["baselineYears"], 27)
        with self.assertRaisesRegex(ValueError, "different station"):
            cutoffs(raw, {**SITE, "name": "Another reference"})


class BaselineCacheTests(unittest.TestCase):
    def test_single_bulk_query_is_persisted_and_reused_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch = Mock(return_value=baseline())
            first = TemperatureTerciles(folder, fetch).get(SITE)
            query = json.loads(parse_qs(urlsplit(fetch.call_args.args[0]).query)["params"][0])
            self.assertEqual((query["sdate"], query["edate"], query["sid"]), ("1991-01-01", "2020-12-31", SITE["sid"]))
            self.assertEqual([e["name"] for e in query["elems"]], ["maxt", "mint"])
            offline = Mock(side_effect=OSError("offline"))
            second = TemperatureTerciles(folder, offline).get(SITE)
            self.assertEqual(first, second); offline.assert_not_called()

    def test_station_and_method_cannot_share_a_baseline(self):
        with tempfile.TemporaryDirectory() as folder:
            other = {**SITE, "id": "OTHER", "sid": "OTHER 6", "name": "Another reference"}
            get = Mock(side_effect=[baseline(), baseline(other)])
            store = TemperatureTerciles(folder, None, get)
            store.get(SITE); store.get(other)
            self.assertEqual(get.call_count, 2)
            files = list((Path(folder) / "temperature-calendar/terciles").glob("*.json"))
            self.assertEqual(len(files), 2)
            for path in files:
                saved = json.loads(path.read_text()); saved["method"] = "old-method"; path.write_text(json.dumps(saved))
            retry = Mock(return_value=baseline())
            TemperatureTerciles(folder, None, retry).get(SITE)
            retry.assert_called_once()

    def test_corrupt_counts_and_nonfinite_cutoffs_are_not_accepted(self):
        identity = TemperatureTerciles.identity(SITE)
        saved = {**identity, "days": cutoffs(baseline(), SITE)}
        self.assertTrue(TemperatureTerciles.valid(saved, identity))
        for key, value in [("baselineYears", True), ("baselineYears", 31), ("lowerThirdF", float("nan")), ("upperThirdF", -100)]:
            bad = copy.deepcopy(saved); bad["days"]["01-01"][key] = value
            self.assertFalse(TemperatureTerciles.valid(bad, identity))

    def test_concurrent_month_loads_share_one_baseline_request(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch = Mock(return_value=baseline())
            store = TemperatureTerciles(folder, fetch)
            with ThreadPoolExecutor(max_workers=4) as pool:
                values = list(pool.map(lambda _: store.get(SITE), range(4)))
            self.assertEqual(values[0], values[-1]); fetch.assert_called_once()

    def test_failed_baseline_is_not_cached_as_a_substitute_and_retries_are_paced(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch = Mock(side_effect=[OSError("offline"), baseline()])
            store = TemperatureTerciles(folder, fetch)
            with patch("temperature_terciles.time.monotonic", return_value=100):
                for _ in range(2):
                    with self.assertRaisesRegex(ValueError, "unavailable"): store.get(SITE)
            self.assertEqual(fetch.call_count, 1)
            self.assertFalse(list(Path(folder).rglob("*.json")))
            with patch("temperature_terciles.time.monotonic", return_value=401):
                self.assertIsNotNone(store.get(SITE)["01-01"]["lowerThirdF"])


@patch("temperature_calendar.local_date", return_value=dt.date(2026, 1, 5))
class CalendarIntegrationTests(unittest.TestCase):
    def test_three_categories_keep_exact_departure_and_pending_days(self, _):
        with tempfile.TemporaryDirectory() as folder:
            cal = TemperatureCalendar(folder, Mock(return_value=month()), Mock(return_value=baseline()))
            cal.snapshot("2026-01", SITE, ZONE); wait_loaded(cal)
            result = cal.snapshot("2026-01", SITE, ZONE)
            self.assertEqual(result["comparisonMethod"], METHOD)
            self.assertEqual([d["status"] for d in result["days"][:6]], ["at", "above", "below", "missing", "pending", "future"])
            day = result["days"][0]
            self.assertEqual((day["meanF"], day["normalF"], day["departureF"], day["baselineYears"]), (60, 55, 5, 30))
            self.assertLess(day["lowerThirdF"], day["meanF"]); self.assertGreater(day["upperThirdF"], day["meanF"])

    def test_missing_baseline_preserves_observation_and_departure_without_colour(self, _):
        with tempfile.TemporaryDirectory() as folder:
            fetch = Mock(side_effect=OSError("offline"))
            cal = TemperatureCalendar(folder, Mock(return_value=month()), fetch)
            cal.snapshot("2026-01", SITE, ZONE); wait_loaded(cal)
            result = cal.snapshot("2026-01", SITE, ZONE); day = result["days"][0]
            self.assertEqual((day["status"], day["meanF"], day["departureF"]), ("missing", 60, 5))
            self.assertIsNotNone(result["baselineError"]); self.assertIsNone(day["lowerThirdF"])

    def test_missing_normal_does_not_invent_departure_or_prevent_a_valid_tercile(self, _):
        raw = month(); raw["data"][0][3] = "M"
        with tempfile.TemporaryDirectory() as folder:
            cal = TemperatureCalendar(folder, Mock(return_value=raw), Mock(return_value=baseline()))
            cal.snapshot("2026-01", SITE, ZONE); wait_loaded(cal)
            day = cal.snapshot("2026-01", SITE, ZONE)["days"][0]
            self.assertEqual(day["status"], "at"); self.assertIsNone(day["departureF"])

    def test_restarts_use_saved_thresholds_and_observations_without_network(self, _):
        with tempfile.TemporaryDirectory() as folder:
            cal = TemperatureCalendar(folder, Mock(return_value=month()), Mock(return_value=baseline()))
            cal.snapshot("2026-01", SITE, ZONE); wait_loaded(cal)
            offline = Mock(side_effect=AssertionError("unexpected network call"))
            restarted = TemperatureCalendar(folder, offline, offline)
            restarted.snapshot("2026-01", SITE, ZONE); wait_loaded(restarted)
            result = restarted.snapshot("2026-01", SITE, ZONE)
            self.assertIsNone(result["error"]); self.assertIsNone(result["baselineError"])
            self.assertEqual(result["days"][0]["status"], "at"); offline.assert_not_called()

    def test_old_fixed_tolerance_cache_cannot_supply_test_colours(self, _):
        with tempfile.TemporaryDirectory() as folder:
            identity = hashlib.sha256("|".join(("2026-01", SITE["id"], ZONE, "")).encode()).hexdigest()[:32]
            directory = Path(folder) / "temperature-calendar"; directory.mkdir()
            old = {"month": "2026-01", "stationId": SITE["id"], "timezone": ZONE, "source": "noaa",
                   "days": [{"date": "2026-01-01", "status": "at", "meanF": 60, "normalF": 60}]}
            (directory / (identity + "-month.json")).write_text(json.dumps(old))
            cal = TemperatureCalendar(folder, Mock(side_effect=OSError("offline")))
            cal.snapshot("2026-01", SITE, ZONE); wait_loaded(cal)
            result = cal.snapshot("2026-01", SITE, ZONE)
            self.assertTrue(result["error"]); self.assertEqual(result["days"][0]["status"], "missing")

    def test_insufficient_same_date_history_is_explained_even_when_other_dates_work(self, _):
        raw = baseline(); raw["data"] = [r for r in raw["data"] if r[0][5:] != "01-01" or int(r[0][:4]) < 2010]
        with tempfile.TemporaryDirectory() as folder:
            cal = TemperatureCalendar(folder, Mock(return_value=month()), Mock(return_value=raw))
            cal.snapshot("2026-01", SITE, ZONE); wait_loaded(cal)
            day = cal.snapshot("2026-01", SITE, ZONE)["days"][0]
            self.assertEqual(day["status"], "missing"); self.assertIn("19 of 30", day["note"])


if __name__ == "__main__": unittest.main()
