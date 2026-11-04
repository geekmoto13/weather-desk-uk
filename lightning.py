"""Free NOAA GLM / EUMETSAT LI discovery and bounded local NetCDF cache.

The bundled browser worker decodes HDF5, keeping Python standard-library only.
Consumer credentials and OAuth tokens never leave the local server.
"""

from __future__ import annotations

import base64
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

from local_store import SavedStationError, StationStore

UTC = dt.timezone.utc
COLLECTION = "EO:EUM:DAT:0691"
API_ROOT = (
    "https://api.eumetsat.int/data/download/1.0.0/collections/"
    + quote(COLLECTION, safe="")
    + "/products/"
)
MAX_FILE_BYTES = 32 * 1024 * 1024
HDF_SIGNATURE = b"\x89HDF\r\n\x1a\n"


class LightningError(ValueError):
    pass


def finite(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (float, int)):
        return None
    return float(value) if math.isfinite(value) and low <= value <= high else None


def coordinates(settings):
    lat, lon = finite(settings.get("latitude"), -90, 90), finite(
        settings.get("longitude"), -180, 180
    )
    if lat is None or lon is None:
        raise LightningError("Choose a valid location for lightning observations.")
    return round(lat, 4), round(lon, 4)


def iso_time(value):
    try:
        stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("Missing UTC offset")
        return stamp.timestamp()
    except (ValueError, AttributeError, OverflowError, OSError) as exc:
        raise LightningError("The satellite catalogue has an unreadable observation time.") from exc


def noaa_time(value):
    try:
        return (
            dt.datetime.strptime(value[:13], "%Y%j%H%M%S").replace(tzinfo=UTC).timestamp()
            + int(value[13]) / 10
        )
    except (ValueError, IndexError) as exc:
        raise LightningError("NOAA returned an unreadable granule time.") from exc


def granule(url, start, end, provider, name):
    return {
        "id": hashlib.sha256(url.encode()).hexdigest()[:32],
        "url": url,
        "start": start,
        "end": end,
        "provider": provider,
        "name": name,
    }


def parse_noaa(raw, satellite, now):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise LightningError("NOAA's lightning catalogue is unreadable.") from exc
    if root.findtext("{*}IsTruncated", "false").lower() != "false":
        raise LightningError("NOAA's lightning catalogue is incomplete.")
    rows = []
    for node in root.findall("{*}Contents"):
        key = node.findtext("{*}Key", "")
        match = re.fullmatch(
            r"GLM-L2-LCFA/\d{4}/\d{3}/\d{2}/OR_GLM-L2-LCFA_G(18|19)_s(\d{14})_e(\d{14})_c(\d{14})\.nc",
            key,
        )
        if not match or "G" + match[1] != satellite:
            continue
        start, end = noaa_time(match[2]), noaa_time(match[3])
        if not now - 10800 <= start < end <= now + 120 or not 18 <= end - start <= 22:
            continue
        rows.append(
            granule(
                f"https://noaa-goes{match[1]}.s3.amazonaws.com/" + key,
                start,
                end,
                "noaa",
                key.rsplit("/", 1)[1],
            )
        )
    return rows


def parse_eumetsat(payload, now):
    if not isinstance(payload, dict) or not isinstance(payload.get("features"), list):
        raise LightningError("EUMETSAT's lightning catalogue is unreadable.")
    if payload.get("totalResults", 0) > len(payload["features"]):
        raise LightningError("EUMETSAT's lightning catalogue is incomplete.")
    rows = []
    for feature in payload["features"]:
        props = feature.get("properties", {}) if isinstance(feature, dict) else {}
        if not isinstance(props, dict) or props.get("parentIdentifier") != COLLECTION:
            continue
        identifier = props.get("identifier")
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_,+.\-]{50,300}", identifier)
            or not identifier.startswith("W_XX-EUMETSAT-Darmstadt,IMG+SAT,MTI1+LI-2-LFL--FD--")
        ):
            continue
        interval = props.get("date", "").split("/")
        if len(interval) != 2:
            continue
        start, end = map(iso_time, interval)
        if not now - 10800 <= start < end <= now + 120 or not 590 <= end - start <= 610:
            continue
        links = props.get("links", {}).get("sip-entries", [])
        bodies = [
            link.get("title")
            for link in links
            if isinstance(link, dict)
            and isinstance(link.get("title"), str)
            and "LI-2-LFL--FD--CHK-BODY--ARC-NC4E" in link["title"]
            and link["title"].endswith(".nc")
        ]
        if len(bodies) != 1 or not re.fullmatch(r"[A-Za-z0-9_,+.\-]{50,350}\.nc", bodies[0]):
            continue
        url = API_ROOT + quote(identifier, safe="") + "/entry?" + urlencode({"name": bodies[0]})
        rows.append(granule(url, start, end, "eumetsat", bodies[0]))
    return rows


