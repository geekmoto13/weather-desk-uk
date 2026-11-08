"""Public space weather and a cached KC2G point-to-point HF forecast.

Python standard library only. No Ambient credentials enter this module.
"""

from __future__ import annotations
import copy
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from local_store import write_private_json
from radio_reports import ReceptionReports, callsign, default_callsign
import radio_voacap

DESTINATION_CATALOG = json.loads((Path(__file__).parent / "data/radio-destinations.json").read_text("utf-8"))

UTC = dt.timezone.utc
NOAA = "https://services.swpc.noaa.gov/"
KC2G = "https://prop.kc2g.com/"
BANDS = ["10m", "12m", "15m", "17m", "20m", "30m", "40m", "60m", "80m", "160m"]
TARGETS = {
    "uk": ("England · Midlands", 52.77, -1.56),
    "europe": ("Central Europe", 50.11, 8.68),
    "west": ("US West Coast", 37.77, -122.42),
    "japan": ("Japan", 35.68, 139.65),
    "australia": ("Eastern Australia", -33.86, 151.21),
    "custom": ("Custom grid", None, None),
}
TARGETS.update({d["id"]: (d["name"] + " · " + d["prefix"], d["latitude"], d["longitude"])
                for d in DESTINATION_CATALOG["entities"] + DESTINATION_CATALOG["cities"]})
MODES = {
    "SSB": {"bandwidth": 3000, "requiredSnr": 6},
    "CW": {"bandwidth": 500, "requiredSnr": 0},
    "FT8": {"bandwidth": 50, "requiredSnr": -3},
}
DEFAULT_RADIO = {
    "target": "uk",
    "customGrid": "IO92",
    "mode": "SSB",
    "power": 100,
    "txGain": 0,
    "rxGain": 0,
    "noise": "RESIDENTIAL",
    "reportCallsign": "",
    "engine": "kc2g",
}


def numeric(value, low=-math.inf, high=math.inf):
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        n = float(value)
        return n if math.isfinite(n) and low <= n <= high else None
    except (TypeError, ValueError):
        return None


def stamp(value):
    if not isinstance(value, str):
        return None
    try:
        t = dt.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return t.replace(tzinfo=t.tzinfo or UTC).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def read_public(url):
    request = Request(
        url,
        headers={
            "User-Agent": "Shannons-Weather-Desk/15 (personal amateur-radio dashboard)",
            "Accept": "application/json, application/xml, text/plain",
        },
    )
    with urlopen(request, timeout=25) as response:
        data = response.read(6_000_001)
    if len(data) > 6_000_000:
        raise ValueError("Public response too large")
    return data.decode("utf-8-sig")


def as_json(raw):
    return json.loads(raw) if isinstance(raw, (str, bytes)) else raw


def daily_solar(raw, now):
    """NOAA DSD: retain the latest completed UTC day's values, including missing data."""
    issued = re.search(r":Issued:\s*(\d{4}) UT (\d{1,2} \w{3} \d{4})", raw)
    if not issued:
        raise ValueError("Missing NOAA daily report time")
    published = (
        dt.datetime.strptime(issued[1] + " " + issued[2], "%H%M %d %b %Y")
        .replace(tzinfo=UTC)
        .timestamp()
    )
    if published > now + 300:
        raise ValueError("Future NOAA daily report")
    today = dt.datetime.fromtimestamp(now, UTC).date()
    rows = []
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 5 or not re.fullmatch(r"\d{4}", fields[0]):
            continue
        try:
            day = dt.date(*map(int, fields[:3]))
        except ValueError:
            continue
        if day >= today or day > dt.datetime.fromtimestamp(published, UTC).date():
            continue
        rows.append(
            {
                "date": day.isoformat(),
                "sfi": numeric(fields[3], 0, 1000),
                "sn": numeric(fields[4], 0, 1000),
            }
        )
    if not rows:
        raise ValueError("No completed daily solar observations")
    row = max(rows, key=lambda p: p["date"])
    day_end = dt.datetime.fromisoformat(row["date"]).replace(tzinfo=UTC).timestamp() + 86400
    return {**row, "timestamp": min(published, day_end), "issuedAt": published}


