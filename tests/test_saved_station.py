"""Restart/forget behavior and protection of locally remembered station keys."""
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_store
import server


class SavedStationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.desks = []
        self.desk = self.new_desk()
        self.keys = {"apiKey": "test-secret-api", "applicationKey": "test-secret-app"}
        self.device = {"macAddress": "AA:BB:CC:DD:EE:FF", "info": {"name": "Test station"},
                       "lastData": {"dateutc": int(time.time()*1000), "tempf": 55}}

    def new_desk(self):
        desk = server.Desk(self.folder.name)
        self.desks.append(desk)
        return desk

    def tearDown(self):
        for desk in self.desks:
            desk.db.close()
        self.folder.cleanup()

    def connect(self, **values):
        with patch.object(self.desk, "ambient", return_value=[self.device]):
            return self.desk.connect({**self.keys, **values})

    def test_default_remember_survives_restart_without_remote_login(self):
        self.connect()
        with patch.object(server, "fetch_json", side_effect=AssertionError("Startup must not require network")):
            restarted = self.new_desk()
        self.assertTrue(restarted.status()["connected"])
        self.assertTrue(restarted.status()["credentialsSaved"])
        self.assertEqual(restarted.mac, self.device["macAddress"])
        self.assertEqual(restarted.api_key, self.keys["apiKey"])
        self.assertEqual(restarted.cache["tempF"], 55)
        with patch.object(restarted, "ambient", side_effect=server.WeatherError("Offline")):
            reading = restarted.weather()
        self.assertFalse(reading["demo"])
        self.assertTrue(reading["stale"])
        self.assertEqual(reading["current"]["tempF"], 55)

    def test_saved_keys_do_not_escape_status_preferences_or_observations(self):
        self.connect()
        self.desk.settings_update({"title": "Saved station test"})
        public = json.dumps([self.desk.status(), self.desk.weather(), self.desk.saved_history()])
        preferences = (Path(self.folder.name)/"preferences.json").read_text()
        for secret in self.keys.values():
            self.assertNotIn(secret, public)
            self.assertNotIn(secret, preferences)

    def test_forget_removes_disk_copy_and_next_start_is_demo(self):
        self.connect()
        self.desk.disconnect()
        self.assertFalse((Path(self.folder.name)/"station.json").exists())
        self.assertFalse(self.new_desk().status()["connected"])

    def test_session_only_removes_previously_remembered_station(self):
        self.connect()
        self.connect(remember=False)
        self.assertTrue(self.desk.status()["connected"])
        self.assertFalse(self.desk.status()["credentialsSaved"])
        self.assertFalse(self.new_desk().status()["connected"])

    def test_connected_station_can_be_saved_without_returning_keys_to_browser(self):
        self.connect(remember=False)
        with patch.object(self.desk, "ambient", return_value=[self.device]):
            self.desk.connect({"apiKey": "", "applicationKey": "", "remember": True})
        self.assertTrue(self.new_desk().status()["connected"])

    def test_failed_validation_or_disk_write_keeps_previous_connection(self):
        self.connect()
        before = (Path(self.folder.name)/"station.json").read_bytes()
        with patch.object(self.desk, "ambient", side_effect=server.WeatherError("Rejected")):
            with self.assertRaises(server.WeatherError):
                self.desk.connect({"apiKey": "new-api-key", "applicationKey": "new-app-key"})
        with patch.object(self.desk, "ambient", return_value=[self.device]), patch.object(self.desk.station_store, "save", side_effect=local_store.SavedStationError("Cannot save")):
            with self.assertRaises(server.WeatherError):
                self.desk.connect({"apiKey": "new-api-key", "applicationKey": "new-app-key"})
        self.assertEqual((Path(self.folder.name)/"station.json").read_bytes(), before)
        self.assertEqual(self.desk.api_key, self.keys["apiKey"])

    def test_corrupt_saved_file_is_reported_without_blocking_startup(self):
        (Path(self.folder.name)/"station.json").write_text('{broken')
        restarted = self.new_desk()
        self.assertFalse(restarted.status()["connected"])
        self.assertIn("Reconnect", restarted.status()["credentialNotice"])

    @unittest.skipIf(os.name == "nt", "Unix permission check")
    def test_unix_private_credentials_have_owner_only_permissions(self):
        self.connect()
        self.assertEqual((Path(self.folder.name)/"station.json").stat().st_mode & 0o777, 0o600)

    def test_windows_store_encrypts_and_decrypts_without_plaintext_fallback(self):
        store = self.desk.station_store
        cipher = b"opaque-windows-ciphertext"
        with patch.object(local_store, "WINDOWS", True), patch.object(local_store, "windows_protect", return_value=cipher) as protect:
            store.save(self.keys)
            encoded = store.path.read_text()
            for secret in self.keys.values():
                self.assertNotIn(secret, encoded)
            self.assertEqual(json.loads(encoded)["protection"], "windows-dpapi")
            self.assertEqual(base64.b64decode(json.loads(encoded)["data"]), cipher)
            protect.assert_called_once()
        with patch.object(local_store, "WINDOWS", True), patch.object(local_store, "windows_protect", return_value=json.dumps(self.keys).encode()) as unlock:
            self.assertEqual(store.load(), self.keys)
            unlock.assert_called_once_with(cipher, decrypt=True)
        before = store.path.read_bytes()
        with patch.object(local_store, "WINDOWS", True), patch.object(local_store, "windows_protect", side_effect=local_store.SavedStationError("Locked")):
            with self.assertRaises(local_store.SavedStationError):
                store.save(self.keys)
        self.assertEqual(store.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