def select_window(rows):
    if not rows:
        raise LightningError(
            "No recent satellite lightning files are published. Retrying automatically."
        )
    unique = {}
    for row in sorted(rows, key=lambda r: r["name"]):
        unique[(row["start"], row["end"])] = row
    end = max(r["end"] for r in unique.values())
    start, covered, gaps = end - 2700, end - 2700, []
    selected = sorted(
        (r for r in unique.values() if r["end"] > start and r["start"] < end),
        key=lambda r: r["start"],
    )
    for row in selected:
        if row["start"] > covered + 1:
            gaps.append([covered, row["start"]])
        covered = max(covered, row["end"])
    if covered < end - 1:
        gaps.append([covered, end])
    if len(selected) > 160:
        raise LightningError("The satellite catalogue has too many files for this window.")
    return {
        "files": list(reversed(selected)),
        "windowStart": start,
        "windowEnd": end,
        "gaps": gaps,
        "coverageComplete": not gaps,
    }


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urlsplit(newurl)
        host = destination.hostname or ""
        if (
            destination.scheme != "https"
            or destination.username
            or destination.password
            or not (
                host.endswith(".eumetsat.int")
                or host in ("noaa-goes18.s3.amazonaws.com", "noaa-goes19.s3.amazonaws.com")
            )
        ):
            raise LightningError("The satellite service returned an unsupported download redirect.")
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result and host != urlsplit(req.full_url).hostname:
            result.remove_header("Authorization")
        return result


def fetch_bytes(url, headers=None, data=None, limit=MAX_FILE_BYTES):
    request = Request(url, data=data, headers={"User-Agent": "WeatherDesk/1.0", **(headers or {})})
    with build_opener(SafeRedirect()).open(request, timeout=25) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise LightningError("The satellite download exceeded its size limit.")
    return raw