def scales(raw, now):
    raw = as_json(raw)
    row = raw.get("0") if isinstance(raw, dict) else None
    if not isinstance(row, dict):
        raise ValueError("Missing current NOAA scales")
    ts = stamp(str(row.get("DateStamp", "")) + "T" + str(row.get("TimeStamp", "")))
    result = {"timestamp": ts, "levels": {}}
    for key in ("R", "S", "G"):
        entry = row.get(key)
        result["levels"][key] = (
            numeric(entry.get("Scale"), 0, 5) if isinstance(entry, dict) else None
        )
    if ts is None or all(v is None for v in result["levels"].values()):
        raise ValueError("Incomplete NOAA scales")
    maximum = raw.get("-1", {})
    result["maximum24h"] = {
        code: numeric(maximum.get(code, {}).get("Scale"), 0, 5)
        if isinstance(maximum.get(code), dict) else None
        for code in ("R", "S", "G")
    }
    result["outlook"] = []
    for key in ("1", "2", "3"):
        row = raw.get(key, {})

        def field(kind, name):
            r = row.get(kind, {})
            return numeric(r.get(name), 0, 100) if isinstance(r, dict) else None

        result["outlook"].append(
            {
                "date": row.get("DateStamp"),
                "g": field("G", "Scale"),
                "minorBlackout": field("R", "MinorProb"),
                "majorBlackout": field("R", "MajorProb"),
                "radiation": field("S", "Prob"),
            }
        )
    return result


def kp_forecast(raw, now):
    raw = as_json(raw)
    if not isinstance(raw, list):
        raise ValueError("Invalid Kp feed")
    by_time = {}
    for row in raw:
        if not isinstance(row, dict):
            continue
        ts, value = stamp(row.get("time_tag")), numeric(row.get("kp"), 0, 9)
        kind = str(row.get("observed", "")).lower()
        if ts is None or value is None or kind not in ("observed", "estimated", "predicted"):
            continue
        if now - 86400 <= ts <= now + 3 * 86400:
            by_time[ts] = {"timestamp": ts, "kp": value, "kind": kind}
    rows = [by_time[t] for t in sorted(by_time)]
    if not rows:
        raise ValueError("No usable Kp intervals")
    recent = [
        p for p in rows if p["timestamp"] <= now + 60 and p["kind"] in ("observed", "estimated")
    ]
    return {
        "timestamp": recent[-1]["timestamp"] if recent else now,
        "current": recent[-1] if recent else None,
        "points": rows,
    }


def wind_data(raw, now, magnetic):
    rows = as_json(raw)
    if not isinstance(rows, list):
        raise ValueError("Invalid solar wind feed")
    found = []
    for row in rows:
        if not isinstance(row, dict) or row.get("active") is not True:
            continue
        if row.get("overall_quality") not in (0, "0"):
            continue
        ts = stamp(row.get("time_tag"))
        if ts is None or ts > now + 300:
            continue
        if magnetic:
            values = {
                "bz": numeric(row.get("bz_gsm"), -500, 500),
                "bt": numeric(row.get("bt"), 0, 500),
            }
        else:
            values = {
                "speed": numeric(row.get("proton_speed"), 50, 3000),
                "density": numeric(row.get("proton_density"), 0, 1000),
            }
        if all(v is None for v in values.values()):
            continue
        found.append(
            {"timestamp": ts, "spacecraft": str(row.get("source", "Primary"))[:30], **values}
        )
    if not found:
        raise ValueError("No primary spacecraft observations")
    return max(found, key=lambda p: p["timestamp"])


def xray(raw, now):
    rows = as_json(raw)
    if not isinstance(rows, list):
        raise ValueError("Invalid X-ray feed")
    good = []
    series = {"long": [], "short": []}
    for row in rows:
        if not isinstance(row, dict) or row.get("energy") not in ("0.1-0.8nm", "0.05-0.4nm"):
            continue
        if row.get("electron_contaminaton") is True or row.get("electron_contamination") is True:
            continue
        ts, value = stamp(row.get("time_tag")), numeric(row.get("flux"), 0, 1)
        if ts is not None and ts <= now + 300 and value is not None:
            if row["energy"] == "0.1-0.8nm":
                good.append({"timestamp": ts, "flux": value})
            if now - 21600 <= ts <= now:
                series["long" if row["energy"] == "0.1-0.8nm" else "short"].append({"timestamp": ts, "value": value})
    if not good:
        raise ValueError("No long-wave X-ray observations")
    return {**max(good, key=lambda p: p["timestamp"]), "series": series}


