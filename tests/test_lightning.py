import base64
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lightning import (
    LightningDesk,
    LightningError,
    parse_noaa,
    parse_eumetsat,
    select_window,
    granule,
    SafeRedirect,
    HDF_SIGNATURE,
)

NOW = dt.datetime(2026, 10, 25, 22, 30, tzinfo=dt.timezone.utc).timestamp()
KEY, SECRET = "synthetic-consumer-key", "synthetic-consumer-secret"
POINT = {"latitude": 42.4668, "longitude": -70.9495}


def noaa_name(start, satellite="19", suffix="0"):
    stamp = lambda t: dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y%j%H%M%S") + suffix
    prefix = dt.datetime.fromtimestamp(start, dt.timezone.utc).strftime("GLM-L2-LCFA/%Y/%j/%H/")
    return (
        prefix
        + f"OR_GLM-L2-LCFA_G{satellite}_s{stamp(start)}_e{stamp(start+20)}_c{stamp(start+22)}.nc"
    )


def noaa_xml(starts=None, satellite="19"):
    starts = [NOW - 40, NOW - 20] if starts is None else starts
    return (
        "<ListBucketResult><IsTruncated>false</IsTruncated>"
        + "".join("<Contents><Key>" + noaa_name(t, satellite) + "</Key></Contents>" for t in starts)
        + "</ListBucketResult>"
    ).encode()


def eum_response():
    identifier = "W_XX-EUMETSAT-Darmstadt,IMG+SAT,MTI1+LI-2-LFL--FD--x-x--ARC-x_C_EUMT_20261025222022_L2PF_OPE_20261025221009_20261025222009_N__O_0134_0000"
    body = identifier.replace("x-x--ARC-x", "CHK-BODY--ARC-NC4E") + ".nc"
    return {
        "totalResults": 1,
        "features": [
            {
                "properties": {
                    "identifier": identifier,
                    "parentIdentifier": "EO:EUM:DAT:0691",
                    "date": "2026-10-25T22:10:09Z/2026-10-25T22:20:09Z",
                    "links": {
                        "sip-entries": [
                            {"title": body, "href": "https://untrusted.invalid/not-used"}
                        ]
                    },
                }
            }
        ],
    }


