"""Start Weather Desk with bundled or installed Python, without site-packages."""

from __future__ import annotations

import os
import sys

# Embeddable/isolated Python need not add the script directory to sys.path.
# Establish our own import root before importing any application modules.
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

PINNED = (3, 14, 9)
_DLL_DIRECTORY = None  # Keep Windows' directory handle alive for this process.


def configure_runtime(installed=False):
    global _DLL_DIRECTORY
    if sys.version_info[:2] < (3, 10):
        raise RuntimeError("Weather Desk requires Python 3.10 or newer.")
    if sys.platform != "win32":
        return "installed" if installed else "host"
    if installed:
        return "installed"

    runtime = os.path.join(ROOT, "runtime", "windows-x64")
    expected = os.path.join(runtime, "python.exe")
    if os.path.normcase(os.path.realpath(sys.executable)) != os.path.normcase(
        os.path.realpath(expected)
    ):
        raise RuntimeError("Use the supplied START launcher, or START-WITH-INSTALLED-PYTHON.bat.")
    if sys.version_info[:3] != PINNED or sys.maxsize <= 2**32:
        raise RuntimeError("This download requires its bundled Python 3.14.9 for Windows x64.")
    if not sys.flags.isolated or not sys.flags.utf8_mode:
        raise RuntimeError("Use the supplied launcher for isolated paths and UTF-8 text.")
    if _DLL_DIRECTORY is None:
        try:
            _DLL_DIRECTORY = os.add_dll_directory(runtime)
        except (AttributeError, OSError) as exc:
            raise RuntimeError(
                "Could not register the bundled DLL directory: " + str(exc)
            ) from None
    return "bundled"


def load_app(installed=False):
    # The Windows batch launcher already verifies and prepares all runtime files.
    # Register its DLL directory before server imports SSL, SQLite or ctypes.
    mode = configure_runtime(installed)
    if not os.path.isfile(os.path.join(ROOT, "static", "index.html")):
        raise RuntimeError(
            "The dashboard files are incomplete. Extract the complete Weather Desk ZIP."
        )
    import importlib

    try:
        app = importlib.import_module("server")
    except (ImportError, OSError) as exc:
        raise RuntimeError("Could not load the dashboard: " + str(exc)) from None
    return app, mode


def diagnose(app, mode):
    """Exercise TLS and SQLite on request, without creating private app data."""
    import sqlite3
    import ssl

    ssl.create_default_context()
    with sqlite3.connect(":memory:") as db:
        if db.execute("SELECT 1").fetchone() != (1,):
            raise RuntimeError("SQLite diagnostic failed.")
    return {
        "build": app.BUILD,
        "runtime": mode,
        "python": ".".join(map(str, sys.version_info[:3])),
        "executable": sys.executable,
        "application": ROOT,
        "platform": sys.platform,
        "bits": 64 if sys.maxsize > 2**32 else 32,
        "isolated": bool(sys.flags.isolated),
        "utf8": bool(sys.flags.utf8_mode),
        "dllDirectoryConfigured": _DLL_DIRECTORY is not None,
        "sqlite": sqlite3.sqlite_version,
        "tls": ssl.OPENSSL_VERSION,
    }


def main():
    try:
        app, mode = load_app(installed="--installed-python" in sys.argv)
        if "--diagnose" in sys.argv:
            import json

            print(json.dumps(diagnose(app, mode), indent=2))
            return 0
        print(
            "Weather Desk runtime ready: "
            + mode
            + " Python "
            + ".".join(map(str, sys.version_info[:3])),
            flush=True,
        )
        sys.argv = [arg for arg in sys.argv if arg != "--installed-python"]
        return app.main() or 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print("Weather Desk could not start: " + str(exc), file=sys.stderr)
        print(
            "Run CHECK-WEATHER-DESK-RUNTIME.bat for details, or use START-WITH-INSTALLED-PYTHON.bat.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