def protons(raw, now):
    rows = as_json(raw)
    if not isinstance(rows, list):
        raise ValueError("Unreadable NOAA proton flux data.")
    series = {"10": [], "50": [], "100": []}
    for row in rows:
        if not isinstance(row, dict): continue
        energy = str(row.get("energy", ""))
        match = re.fullmatch(r">=?\s*(10|50|100)\s*MeV", energy)
        if not match: continue
        ts, flux = stamp(row.get("time_tag")), numeric(row.get("flux"), 0, 1e9)
        if ts is not None and now - 21600 <= ts <= now + 120 and flux is not None:
            series[match[1]].append({"timestamp":ts,"value":flux})
    if not series["10"]:
        raise ValueError("No current NOAA ≥10 MeV proton measurements.")
    return {"timestamp":max(p["timestamp"] for p in series["10"]),"series":series}


def wwv(raw, now):
    issued = re.search(r":Issued:\s*(\d{4} \w{3} \d{1,2} \d{4}) UTC", raw)
    values = re.search(
        r"Solar flux\s+([\d.]+)\s+and estimated planetary A-index\s+([\d.]+)", raw, re.I
    )
    if not issued or not values:
        raise ValueError("Incomplete geophysical summary")
    ts = dt.datetime.strptime(issued[1], "%Y %b %d %H%M").replace(tzinfo=UTC).timestamp()
    date = None
    observation = re.search(r"Solar-terrestrial indices for (\d{1,2}) (\w+) follow", raw)
    if observation:
        report_day = dt.datetime.fromtimestamp(ts, UTC).date()
        try:
            date = dt.datetime.strptime(
                f"{report_day.year} {observation[2]} {observation[1]}", "%Y %B %d"
            ).date()
            if date > report_day:
                date = date.replace(year=date.year - 1)
            date = date.isoformat()
        except ValueError:
            date = None
    return {
        "timestamp": ts,
        "date": date,
        "sfi": numeric(values[1], 0, 1000),
        "aIndex": numeric(values[2], 0, 500),
        "text": raw.split("#\n")[-1].strip()[:5000],
    }


def bulletins(raw, now):
    rows = as_json(raw)
    if not isinstance(rows, list):
        raise ValueError("Invalid bulletin feed")
    found = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        ts = stamp(row.get("issue_datetime"))
        message = row.get("message")
        if ts is None or not now - 48 * 3600 <= ts <= now + 300 or not isinstance(message, str):
            continue
        headline = next(
            (
                s.strip()
                for s in message.splitlines()
                if re.match(r"^(ALERT|WATCH|WARNING|SUMMARY|CANCEL)", s.strip())
            ),
            str(row.get("product_id", "NOAA bulletin")),
        )
        found.append(
            {
                "timestamp": ts,
                "id": str(row.get("product_id", ""))[:50],
                "headline": headline[:200],
                "message": message[:18000],
            }
        )
    return {
        "timestamp": now,
        "items": sorted(found, key=lambda p: p["timestamp"], reverse=True)[:12],
    }


def grid_location(grid):
    grid = str(grid).strip().upper()
    if not re.fullmatch(r"[A-R]{2}\d{2}(?:[A-X]{2}(?:\d{2})?)?", grid):
        raise ValueError("Use a 4, 6 or 8 character Maidenhead grid.")
    lon = (ord(grid[0]) - 65) * 20 - 180 + int(grid[2]) * 2
    lat = (ord(grid[1]) - 65) * 10 - 90 + int(grid[3])
    width, height = 2, 1
    if len(grid) >= 6:
        lon += (ord(grid[4]) - 65) / 12
        lat += (ord(grid[5]) - 65) / 24
        width, height = 1 / 12, 1 / 24
    if len(grid) == 8:
        lon += int(grid[6]) / 120
        lat += int(grid[7]) / 240
        width, height = 1 / 120, 1 / 240
    return lat + height / 2, lon + width / 2