class CatalogueTests(unittest.TestCase):
    def test_noaa_uses_only_expected_satellite_and_keys(self):
        rows = parse_noaa(noaa_xml(), "G19", NOW)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["end"], NOW)
        self.assertTrue(
            rows[0]["url"].startswith("https://noaa-goes19.s3.amazonaws.com/GLM-L2-LCFA/")
        )
        self.assertEqual(parse_noaa(noaa_xml(), "G18", NOW), [])

    def test_truncated_or_unreadable_catalogue_is_not_an_empty_success(self):
        for raw in (
            b"bad",
            b"<ListBucketResult><IsTruncated>true</IsTruncated></ListBucketResult>",
        ):
            with self.subTest(raw=raw), self.assertRaises(LightningError):
                parse_noaa(raw, "G19", NOW)
        with self.assertRaises(LightningError):
            select_window([])

    def test_window_is_complete_only_with_every_interval(self):
        rows = parse_noaa(noaa_xml([NOW - 2700 + i * 20 for i in range(135)]), "G19", NOW)
        window = select_window(rows)
        self.assertTrue(window["coverageComplete"])
        self.assertEqual(window["windowStart"], NOW - 2700)
        self.assertEqual(window["windowEnd"], NOW)
        partial = select_window(rows[:40] + rows[41:])
        self.assertFalse(partial["coverageComplete"])
        self.assertEqual(len(partial["gaps"]), 1)

    def test_short_startup_history_does_not_claim_full_window(self):
        window = select_window(parse_noaa(noaa_xml(), "G19", NOW))
        self.assertFalse(window["coverageComplete"])
        self.assertEqual(window["gaps"][0], [NOW - 2700, NOW - 40])

    def test_reprocessed_intervals_do_not_count_twice(self):
        rows = parse_noaa(noaa_xml(), "G19", NOW)
        self.assertEqual(len(select_window(rows + rows)["files"]), 2)

    def test_observation_window_ends_at_satellite_time(self):
        window = select_window(parse_noaa(noaa_xml([NOW - 600]), "G19", NOW))
        self.assertEqual(window["windowEnd"], NOW - 580)
        self.assertEqual(window["windowStart"], NOW - 3280)

    def test_noaa_new_year_prefix_and_times(self):
        now = dt.datetime(2027, 1, 1, 0, 0, tzinfo=dt.timezone.utc).timestamp()
        rows = parse_noaa(noaa_xml([now - 20]), "G19", now)
        self.assertEqual(rows[0]["start"], now - 20)
        self.assertEqual(rows[0]["end"], now)

    def test_eum_selects_body_and_constructs_official_entry_url(self):
        row = parse_eumetsat(eum_response(), NOW)[0]
        self.assertEqual(row["end"] - row["start"], 600)
        self.assertIn("/EO%3AEUM%3ADAT%3A0691/products/", row["url"])
        self.assertIn("/entry?name=", row["url"])
        self.assertNotIn("untrusted", row["url"])
        self.assertIn("CHK-BODY", row["name"])

    def test_eum_missing_duplicate_body_wrong_collection_and_truncated_results(self):
        for change in ("missing", "duplicate", "collection"):
            payload = eum_response()
            props = payload["features"][0]["properties"]
            if change == "missing":
                props["links"]["sip-entries"] = []
            if change == "duplicate":
                props["links"]["sip-entries"] *= 2
            if change == "collection":
                props["parentIdentifier"] = "different"
            self.assertEqual(parse_eumetsat(payload, NOW), [])
        with self.assertRaises(LightningError):
            parse_eumetsat({"features": [], "totalResults": 4}, NOW)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = patch("lightning.time.time", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def uk(self, fetch=None):
        return LightningDesk(self.temp.name, "UK", fetch or Mock())

    def connect(self, desk, remember=False):
        return desk.update(
            {
                "action": "connect",
                "consumerKey": KEY,
                "consumerSecret": SECRET,
                "remember": remember,
            }
        )

    def state(self, desk, point=POINT):
        with patch("lightning.threading.Thread"):
            return desk.snapshot(point)

    def test_us_needs_no_account_and_does_no_foreground_network(self):
        fetch = Mock()
        desk = LightningDesk(self.temp.name, fetch=fetch)
        value = self.state(desk)
        self.assertTrue(value["configured"])
        self.assertFalse(value["requiresAccount"])
        self.assertEqual(value["status"], "loading")
        fetch.assert_not_called()
        with self.assertRaises(LightningError):
            desk.update({"action": "connect"})

    def test_uk_without_account_is_map_only_and_does_not_download(self):
        fetch = Mock()
        desk = self.uk(fetch)
        value = desk.snapshot(POINT)
        self.assertEqual(value["status"], "unconfigured")
        fetch.assert_not_called()

    def test_east_west_and_unsupported_coverage_are_explicit(self):
        desk = LightningDesk(self.temp.name)
        self.assertIn("GOES-19", self.state(desk)["source"])
        self.assertIn("GOES-18", self.state(desk, {"latitude": 35, "longitude": -120})["source"])
        self.assertEqual(
            desk.snapshot({"latitude": 65, "longitude": -145})["status"], "unsupported"
        )

    def test_noaa_refresh_redacts_upstream_urls_and_reuses_catalogue(self):
        fetch = Mock(return_value=noaa_xml())
        desk = LightningDesk(self.temp.name, fetch=fetch)
        self.state(desk)
        desk._refresh("G19", 0)
        value = desk.snapshot(POINT)
        self.assertEqual(value["status"], "ready")
        self.assertNotIn("url", value["files"][0])
        self.assertEqual(fetch.call_count, 2)
        desk.snapshot(POINT)
        self.assertEqual(fetch.call_count, 2)

    def test_failed_refresh_retains_window_and_original_times(self):
        fetch = Mock(return_value=noaa_xml())
        desk = LightningDesk(self.temp.name, fetch=fetch)
        self.state(desk)
        desk._refresh("G19", 0)
        before = desk.snapshot(POINT)
        fetch.side_effect = OSError("private upstream URL " + SECRET)
        desk._refresh("G19", 0)
        after = desk.snapshot(POINT)
        self.assertTrue(after["stale"])
        self.assertEqual(before["windowEnd"], after["windowEnd"])
        self.assertNotIn(SECRET, json.dumps(after))

    def test_saved_credentials_and_session_only_replacement(self):
        desk = self.uk()
        self.connect(desk, True)
        self.assertTrue(self.uk().connection()["saved"])
        self.assertEqual(self.uk().credentials, (KEY, SECRET))
        self.connect(desk, False)
        self.assertIsNone(self.uk().credentials)
        self.assertTrue(desk.connection()["configured"])

    def test_forget_clears_credentials_token_and_catalogue(self):
        desk = self.uk()
        self.connect(desk, True)
        desk.token = "oldtoken"
        desk.states["EUM"] = {}
        desk.update({"action": "forget"})
        self.assertFalse(desk.connection()["configured"])
        self.assertEqual(desk.token, "")
        self.assertEqual(desk.states, {})
        self.assertFalse((Path(self.temp.name) / "eumetsat-key.json").exists())

    def test_credentials_never_appear_in_connection_or_public_catalogue(self):
        desk = self.uk()
        self.connect(desk)
        value = self.state(desk)
        self.assertNotIn(KEY, json.dumps(value))
        self.assertNotIn(SECRET, json.dumps(value))

    def test_token_is_authenticated_server_side_cached_and_renewed(self):
        fetch = Mock(
            return_value=json.dumps(
                {"access_token": "synthetic-bearer-token", "expires_in": 3600}
            ).encode()
        )
        desk = self.uk(fetch)
        self.connect(desk)
        token = desk._bearer(desk.generation)
        self.assertEqual(token, "synthetic-bearer-token")
        desk._bearer(desk.generation)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(
            fetch.call_args.kwargs["headers"]["Authorization"],
            "Basic " + base64.b64encode((KEY + ":" + SECRET).encode()).decode(),
        )
        desk.token_until = NOW + 30
        desk._bearer(desk.generation)
        self.assertEqual(fetch.call_count, 2)

    def test_invalid_credentials_do_not_replace_connection(self):
        desk = self.uk()
        self.connect(desk)
        for values in (
            {"consumerKey": "x", "consumerSecret": SECRET},
            {"consumerKey": KEY, "consumerSecret": SECRET, "remember": "yes"},
        ):
            with self.assertRaises(LightningError):
                desk.update({"action": "connect", **values})
        self.assertEqual(desk.credentials, (KEY, SECRET))

    def test_auth_error_does_not_expose_credentials_or_exception_url(self):
        fetch = Mock(
            side_effect=HTTPError(
                "https://example.invalid/?secret=" + SECRET, 401, "private", {}, None
            )
        )
        desk = self.uk(fetch)
        self.connect(desk)
        self.state(desk)
        desk._refresh("EUM", desk.generation)
        value = desk.snapshot(POINT)
        self.assertEqual(value["status"], "error")
        self.assertIn("consumer key", value["error"])
        self.assertNotIn(SECRET, json.dumps(value))

    def test_stale_inflight_generation_cannot_restore_forgotten_catalogue(self):
        desk = self.uk()
        self.connect(desk)
        old = desk.generation
        desk.update({"action": "forget"})
        desk._refresh("EUM", old)
        self.assertEqual(desk.states, {})

    def test_file_ids_are_registered_not_arbitrary_paths(self):
        desk = LightningDesk(self.temp.name)
        for fid in ("../station.json", "a" * 32, "https://example.invalid"):
            with self.assertRaises(LightningError):
                desk.file(fid)

    def test_file_cache_reuses_download_after_restart(self):
        raw = HDF_SIGNATURE + b"synthetic NetCDF body"
        fetch = Mock(return_value=raw)
        desk = LightningDesk(self.temp.name, fetch=fetch)
        row = granule("https://noaa-goes19.s3.amazonaws.com/test.nc", NOW - 20, NOW, "noaa", "test")
        desk.file_map[row["id"]] = row
        self.assertEqual(desk.file(row["id"]), raw)
        self.assertEqual(desk.file(row["id"]), raw)
        self.assertEqual(fetch.call_count, 1)
        reopened = LightningDesk(self.temp.name, fetch=fetch)
        reopened.file_map[row["id"]] = row
        self.assertEqual(reopened.file(row["id"]), raw)
        self.assertEqual(fetch.call_count, 1)

    def test_non_hdf_download_does_not_enter_cache(self):
        desk = LightningDesk(self.temp.name, fetch=Mock(return_value=b"<html>error</html>"))
        row = granule("https://noaa-goes19.s3.amazonaws.com/test.nc", NOW - 20, NOW, "noaa", "test")
        desk.file_map[row["id"]] = row
        with self.assertRaises(LightningError):
            desk.file(row["id"])
        self.assertEqual(list(desk.directory.glob("*.nc")), [])

    def test_bearer_is_removed_on_cross_host_redirect(self):
        req = Request("https://api.eumetsat.int/data", headers={"Authorization": "Bearer private"})
        redir = SafeRedirect().redirect_request(
            req, None, 302, "", {}, "https://storage.eumetsat.int/file"
        )
        self.assertIsNone(redir.get_header("Authorization"))
        with self.assertRaises(LightningError):
            SafeRedirect().redirect_request(
                req, None, 302, "", {}, "https://untrusted.invalid/file"
            )


if __name__ == "__main__":
    unittest.main()
