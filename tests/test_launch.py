"""Startup regressions, including an isolated interpreter and unrelated cwd."""
import contextlib
import hashlib
import importlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import launch_weather


class LauncherTests(unittest.TestCase):
    def test_isolated_start_finds_app_from_unrelated_directory(self):
        with tempfile.TemporaryDirectory(prefix="Weather Desk (test) ! '") as folder:
            # Neither cwd nor PYTHONPATH may replace the actual application.
            Path(folder, "server.py").write_text("raise RuntimeError('WRONG APPLICATION')\n")
            env = {**os.environ, "PYTHONPATH": folder}
            result = subprocess.run(
                [sys.executable, "-I", "-X", "utf8", "-B", str(ROOT / "launch_weather.py"),
                 "--installed-python", "--diagnose"],
                cwd=folder, env=env, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["application"], str(ROOT))
        self.assertTrue(value["isolated"])
        self.assertTrue(value["utf8"])
        self.assertEqual(value["runtime"], "installed")
        self.assertIn("OpenSSL", value["tls"])

    def test_bootstrap_does_not_import_native_extensions(self):
        code = (
            "import sys,runpy;sys.modules['ssl']=None;sys.modules['sqlite3']=None;"
            "runpy.run_path(sys.argv[1],run_name='bootstrap_probe');print('loaded')"
        )
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [sys.executable, "-I", "-B", "-c", code, str(ROOT / "launch_weather.py")],
                cwd=folder, capture_output=True, text=True, timeout=30,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "loaded")

    def test_installed_mode_rejects_old_python(self):
        with patch.object(launch_weather.sys, "version_info", (3, 9, 10)):
            with self.assertRaisesRegex(RuntimeError, "3.10 or newer"):
                launch_weather.configure_runtime(installed=True)

    def test_installed_windows_mode_uses_its_own_dlls(self):
        with patch.object(launch_weather.sys, "platform", "win32"), \
                patch.object(launch_weather.os, "add_dll_directory", create=True) as add:
            self.assertEqual(launch_weather.configure_runtime(installed=True), "installed")
        add.assert_not_called()

    def windows(self, executable=None, version=(3, 14, 9), isolated=1, utf8=1):
        stack = contextlib.ExitStack()
        stack.enter_context(patch.object(launch_weather.sys, "platform", "win32"))
        stack.enter_context(patch.object(launch_weather.sys, "executable", executable or str(ROOT / "runtime/windows-x64/python.exe")))
        stack.enter_context(patch.object(launch_weather.sys, "version_info", version))
        stack.enter_context(patch.object(launch_weather.sys, "flags", SimpleNamespace(isolated=isolated, utf8_mode=utf8)))
        stack.enter_context(patch.object(launch_weather, "_DLL_DIRECTORY", None))
        return stack

    def test_bundled_mode_rejects_another_executable(self):
        with self.windows(executable=str(ROOT / "another-python.exe")):
            with self.assertRaisesRegex(RuntimeError, "supplied START launcher"):
                launch_weather.configure_runtime()

    def test_bundled_mode_requires_pinned_version(self):
        with self.windows(version=(3, 14, 8)):
            with self.assertRaisesRegex(RuntimeError, "3.14.9"):
                launch_weather.configure_runtime()

    def test_bundled_mode_requires_isolated_utf8_flags(self):
        for isolated, utf8 in [(0, 1), (1, 0)]:
            with self.subTest(isolated=isolated, utf8=utf8), self.windows(isolated=isolated, utf8=utf8):
                with self.assertRaisesRegex(RuntimeError, "supplied launcher"):
                    launch_weather.configure_runtime()

    def test_dll_registration_precedes_server_import_and_handle_survives(self):
        events = []
        handle = Mock()
        def register(directory):
            events.append("dll")
            self.assertEqual(directory, str(ROOT / "runtime/windows-x64"))
            return handle
        def load(name):
            events.append(name)
            return Mock()
        with self.windows(), patch.object(launch_weather.os, "add_dll_directory", side_effect=register, create=True), \
                patch.object(importlib, "import_module", side_effect=load):
            _, mode = launch_weather.load_app()
            self.assertEqual(mode, "bundled")
            launch_weather.configure_runtime()
            self.assertIs(launch_weather._DLL_DIRECTORY, handle)
        self.assertEqual(events, ["dll", "server"])
        handle.close.assert_not_called()

    def test_dll_registration_failure_is_named(self):
        with self.windows(), patch.object(launch_weather.os, "add_dll_directory", side_effect=OSError("access denied"), create=True):
            with self.assertRaisesRegex(RuntimeError, "DLL directory: access denied"):
                launch_weather.configure_runtime()

    def test_missing_web_files_are_reported(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(launch_weather, "ROOT", folder):
            with self.assertRaisesRegex(RuntimeError, "dashboard files are incomplete"):
                launch_weather.load_app(installed=True)

    def test_native_import_error_is_readable_and_stops_startup(self):
        output = io.StringIO()
        with patch.object(importlib, "import_module", side_effect=ImportError("DLL load failed while importing _ssl")), \
                patch.object(launch_weather.sys, "argv", ["launch_weather.py", "--installed-python"]), \
                contextlib.redirect_stderr(output):
            self.assertEqual(launch_weather.main(), 1)
        self.assertIn("Could not load the dashboard", output.getvalue())
        self.assertIn("_ssl", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_normal_start_strips_launcher_flag_and_keeps_server_exit_status(self):
        for status in (0, 1):
            app = Mock()
            app.main.return_value = status
            with self.subTest(status=status), patch.object(launch_weather, "load_app", return_value=(app, "installed")), \
                    patch.object(launch_weather.sys, "argv", ["launch_weather.py", "--installed-python", "--port", "8765"]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(launch_weather.main(), status)
                self.assertEqual(launch_weather.sys.argv, ["launch_weather.py", "--port", "8765"])
                app.main.assert_called_once_with()

    def test_runtime_archive_matches_file_and_archive_checksums(self):
        path = ROOT / "runtime/python-windows-x64.zip"
        factory = json.loads((ROOT / "RUNTIME-ARCHIVE.json").read_text())
        manifest = json.loads((ROOT / "RUNTIME-CHECKSUMS.json").read_text())["files"]
        self.assertEqual(path.stat().st_size, factory["bytes"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), factory["sha256"])
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(set(archive.namelist()), set(manifest))
            self.assertEqual(len(manifest), 39)
            for name, expected in manifest.items():
                data = archive.read(name)
                self.assertEqual(len(data), expected["bytes"], name)
                self.assertEqual(hashlib.sha256(data).hexdigest(), expected["sha256"], name)
            # App paths now belong to the launcher, not Python's runtime config.
            self.assertNotIn(b"../..", archive.read("python314._pth"))

    def test_windows_shortcuts_use_one_startup_implementation(self):
        common = (ROOT / "weather-desk.bat").read_text()
        self.assertEqual(common.count("powershell.exe -NoLogo"), 1)
        self.assertLess(common.index("powershell.exe -NoLogo"), common.index('"%weather_runtime%\\python.exe" -I'))
        self.assertIn("if errorlevel 1 goto preparation_failed", common)
        self.assertNotIn("-ExecutionPolicy", common)
        for name, argument in [
            (next(ROOT.glob("START-WEATHER-DESK*.bat")).name, "--open"),
            ("CHECK-WEATHER-DESK-RUNTIME.bat", "--diagnose"),
            ("START-WITH-INSTALLED-PYTHON.bat", "--installed-python --open"),
        ]:
            text = (ROOT / name).read_text()
            self.assertIn('call "%~dp0weather-desk.bat" ' + argument, text)
            self.assertIn("DisableDelayedExpansion", text)
            self.assertNotIn("powershell.exe -Command", text)


if __name__ == "__main__":
    unittest.main()