def clean_preferences(values):
    if not isinstance(values, dict):
        raise ValueError("Invalid radio preferences.")
    result = copy.deepcopy(DEFAULT_RADIO)
    result.update({k: v for k, v in values.items() if k in result})
    if (
        not isinstance(result["target"], str)
        or not isinstance(result["mode"], str)
        or result["target"] not in TARGETS
        or result["mode"] not in MODES
    ):
        raise ValueError("Choose a listed destination and mode.")
    result["customGrid"] = str(result["customGrid"]).strip().upper()
    if result["target"] == "custom":
        grid_location(result["customGrid"])
    elif not re.fullmatch(r"[A-R]{2}\d{2}(?:[A-X]{2}(?:\d{2})?)?", result["customGrid"]):
        result["customGrid"] = ""
    supplied_call = str(result["reportCallsign"] or "").strip()
    result["reportCallsign"] = callsign(supplied_call)
    if supplied_call and not result["reportCallsign"]:
        raise ValueError("Enter one callsign for PSK Reporter, or leave it blank to use your station callsign.")
    if result["engine"] not in ("kc2g", "voacap"):
        raise ValueError("Choose the KC2G or VOACAP prediction model.")
    if result["noise"] not in ("CITY", "RESIDENTIAL", "RURAL", "QUIETRURAL", "QUIET", "NOISY"):
        raise ValueError("Choose a listed noise environment.")
    for key, low, high in (("power", 0.1, 1500), ("txGain", -20, 20), ("rxGain", -20, 20)):
        n = numeric(result[key], low, high)
        if n is None:
            raise ValueError("Use 0.1–1500 W and antenna gains between −20 and +20 dBi.")
        result[key] = n
    if result["engine"] == "voacap" and (result["power"] < 1 or not result["power"].is_integer()):
        raise ValueError("VOACAP needs whole-watt transmit power (1–1500 W). KC2G supports fractional watts.")
    return result


def path_context(weather, prefs):
    lat, lon = numeric(weather.get("latitude"), -90, 90), numeric(
        weather.get("longitude"), -180, 180
    )
    if lat is None or lon is None:
        raise ValueError("Set your station coordinates in Settings.")
    name, rxlat, rxlon = TARGETS[prefs["target"]]
    if prefs["target"] == "custom":
        rxlat, rxlon = grid_location(prefs["customGrid"])
        name = prefs["customGrid"]
    # A propagation calculation needs an approximate location, not home-level precision.
    context = {
        "txLat": round(lat, 2),
        "txLon": round(lon, 2),
        "rxLat": rxlat,
        "rxLon": rxlon,
        "destination": name,
        "preferences": copy.deepcopy(prefs),
    }
    if prefs.get("engine") == "voacap":
        try:
            context["provider"] = radio_voacap.endpoint()
        except ValueError:
            # Report a bad optional endpoint inside its own feed; NOAA and
            # reception observations must remain available independently.
            context["provider"] = "invalid-voacap-configuration"
    return context


def parse_path(raw, model_time, context):
    raw = as_json(raw)
    if (
        not isinstance(raw, dict)
        or not isinstance(raw.get("table"), list)
        or not isinstance(raw.get("bands"), list)
    ):
        raise ValueError("Invalid HF model response")
    rows = []
    seen = set()
    for band, values in zip(raw["bands"], raw["table"]):
        if not isinstance(band, list) or len(band) < 2 or band[0] not in BANDS or band[0] in seen:
            continue
        frequency = numeric(band[1], 1, 35)
        # The live planner includes a 25th column. The table's 24 UTC hour
        # positions are the first 24 columns; do not discard a valid band.
        if frequency is None or not isinstance(values, list) or len(values) not in (24, 25):
            continue
        cells = []
        for cell in values[:24]:
            cell = cell if isinstance(cell, dict) else {}
            pr, noise = numeric(cell.get("pr"), -400, 100), numeric(cell.get("noise"), -300, 100)
            cells.append(
                {
                    "bcr": numeric(cell.get("bcr"), 0, 100),
                    "snr": pr - noise if pr is not None and noise is not None else None,
                    "propagation": numeric(cell.get("pop"), 0, 100),
                }
            )
        seen.add(band[0])
        rows.append({"band": band[0], "mhz": frequency, "hours": cells})
    if seen != set(BANDS):
        raise ValueError("Incomplete ten-band forecast")
    return {
        "timestamp": model_time,
        "bands": rows,
        "context": context,
        "method": "KC2G / ITU-R HF · idealised fixed-gain antennas",
        "model": "kc2g",
    }