class LightningDesk:
    def __init__(self, directory, region="US", fetch=None):
        self.region, self.fetch = region, fetch or fetch_bytes
        self.lock, self.token_lock = threading.RLock(), threading.Lock()
        self.download_slots, self.download_lock = threading.BoundedSemaphore(3), threading.Lock()
        self.file_locks = {}
        self.store = StationStore(directory)
        self.store.path = Path(directory) / "eumetsat-key.json"
        self.directory = Path(directory) / "satellite-lightning"
        self.credentials = None
        self.saved, self.notice = False, None
        self.token, self.token_until = "", 0
        self.generation = 0
        self.states, self.file_map = {}, {}
        if region == "UK":
            try:
                saved = self.store.load()
                if saved:
                    self.credentials, self.saved = self.validate_credentials(saved), True
            except (SavedStationError, LightningError):
                self.notice = "The saved EUMETSAT connection could not be opened. Reconnect or forget it in Settings."

    @staticmethod
    def validate_credentials(values):
        result = []
        for field in ("consumerKey", "consumerSecret"):
            value = values.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[\x21-\x7e]{8,200}", value.strip()):
                raise LightningError(
                    "Enter your EUMETSAT consumer key and consumer secret from its free API-key page."
                )
            result.append(value.strip())
        return tuple(result)

    def connection(self):
        with self.lock:
            return {
                "configured": self.region == "US" or bool(self.credentials),
                "saved": self.saved,
                "requiresAccount": self.region == "UK",
                "notice": self.notice,
            }

    def update(self, values):
        if self.region != "UK":
            raise LightningError("NOAA lightning is automatic and needs no API key.")
        with self.lock:
            try:
                if values.get("action") == "forget":
                    self.store.forget()
                    credentials, saved = None, False
                elif values.get("action") == "connect":
                    credentials = self.validate_credentials(values)
                    saved = values.get("remember", True)
                    if not isinstance(saved, bool):
                        raise LightningError("Choose whether to remember the EUMETSAT connection.")
                    if saved:
                        self.store.save(dict(zip(("consumerKey", "consumerSecret"), credentials)))
                    else:
                        self.store.forget()
                else:
                    raise LightningError("Choose Connect or Forget EUMETSAT.")
            except SavedStationError as exc:
                raise LightningError(
                    "The EUMETSAT connection could not be saved or removed. Check the app folder's write permission."
                ) from exc
            self.credentials, self.saved = credentials, saved
            self.generation += 1
            self.token, self.token_until = "", 0
            self.states.clear()
            self.file_map.clear()
            self.notice = None
            return self.connection()

    def _bearer(self, generation):
        with self.token_lock:
            with self.lock:
                if generation != self.generation or not self.credentials:
                    raise LightningError("The EUMETSAT connection changed. Refresh the panel.")
                if self.token and self.token_until > time.time() + 60:
                    return self.token
                credentials = self.credentials
            basic = base64.b64encode(":".join(credentials).encode()).decode()
            raw = self.fetch(
                "https://api.eumetsat.int/token",
                headers={
                    "Authorization": "Basic " + basic,
                    "Content-Type": "application/x-www-form-urlencoded",
                },
                data=b"grant_type=client_credentials",
                limit=100000,
            )
            payload = json.loads(raw)
            token, expires = payload.get("access_token"), finite(
                payload.get("expires_in"), 120, 86400
            )
            if (
                not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_.~+/=-]{8,8192}", token)
                or expires is None
            ):
                raise LightningError("EUMETSAT returned an unreadable login response.")
            with self.lock:
                if generation != self.generation:
                    raise LightningError("The EUMETSAT connection changed. Refresh the panel.")
                self.token, self.token_until = token, time.time() + expires
            return token

    def snapshot(self, settings):
        lat, lon = coordinates(settings)
        key = "EUM" if self.region == "UK" else ("G18" if lon < -105 else "G19")
        source = "EUMETSAT Meteosat LI" if key == "EUM" else f"NOAA GOES-{key[1:]} GLM"
        now = time.time()
        with self.lock:
            base = {
                **self.connection(),
                "latitude": lat,
                "longitude": lon,
                "locationKey": f"{lat:.4f},{lon:.4f}",
                "source": source,
                "provider": "eumetsat" if key == "EUM" else "noaa",
                "radiusKm": 75,
                "windowMinutes": 45,
                "refreshSeconds": 300 if key == "EUM" else 60,
                "staleAfterSeconds": 1800 if key == "EUM" else 300,
            }
            if key != "EUM" and (abs(lat) > 52 or not -180 <= lon <= -45):
                return {
                    **base,
                    "status": "unsupported",
                    "error": "This location is outside Weather Desk's supported GOES lightning coverage.",
                    "files": [],
                }
            if not base["configured"]:
                return {**base, "status": "unconfigured", "files": [], "stale": False}
            state = self.states.setdefault(
                key,
                {
                    "nextAt": 0,
                    "loading": False,
                    "checkedAt": None,
                    "catalogue": None,
                    "error": None,
                },
            )
            if not state["loading"] and now >= state["nextAt"]:
                state["loading"] = True
                threading.Thread(
                    target=self._refresh, args=(key, self.generation), daemon=True
                ).start()
            catalogue = copy.deepcopy(state["catalogue"] or {"files": []})
            for row in catalogue["files"]:
                row.pop("url", None)
            stale = bool(state["error"]) or (
                bool(catalogue.get("windowEnd"))
                and now - catalogue["windowEnd"] > base["staleAfterSeconds"]
            )
            return {
                **base,
                **catalogue,
                "status": (
                    "error" if state["error"] else "ready" if state["catalogue"] else "loading"
                ),
                "loading": state["loading"],
                "checkedAt": state["checkedAt"],
                "stale": bool(stale),
                "error": state["error"],
            }

    def _refresh(self, key, generation):
        now = time.time()
        error, catalogue = None, None
        try:
            if key == "EUM":
                self._bearer(generation)
                stamp = lambda t: dt.datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                url = "https://api.eumetsat.int/data/search-products/1.0.0/os?" + urlencode(
                    {
                        "format": "json",
                        "pi": COLLECTION,
                        "dtstart": stamp(now - 7200),
                        "dtend": stamp(now),
                        "c": 36,
                        "sort": "start,time,0",
                    }
                )
                rows = parse_eumetsat(json.loads(self.fetch(url, limit=4_000_000)), now)
            else:
                rows = []
                for offset in (0, 3600):
                    prefix = dt.datetime.fromtimestamp(now - offset, UTC).strftime(
                        "GLM-L2-LCFA/%Y/%j/%H/"
                    )
                    url = f"https://noaa-goes{key[1:]}.s3.amazonaws.com/?" + urlencode(
                        {"list-type": "2", "prefix": prefix, "max-keys": 1000}
                    )
                    rows.extend(parse_noaa(self.fetch(url, limit=1_000_000), key, now))
            catalogue = select_window(rows)
        except HTTPError as exc:
            if exc.code in (401, 403) and key == "EUM":
                error = "EUMETSAT rejected this connection. Check your free account's consumer key and secret in Settings."
            elif exc.code == 429:
                error = "The satellite service is limiting requests. Retrying automatically."
            else:
                error = "The satellite lightning catalogue is temporarily unavailable."
        except LightningError as exc:
            error = str(exc)
        except Exception:
            error = "Satellite lightning could not refresh. Retrying automatically."
        with self.lock:
            if generation != self.generation:
                return
            state = self.states[key]
            if catalogue:
                state["catalogue"], state["checkedAt"] = catalogue, time.time()
                for row in catalogue["files"]:
                    self.file_map[row["id"]] = row
                self.file_map = {
                    fid: row for fid, row in self.file_map.items() if row["end"] >= now - 10800
                }
            state.update(
                error=error, loading=False, nextAt=time.time() + (300 if key == "EUM" else 60)
            )
        self._prune()

    def file(self, file_id):
        if not isinstance(file_id, str) or not re.fullmatch(r"[a-f0-9]{32}", file_id):
            raise LightningError("Unknown lightning file. Refresh the panel.")
        with self.lock:
            row, generation = self.file_map.get(file_id), self.generation
            if row is None or time.time() - row["end"] > 10800:
                raise LightningError("This lightning file is no longer in the current catalogue.")
        with self.download_lock:
            lock = self.file_locks.setdefault(file_id, threading.Lock())
        with lock, self.download_slots:
            self.directory.mkdir(parents=True, exist_ok=True)
            path = self.directory / (file_id + ".nc")
            if path.exists() and 8 <= path.stat().st_size <= MAX_FILE_BYTES:
                raw = path.read_bytes()
                if raw.startswith(HDF_SIGNATURE):
                    return raw
            headers = {}
            try:
                if row["provider"] == "eumetsat":
                    headers["Authorization"] = "Bearer " + self._bearer(generation)
                raw = self.fetch(row["url"], headers=headers, limit=MAX_FILE_BYTES)
            except HTTPError as exc:
                if exc.code == 401 and row["provider"] == "eumetsat":
                    with self.lock:
                        if generation == self.generation:
                            self.token_until = 0
                    raise LightningError(
                        "EUMETSAT's login expired. The next refresh will renew it."
                    ) from exc
                if exc.code == 403 and row["provider"] == "eumetsat":
                    raise LightningError(
                        "EUMETSAT did not authorise this file download. Check that your free Data Store access is activated."
                    ) from exc
                raise LightningError(
                    "A satellite file could not download. Retrying automatically."
                ) from exc
            except LightningError:
                raise
            except Exception as exc:
                raise LightningError(
                    "A satellite file could not download. Retrying automatically."
                ) from exc
            if (
                not isinstance(raw, bytes)
                or not raw.startswith(HDF_SIGNATURE)
                or len(raw) > MAX_FILE_BYTES
            ):
                raise LightningError("The satellite service returned an unreadable NetCDF file.")
            with self.lock:
                if generation != self.generation:
                    raise LightningError("The lightning connection changed. Refresh the panel.")
            temporary = path.with_suffix(".tmp")
            try:
                temporary.write_bytes(raw)
                temporary.replace(path)
            except OSError:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
            return raw

    def _prune(self):
        with self.download_lock:
            self.file_locks = {
                key: lock
                for key, lock in self.file_locks.items()
                if key in self.file_map or lock.locked()
            }
        if not self.directory.exists():
            return
        try:
            files = sorted(self.directory.glob("*.nc"), key=lambda p: p.stat().st_mtime)
            size = sum(p.stat().st_size for p in files)
            for path in files:
                if size <= 160 * 1024 * 1024 and time.time() - path.stat().st_mtime <= 10800:
                    break
                length = path.stat().st_size
                try:
                    path.unlink()
                    size -= length
                except OSError:
                    pass
        except OSError:
            pass
