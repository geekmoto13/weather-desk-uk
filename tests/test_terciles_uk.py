"""UK and Irish references must stay with their own archive and paired days."""
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from temperature_terciles import TemperatureTerciles
from uk_climate import UKArchive, STATIONS, profile


class LocalArchiveTercileTests(unittest.TestCase):
    def test_uk_and_irish_baselines_never_query_us_acis(self):
        uk = next(sid for sid, s in STATIONS.items() if s.get("country") != "IE")
        irish = next(sid for sid, s in STATIONS.items() if s.get("country") == "IE")
        for sid in [uk, irish]:
            with self.subTest(station=sid), tempfile.TemporaryDirectory() as folder:
                archive = UKArchive(folder)
                days = {dt.date(y, 1, 1): {"TMAX": 60 + y - 1991, "TMIN": 40 + y - 1991} for y in range(1991, 2021)}
                days[dt.date(1990, 1, 1)] = {"TMAX": 150, "TMIN": 140}
                days[dt.date(2021, 1, 1)] = {"TMAX": 150, "TMIN": 140}
                archive.load = Mock(return_value={"days": days})
                forbidden = Mock(side_effect=AssertionError("UK/Irish data must not call ACIS"))
                store = TemperatureTerciles(folder, forbidden, archive.temperature_baseline)
                band = store.get(profile(sid))["01-01"]
                self.assertEqual(band["baselineYears"], 30)
                self.assertAlmostEqual(band["lowerThirdF"], 50 + 29 / 3)
                self.assertAlmostEqual(band["upperThirdF"], 50 + 58 / 3)
                forbidden.assert_not_called(); archive.load.assert_called_once_with(sid)

    def test_only_paired_archive_days_count_toward_coverage(self):
        sid = next(iter(STATIONS))
        with tempfile.TemporaryDirectory() as folder:
            archive = UKArchive(folder)
            days = {dt.date(y, 1, 1): {"TMAX": 60, "TMIN": 40} for y in range(1991, 2021)}
            for y in range(1991, 1998): days[dt.date(y, 1, 1)].pop("TMIN")
            archive.load = Mock(return_value={"days": days})
            store = TemperatureTerciles(folder, None, archive.temperature_baseline)
            with self.assertRaisesRegex(ValueError, "Not enough paired"):
                store.get(profile(sid))


if __name__ == "__main__": unittest.main()
