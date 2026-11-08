"""Read-only PSK Reporter reception reports, globally paced to one query / 5 min.

These are crowdsourced digital reception observations, not forecasts or proof
that an SSB path is open. Empty results never turn modelled bands into 'closed'.
"""
from __future__ import annotations
import copy
import math
from pathlib import Path
import re
import threading
import time
import json
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
from local_store import write_private_json

ENDPOINT = "https://retrieve.pskreporter.info/query"


def callsign(value):
    value = str(value or "").strip().upper()
    if len(value) > 24 or not re.fullmatch(r"[A-Z0-9]+(?:/[A-Z0-9]+)*", value):
        return ""
    return value if re.search(r"[A-Z]", value) and re.search(r"\d", value) else ""


def default_callsign(value):
    # Display headings can contain two calls. Use the first complete call and
    # display it prominently; the operator can override it in Path setup.
    for token in re.split(r"\s+|[,·]", str(value or "")):
        candidate = callsign(token)
        if candidate:
            return candidate
    return ""


def band_for(frequency):
    mhz = frequency / 1_000_000
    for band, low, high in [("160m",1.8,2), ("80m",3.5,4), ("60m",5.06,5.45),
                            ("40m",7,7.3), ("30m",10.1,10.15), ("20m",14,14.35),
                            ("17m",18.068,18.168), ("15m",21,21.45),
                            ("12m",24.89,24.99), ("10m",28,29.7)]:
        if low <= mhz <= high:
            return band
    return "Other"


def parse_reports(raw, requested_call, now):
    raw = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    if not isinstance(raw, str) or len(raw) > 6_000_000 or re.search(r"<!\s*(?:DOCTYPE|ENTITY)", raw, re.I):
        raise ValueError("Unreadable PSK Reporter response.")
    root = ET.fromstring(raw)
    if root.tag.split("}")[-1].lower() not in ("receptionreports", "pskreporter"):
        raise ValueError("PSK Reporter returned an error instead of reception reports.")
    if any(n.tag.split("}")[-1].lower() in ("error", "errors") for n in root.iter()):
        raise ValueError("PSK Reporter is unavailable or rate limited.")
    reports, seen = [], set()
    for node in root.iter():
        if node.tag.split("}")[-1] != "receptionReport":
            continue
        a = node.attrib
        if callsign(a.get("senderCallsign")) != requested_call:
            continue
        receiver = callsign(a.get("receiverCallsign"))
        try:
            stamp, frequency = float(a.get("flowStartSeconds", "")), float(a.get("frequency", ""))
        except (ValueError, TypeError):
            continue
        if not receiver or not math.isfinite(stamp) or not math.isfinite(frequency):
            continue
        if not now - 3600 <= stamp <= now + 120 or not 100_000 <= frequency <= 100_000_000_000:
            continue
        identity = (receiver, stamp, frequency)
        if identity in seen:
            continue
        seen.add(identity)
        locator = str(a.get("receiverLocator", "")).strip().upper()
        if not re.fullmatch(r"[A-R]{2}\d{2}(?:[A-X]{2}(?:\d{2})?)?", locator):
            locator = ""
        try:
            snr = float(a.get("sNR", ""))
            if not math.isfinite(snr) or not -100 <= snr <= 100: snr = None
        except (ValueError, TypeError):
            snr = None
        reports.append({"receiver":receiver, "locator":locator, "timestamp":stamp,
                        "frequency":frequency, "band":band_for(frequency),
                        "mode":str(a.get("mode", ""))[:24], "snr":snr})
    reports.sort(key=lambda row: row["timestamp"], reverse=True)
    return {"timestamp":now, "callsign":requested_call, "windowSeconds":3600,
            "reports":reports[:100], "limited":len(reports)>=100,
            "receiverCount":len({r["receiver"] for r in reports[:100]})}


class ReceptionReports:
    def __init__(self, directory, fetch):
        self.path = Path(directory) / "psk-reports.json"
        self.fetch = fetch
        self.lock = threading.RLock()
        self.cache = {}
        self.next_attempt = 0
        self.thread = None
        self.error = None
        self.attempt_call = ""
        try:
            if self.path.stat().st_size <= 1_000_000:
                value = json.loads(self.path.read_text("utf-8"))
                if value.get("schema") == 1:
                    next_attempt = float(value.get("nextAttempt", 0))
                    self.next_attempt = min(next_attempt, time.time() + 600) if math.isfinite(next_attempt) and next_attempt >= 0 else 0
                    self.cache = {k:v for k,v in value.get("cache", {}).items()
                                  if callsign(k) == k and isinstance(v, dict) and v.get("callsign") == k
                                  and isinstance(v.get("timestamp"), (int, float))
                                  and not isinstance(v["timestamp"], bool) and math.isfinite(v["timestamp"])
                                  and isinstance(v.get("reports"), list)
                                  and all(isinstance(row, dict) for row in v["reports"])}
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def _save(self):
        try:
            write_private_json(self.path, {"schema":1,"nextAttempt":self.next_attempt,"cache":self.cache})
        except OSError:
            pass

    def snapshot(self, requested_call, now=None):
        now = time.time() if now is None else now
        requested_call = callsign(requested_call)
        if not requested_call:
            return {"configured":False,"value":None,"loading":False,"stale":True,"error":None}
        with self.lock:
            if (not self.thread or not self.thread.is_alive()) and now >= self.next_attempt:
                self.attempt_call = requested_call
                self.next_attempt = now + 305  # shared across calls and persisted across restarts
                self._save()
                self.thread = threading.Thread(target=self._load, args=(requested_call,), daemon=True)
                self.thread.start()
            value = self.cache.get(requested_call)
            age = now - value.get("timestamp", 0) if value else math.inf
            error = self.error if self.attempt_call == requested_call else None
            return {"configured":True,"callsign":requested_call,"value":copy.deepcopy(value),
                    "loading":bool(self.thread and self.thread.is_alive() and self.attempt_call==requested_call),
                    "stale":not value or age < -120 or age > 900 or bool(error),
                    "nextAttempt":self.next_attempt,"error":error,"interval":305}

    def _load(self, requested_call):
        try:
            query = urlencode({"senderCallsign":requested_call,"flowStartSeconds":-3600,
                               "rptlimit":100,"rronly":1,"noactive":1})
            value = parse_reports(self.fetch(ENDPOINT + "?" + query), requested_call, time.time())
            with self.lock:
                self.cache[requested_call] = value
                if len(self.cache) > 8: self.cache.pop(next(iter(self.cache)))
                self.error = None
                self._save()
        except Exception:
            with self.lock:
                self.error = "PSK Reporter unavailable or rate limited. Retrying after the five-minute interval."
