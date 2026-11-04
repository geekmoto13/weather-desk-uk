"""Private local JSON and optional Windows account-bound credential protection."""

from __future__ import annotations

import base64
import ctypes
import json
import os
from pathlib import Path
import tempfile

WINDOWS = os.name == "nt"


class SavedStationError(Exception):
    pass


def write_private_json(path, value):
    """Install complete JSON atomically; never leave a half-written credential file."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".weather-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True, allow_nan=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def windows_protect(data, decrypt=False):
    """DPAPI current-user scope, with no UI and no machine-wide fallback."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source, result = Blob(len(data), source_buffer), Blob()
    pointer = ctypes.POINTER(Blob)
    if decrypt:
        operation = crypt.CryptUnprotectData
        operation.argtypes = [
            pointer,
            ctypes.c_void_p,
            pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            pointer,
        ]
        second = None
    else:
        operation = crypt.CryptProtectData
        operation.argtypes = [
            pointer,
            wintypes.LPCWSTR,
            pointer,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            pointer,
        ]
        second = "Shannon's Weather Desk station"
    operation.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if not operation(ctypes.byref(source), second, None, None, None, 1, ctypes.byref(result)):
        raise SavedStationError("Windows could not unlock or protect the saved station.")
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        if result.data:
            kernel.LocalFree(result.data)


class StationStore:
    def __init__(self, directory):
        self.path = Path(directory) / "station.json"

    @property
    def protection(self):
        return "windows-dpapi" if WINDOWS else "private-file"

    def save(self, station):
        try:
            raw = json.dumps(station, allow_nan=False).encode("utf-8")
            if WINDOWS:
                value = {
                    "version": 1,
                    "protection": "windows-dpapi",
                    "data": base64.b64encode(windows_protect(raw)).decode("ascii"),
                }
            else:
                value = {"version": 1, "protection": "private-file", "station": station}
            write_private_json(self.path, value)
        except (OSError, ValueError, SavedStationError) as exc:
            raise SavedStationError(
                "The station could not be saved. Uncheck Remember to connect for this session, or check the dashboard folder's write permission."
            ) from exc

    def load(self):
        if not self.path.exists():
            return None
        try:
            if self.path.stat().st_size > 16000:
                raise ValueError("Invalid saved station size")
            value = json.loads(self.path.read_text("utf-8"))
            if value.get("version") != 1:
                raise ValueError("Unknown saved station version")
            if value.get("protection") == "windows-dpapi":
                if not WINDOWS:
                    raise SavedStationError(
                        "Saved Windows station belongs to its original Windows account."
                    )
                station = json.loads(
                    windows_protect(base64.b64decode(value["data"], validate=True), decrypt=True)
                )
            elif value.get("protection") == "private-file" and not WINDOWS:
                os.chmod(self.path, 0o600)
                station = value["station"]
            else:
                raise ValueError("Unsupported station protection")
            if not isinstance(station, dict):
                raise ValueError("Invalid station")
            return station
        except (OSError, ValueError, KeyError, TypeError, AttributeError, SavedStationError) as exc:
            raise SavedStationError(
                "The saved station could not be opened. Reconnect once in Settings to replace it, or use Forget station."
            ) from exc

    def forget(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError as exc:
            raise SavedStationError(
                "The saved station could not be removed. Check the dashboard folder's write permission and retry."
            ) from exc
