"""VOACAP via the HamClock-compatible backend protocol, not VOACAP.com scraping.

The current nine-band wire order is 80,60,40,30,20,17,15,12,10 metres.
This service returns circuit reliability, not SNR. 160 m stays unavailable.
"""
from __future__ import annotations
import datetime as dt
import math
import os
import re
from urllib.parse import urlencode, urlsplit

DEFAULT_ENDPOINT = "https://www.hamclock.com/ham/HamClock/fetchBandConditions.pl"
FREQUENCIES = [("80m",3.75),("60m",5.36),("40m",7.15),("30m",10.13),
               ("20m",14.18),("17m",18.12),("15m",21.23),("12m",24.94),("10m",28.85)]
MODE_CODES = {"SSB":38,"CW":19,"FT8":13}


def endpoint():
    url = os.environ.get("WEATHER_DESK_VOACAP_URL", DEFAULT_ENDPOINT).strip()
    p = urlsplit(url)
    if p.username or p.password or p.fragment or p.query or not p.hostname:
        raise ValueError("Use a HamClock-compatible VOACAP endpoint without login details or query parameters.")
    if p.scheme != "https" and not (p.scheme == "http" and p.hostname in ("localhost","127.0.0.1","::1")):
        raise ValueError("Use HTTPS for a remote VOACAP endpoint, or HTTP on localhost.")
    return url


def request_url(context, now, url=None):
    date = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    prefs = context["preferences"]
    power = float(prefs["power"])
    if not power.is_integer() or power < 1:
        raise ValueError("VOACAP needs whole-watt transmit power (1–1500 W). KC2G supports fractional watts.")
    values = {"YEAR":date.year,"MONTH":date.month,"UTC":date.hour,"PATH":0,
              "TXLAT":context["txLat"],"TXLNG":context["txLon"],
              "RXLAT":round(context["rxLat"],2),"RXLNG":round(context["rxLon"],2),
              "POW":int(power),"MODE":MODE_CODES[prefs["mode"]],"TOA":3,
              "ANTDEDXCONTROL":0}
    return (url or endpoint()) + "?" + urlencode(values)


def parse(raw, now, context, source_url=DEFAULT_ENDPOINT):
    if isinstance(raw, bytes): raw = raw.decode("utf-8-sig")
    if not isinstance(raw, str) or len(raw) > 100_000:
        raise ValueError("VOACAP returned an unreadable forecast.")
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if len(lines) < 26:
        raise ValueError("VOACAP did not return a complete 24-hour calculation.")
    echo = re.fullmatch(r"(\d+)W,(SSB|CW|FT8),TOA>([\d.]+),SP,S=([\d.]+)", lines[1])
    prefs = context["preferences"]
    if not echo or echo[2] != prefs["mode"] or int(echo[1]) != int(prefs["power"]):
        raise ValueError("VOACAP returned a different mode or power. No substitute forecast is shown.")
    if not math.isclose(float(echo[3]), 3):
        raise ValueError("VOACAP returned different takeoff-angle assumptions.")
    hours = {}
    for line in lines[2:]:
        match = re.fullmatch(r"(\d{1,2})\s+(.+)", line)
        if not match: continue
        hour = int(match[1])
        if not 0 <= hour <= 23 or hour in hours:
            raise ValueError("VOACAP returned duplicate or invalid UTC hours.")
        try: values = [float(v) for v in match[2].split(",")]
        except ValueError: raise ValueError("VOACAP reliability values could not be read.") from None
        if len(values) != len(FREQUENCIES) or not all(math.isfinite(v) and 0 <= v <= 1 for v in values):
            raise ValueError("VOACAP returned an unsupported band layout or reliability range.")
        hours[hour] = values
    if set(hours) != set(range(24)):
        raise ValueError("VOACAP did not return all 24 UTC hours.")
    bands = [{"band":band,"mhz":freq,"supported":True,
              "hours":[{"bcr":round(hours[h][n]*100,5),"snr":None,"propagation":None} for h in range(24)]}
             for n,(band,freq) in enumerate(FREQUENCIES)]
    bands.append({"band":"160m","mhz":1.9,"supported":False,
                  "hours":[{"bcr":None,"snr":None,"propagation":None} for _ in range(24)]})
    date = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    return {"timestamp":now,"calculatedAt":now,"model":"voacap","modelMonth":date.strftime("%Y-%m"),
            "sunspotNumber":float(echo[4]),"bands":bands,"context":context,
            "sourceProvider":urlsplit(source_url).hostname,
            "method":"VOACAP monthly planning · HamClock-compatible service",
            "assumptions":"Short path; fixed 0 dBi isotropic antennas; minimum takeoff angle 3°. Backend noise and mode thresholds apply. Gain/noise controls in Path setup apply to KC2G.",
            "sourceUrl":"https://www.hamclock.com/credits.html" if source_url==DEFAULT_ENDPOINT else source_url}