class PublicFeed:
    def __init__(
        self, directory, name, loader, interval, max_age, retry=None, future_tolerance=300
    ):
        self.path = Path(directory) / (name + ".json")
        self.loader = loader
        self.interval = interval
        self.max_age = max_age
        self.retry = retry or min(interval, 60)
        self.lock = threading.RLock()
        self.future_tolerance = future_tolerance
        self.value = None
        self.fetched_at = 0
        self.next_attempt = 0
        self.thread = None
        self.error = None
        try:
            if self.path.stat().st_size <= 1_000_000:
                cached = json.loads(self.path.read_text("utf-8"))
                if (
                    cached.get("schema") == 1
                    and isinstance(cached.get("value"), dict)
                    and numeric(cached.get("fetchedAt"), 0, time.time() + 300) is not None
                ):
                    self.value = cached["value"]
                    self.fetched_at = float(cached["fetchedAt"])
                    self.next_attempt = self.fetched_at + interval
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def get(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if (not self.thread or not self.thread.is_alive()) and now >= self.next_attempt:
                self.next_attempt = now + self.interval
                self.thread = threading.Thread(target=self._load, daemon=True)
                self.thread.start()
            ts = self.value.get("timestamp") if self.value else None
            age = now - ts if numeric(ts) is not None else math.inf
            stale = bool(
                self.error
                or not self.value
                or age > self.max_age
                or age < -self.future_tolerance
                or now - self.fetched_at > max(self.interval * 2, self.max_age)
            )
            return {
                "value": copy.deepcopy(self.value),
                "fetchedAt": self.fetched_at or None,
                "stale": stale,
                "loading": bool(self.thread and self.thread.is_alive()),
                "error": self.error,
                "interval": self.interval,
            }

    def _load(self):
        try:
            now = time.time()
            value = self.loader(now)
            if not isinstance(value, dict) or numeric(value.get("timestamp")) is None:
                raise ValueError("Invalid observation time")
            if value["timestamp"] > now + self.future_tolerance:
                raise ValueError("Future observation time")
            with self.lock:
                self.value = value
                self.fetched_at = now
                self.error = None
            try:
                write_private_json(self.path, {"schema": 1, "fetchedAt": now, "value": value})
            except OSError:
                pass
        except Exception as exc:
            with self.lock:
                detail = str(exc) if isinstance(exc, ValueError) else (
                    f"Public feed returned HTTP {exc.code}" if hasattr(exc, "code") else
                    "The public service did not answer."
                )
                self.error = "Source unavailable: " + detail + " Any displayed values are saved data."
                self.next_attempt = time.time() + self.retry


class RadioDesk:
    def __init__(self, directory, fetch=read_public):
        self.directory = Path(directory) / "radio"
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fetch = fetch
        self.lock = threading.RLock()
        self.prefs = clean_preferences(DEFAULT_RADIO)
        self.reports = ReceptionReports(self.directory, fetch)
        self.paths = {}
        self.preferences_path = self.directory / "preferences.json"
        try:
            self.prefs = clean_preferences(json.loads(self.preferences_path.read_text("utf-8")))
        except (OSError, ValueError, TypeError):
            pass
        specs = {
            "daily": (NOAA + "text/daily-solar-indices.txt", daily_solar, 3600, 36 * 3600),
            "scales": (NOAA + "products/noaa-scales.json", scales, 120, 15 * 60),
            "kp": (
                NOAA + "products/noaa-planetary-k-index-forecast.json",
                kp_forecast,
                900,
                6 * 3600,
            ),
            "mag": (
                NOAA + "json/rtsw/rtsw_mag_1m.json",
                lambda r, n: wind_data(r, n, True),
                300,
                20 * 60,
            ),
            "wind": (
                NOAA + "json/rtsw/rtsw_wind_1m.json",
                lambda r, n: wind_data(r, n, False),
                300,
                20 * 60,
            ),
            "xray": (NOAA + "json/goes/primary/xrays-6-hour.json", xray, 300, 20 * 60),
            "protons": (NOAA + "json/goes/primary/integral-protons-6-hour.json", protons, 300, 20 * 60),
            "wwv": (NOAA + "text/wwv.txt", wwv, 1800, 12 * 3600),
            "bulletins": (NOAA + "products/alerts.json", bulletins, 300, 15 * 60),
        }
        self.feeds = {}
        for name, (url, parser, interval, age) in specs.items():
            loader = lambda now, url=url, parser=parser: parser(self.fetch(url), now)
            self.feeds[name] = PublicFeed(self.directory, name, loader, interval, age)

    def update(self, values):
        with self.lock:
            prefs = clean_preferences({**self.prefs, **values})
            write_private_json(self.preferences_path, prefs)
            self.prefs = prefs
        return {"settings": copy.deepcopy(prefs)}

    def destinations(self):
        return copy.deepcopy(DESTINATION_CATALOG)

    def _path(self, context, now):
        prefs = context["preferences"]
        if prefs["engine"] == "voacap":
            url = radio_voacap.endpoint()
            return radio_voacap.parse(self.fetch(radio_voacap.request_url(context, now, url)), now, context, url)
        mode = MODES[prefs["mode"]]
        first = as_json(self.fetch(KC2G + "api/latest_hourly.json"))
        model_time = numeric(first.get("target_time")) if isinstance(first, dict) else None
        if model_time is None or model_time < now - 3 * 3600 or model_time > now + 3600:
            raise ValueError("HF model not current")
        params = {
            "txloc": f"{context['txLat']:.2f},{context['txLon']:.2f}",
            "rxloc": f"{context['rxLat']:.5f},{context['rxLon']:.5f}",
            "txpow": prefs["power"],
            "txant": "ISOTROPIC",
            "rxant": "ISOTROPIC",
            "txgos": prefs["txGain"],
            "rxgos": prefs["rxGain"],
            "rxnoise": prefs["noise"],
            "bw": mode["bandwidth"],
            "snrr": mode["requiredSnr"],
            "start_hour": "ZERO",
            "tz_offset": 0,
        }
        raw = self.fetch(KC2G + "hfprop/planner.json?" + urlencode(params))
        last = as_json(self.fetch(KC2G + "api/latest_hourly.json"))
        if first.get("run_id") is None or first.get("run_id") != last.get("run_id"):
            raise ValueError("Model changed during calculation")
        returned = as_json(raw)
        info = returned.get("run_info") if isinstance(returned, dict) else None
        if isinstance(info, dict) and info.get("run_id") != first["run_id"]:
            raise ValueError("HF model generation changed during calculation. Retrying shortly.")
        result = parse_path(raw, model_time, context)
        result["runId"] = first["run_id"]
        result["fetchedAt"] = now
        return result

    def snapshot(self, weather):
        with self.lock:
            context = path_context(weather, self.prefs)
            key = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:20]
            if key not in self.paths:
                voacap = self.prefs["engine"] == "voacap"
                self.paths[key] = PublicFeed(
                    self.directory,
                    "path-" + key,
                    lambda now: self._path(context, now),
                    3600 if voacap else 900,
                    2 * 3600 if voacap else 3 * 3600,
                    future_tolerance=3600,
                )
                if len(self.paths) > 8:
                    self.paths.pop(next(iter(self.paths)))
            path = self.paths[key].get()
            # Cache identity is checked again before any previous result is displayed.
            if path["value"] and path["value"].get("context") != context:
                path["value"] = None
                path["stale"] = True
            settings = copy.deepcopy(self.prefs)
        sources = {name: feed.get() for name, feed in self.feeds.items()}
        reports = self.reports.snapshot(settings["reportCallsign"] or default_callsign(weather.get("callsign")))
        return {
            "settings": settings,
            "context": context,
            "sources": sources,
            "path": path,
            "reports": reports,
            "loading": path["loading"] or reports["loading"] or any(s["loading"] for s in sources.values()),
            "serverTime": time.time(),
        }
