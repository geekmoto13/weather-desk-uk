"""Live response shapes, correct path/provider identity and paced reception queries."""
import copy
import datetime as dt
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs,urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import radio_space as r
import radio_voacap as v
from radio_reports import ReceptionReports, parse_reports, callsign, default_callsign

NOW=dt.datetime(2026,11,8,0,30,tzinfo=dt.timezone.utc).timestamp()

def context(engine="voacap",**prefs):
    return r.path_context({"latitude":0,"longitude":0},r.clean_preferences({"target":"japan","engine":engine,**prefs}))

def wire(mode="SSB",power=100):
    values="0.10,0.20,0.30,0.40,0.50,0.60,0.70,0.80,0.90"
    return values+f"\n{power}W,{mode},TOA>3,SP,S=93\n"+"\n".join(f"{h} {values}" for h in [*range(1,24),0])+"\n"

def path25():
    return {"bands":[[b,f] for b,f in zip(r.BANDS,[28.85,24.94,21.225,18.118,14.175,10.12,7.15,5.33,3.65,1.9])],
            "table":[[{"bcr":70,"pr":-130,"noise":-145,"pop":90} for _ in range(24)]+[{"bcr":5,"pr":-160,"noise":-145,"pop":10}] for _ in r.BANDS]}

def xml(call="N1DQ",**attrs):
    a={"senderCallsign":call,"receiverCallsign":"JA1ABC","receiverLocator":"PM95", "flowStartSeconds":str(NOW-30),"frequency":"14074000","mode":"FT8","sNR":"-12",**attrs}
    return "<receptionReports><receptionReport "+" ".join(f'{k}="{val}"' for k,val in a.items())+" /></receptionReports>"

def wait_thread(thread):
    if thread:thread.join(timeout=3)
    assert not thread or not thread.is_alive()

class ModelAndDestinationTests(unittest.TestCase):
    def test_live_kc2g_25_columns_are_accepted_without_shifting_utc_hours(self):
        result=r.parse_path(path25(),NOW,context("kc2g"))
        self.assertEqual(len(result["bands"]),10)
        self.assertTrue(all(len(b["hours"])==24 for b in result["bands"]))
        self.assertEqual(result["bands"][0]["hours"][0]["bcr"],70)
        self.assertEqual(result["bands"][0]["hours"][23]["bcr"],70)
        self.assertEqual(result["bands"][0]["hours"][0]["snr"],15)

    def test_kc2g_rejects_a_different_returned_model_generation(self):
        raw=path25();raw["run_info"]={"run_id":2}
        fetch=Mock(side_effect=[{"run_id":1,"target_time":NOW},raw,{"run_id":1,"target_time":NOW}])
        with tempfile.TemporaryDirectory() as folder:
            desk=r.RadioDesk(folder,fetch)
            with self.assertRaisesRegex(ValueError,"generation changed"):desk._path(context("kc2g"),NOW)

    def test_named_destinations_never_require_an_unused_grid(self):
        for target in ["japan","australia","uk","dxcc:DL","dxcc:SP","dxcc:UA","dxcc:UA9"]:
            prefs=r.clean_preferences({"target":target,"customGrid":""})
            c=r.path_context({"latitude":0,"longitude":0},prefs)
            self.assertTrue(math.isfinite(c["rxLat"]) and math.isfinite(c["rxLon"]))
        with self.assertRaises(ValueError):r.clean_preferences({"target":"custom","customGrid":""})
        with self.assertRaises(ValueError):r.clean_preferences({"target":"custom","customGrid":"../../"})

    def test_catalogue_has_current_340_entities_and_valid_regional_points(self):
        data=r.DESTINATION_CATALOG
        self.assertEqual(len(data["entities"]),340)
        self.assertGreaterEqual(len(data["cities"]),500)
        all_points=data["entities"]+data["cities"]
        self.assertEqual(len({p["id"] for p in all_points}),len(all_points))
        self.assertTrue(all(-90<=p["latitude"]<=90 and -180<=p["longitude"]<=180 for p in all_points))

    def test_noaa_current_and_24h_maximums_remain_separate(self):
        raw={"0":{"DateStamp":"2026-11-08","TimeStamp":"00:30:00","R":{"Scale":"0"},"S":{"Scale":"0"},"G":{"Scale":"0"}},
             "-1":{"R":{"Scale":"1"},"S":{"Scale":None},"G":{"Scale":"2"}}}
        result=r.scales(raw,NOW)
        self.assertEqual(result["levels"],{"R":0,"S":0,"G":0})
        self.assertEqual(result["maximum24h"],{"R":1,"S":None,"G":2})

    def test_xray_plot_keeps_both_channels_without_changing_current_long_flux(self):
        iso=lambda t:dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat()
        raw=[{"time_tag":iso(NOW-60),"energy":"0.1-0.8nm","flux":1e-6},
             {"time_tag":iso(NOW-30),"energy":"0.05-0.4nm","flux":2e-7},
             {"time_tag":iso(NOW),"energy":"0.1-0.8nm","flux":2e-3,"electron_contamination":True}]
        result=r.xray(raw,NOW)
        self.assertEqual(result["flux"],1e-6)
        self.assertEqual([len(result["series"][k]) for k in ["long","short"]],[1,1])

    def test_proton_channels_keep_missing_and_zero_distinct(self):
        iso=dt.datetime.fromtimestamp(NOW,dt.timezone.utc).isoformat()
        result=r.protons([{"time_tag":iso,"energy":">=10 MeV","flux":0},
                          {"time_tag":iso,"energy":">=50 MeV","flux":-9999},
                          {"time_tag":iso,"energy":">=100 MeV","flux":.01}],NOW)
        self.assertEqual(result["series"]["10"][0]["value"],0)
        self.assertEqual(result["series"]["50"],[])

class VoacapTests(unittest.TestCase):
    def test_all_24_utc_hours_map_to_the_current_nine_band_protocol(self):
        result=v.parse(wire(),NOW,context())
        by_band={b["band"]:b for b in result["bands"]}
        self.assertEqual(result["model"],"voacap")
        self.assertEqual(result["modelMonth"],"2026-11")
        self.assertEqual(by_band["60m"]["mhz"],5.36)
        self.assertEqual(by_band["60m"]["hours"][0]["bcr"],20)
        self.assertEqual(by_band["10m"]["hours"][23]["bcr"],90)
        self.assertIsNone(by_band["160m"]["hours"][0]["bcr"])
        self.assertIsNone(by_band["20m"]["hours"][0]["snr"])
        self.assertFalse(by_band["160m"]["supported"])

    def test_partial_duplicate_bad_range_and_wrong_settings_are_withheld(self):
        valid=wire()
        for raw in [valid.replace("23 0.10", "22 0.10"),valid.replace("0.90", "1.90"),
                    valid.replace("0.90", "nan"),wire("CW"),wire(power=50),"Service is unavailable",valid[:250]]:
            with self.subTest(raw=raw[:80]),self.assertRaises(ValueError):v.parse(raw,NOW,context())

    def test_request_uses_utc_mode_power_and_only_approximate_path_parameters(self):
        c=context(mode="FT8",power=50);c["apiKey"]="never-send-this"
        q=parse_qs(urlsplit(v.request_url(c,NOW)).query)
        self.assertEqual(q["MODE"],["13"]);self.assertEqual(q["POW"],["50"])
        self.assertEqual(q["MONTH"],["11"]);self.assertEqual(q["UTC"],["0"])
        self.assertNotIn("never-send-this",str(q))
        self.assertEqual(q["ANTDEDXCONTROL"],["0"])
        for power in [.1, 1.5]:
            with self.assertRaisesRegex(ValueError, "whole-watt"):
                r.clean_preferences({"engine":"voacap", "power":power})
        self.assertEqual(r.clean_preferences({"engine":"kc2g", "power":.1})["power"], .1)

    def test_endpoint_allows_self_hosting_without_credentials_in_urls(self):
        for url in ["http://example.com/voacap","https://name:password@example.com/path","file:///tmp/test"]:
            with patch.dict(os.environ,{"WEATHER_DESK_VOACAP_URL":url}),self.assertRaises(ValueError):v.endpoint()
        with patch.dict(os.environ,{"WEATHER_DESK_VOACAP_URL":"http://127.0.0.1:8080/fetchBandConditions"}):
            self.assertIn("127.0.0.1",v.endpoint())

    def test_voacap_and_kc2g_results_are_cached_separately_and_reused(self):
        weather={"latitude":0,"longitude":0}
        with tempfile.TemporaryDirectory() as folder,patch("radio_space.time.time",return_value=NOW):
            desk=r.RadioDesk(folder,Mock());desk.feeds={}
            def load(c,n):return v.parse(wire(),n,c) if c["preferences"]["engine"]=="voacap" else r.parse_path(path25(),n,c)
            desk._path=Mock(side_effect=load)
            desk.snapshot(weather)
            for feed in desk.paths.values():wait_thread(feed.thread)
            desk.update({"engine":"voacap"});desk.snapshot(weather)
            for feed in desk.paths.values():wait_thread(feed.thread)
            self.assertEqual(desk.snapshot(weather)["path"]["value"]["model"],"voacap")
            desk.update({"engine":"kc2g"})
            self.assertEqual(desk.snapshot(weather)["path"]["value"]["model"],"kc2g")
            self.assertEqual(desk._path.call_count,2)
            restarted=r.RadioDesk(folder,Mock(side_effect=AssertionError("No restart fetch needed")));restarted.feeds={}
            self.assertEqual(restarted.snapshot(weather)["path"]["value"]["model"],"kc2g")

class ReceptionTests(unittest.TestCase):
    def test_call_defaults_and_portable_calls_are_validated(self):
        self.assertEqual(callsign("m0oht/p"),"M0OHT/P")
        self.assertEqual(default_callsign("KC1OHT / M0OHT"),"KC1OHT")
        for text in ["not-a-call","https://example.com","<script>","123456",""]:self.assertEqual(callsign(text),"")

    def test_reports_match_call_have_real_times_and_are_not_duplicated(self):
        raw=xml().replace("</receptionReports>",xml().split(">",1)[1])
        result=parse_reports(raw,"N1DQ",NOW)
        self.assertEqual(len(result["reports"]),1)
        report=result["reports"][0]
        self.assertEqual((report["band"],report["snr"],report["receiver"]),("20m",-12,"JA1ABC"))
        for raw in [xml("N2DQ"),xml(flowStartSeconds=NOW-4000),xml(flowStartSeconds=NOW+600),xml(frequency="NaN")]:
            self.assertEqual(parse_reports(raw,"N1DQ",NOW)["reports"],[])

    def test_service_errors_and_dtds_cannot_look_like_no_reports(self):
        for raw in ["<html>Access denied</html>","<receptionReports><error>limit</error></receptionReports>","<!DOCTYPE x><receptionReports/>"]:
            with self.assertRaises(ValueError):parse_reports(raw,"N1DQ",NOW)

    def test_queries_are_paced_across_calls_and_restarts_without_wrong_call_results(self):
        with tempfile.TemporaryDirectory() as folder,patch("radio_reports.time.time",return_value=NOW):
            fetch=Mock(return_value=xml())
            reports=ReceptionReports(folder,fetch);reports.snapshot("N1DQ",NOW);wait_thread(reports.thread)
            self.assertEqual(reports.snapshot("N1DQ",NOW+10)["value"]["receiverCount"],1)
            other=reports.snapshot("N2DQ",NOW+20)
            self.assertIsNone(other["value"]);self.assertEqual(fetch.call_count,1)
            restart=ReceptionReports(folder,fetch);restart.snapshot("N1DQ",NOW+30)
            self.assertEqual(fetch.call_count,1)
            q=parse_qs(urlsplit(fetch.call_args.args[0]).query)
            self.assertEqual(q["senderCallsign"],["N1DQ"])
            self.assertNotIn("latitude",q);self.assertEqual(q["rptlimit"],["100"])

    def test_failed_request_does_not_retry_on_every_page_poll(self):
        with tempfile.TemporaryDirectory() as folder,patch("radio_reports.time.time",return_value=NOW):
            fetch=Mock(side_effect=OSError("offline"));reports=ReceptionReports(folder,fetch)
            reports.snapshot("N1DQ",NOW);wait_thread(reports.thread)
            result=reports.snapshot("N1DQ",NOW+10)
            self.assertTrue(result["stale"]);self.assertTrue(result["error"])
            self.assertIsNone(result["value"]);self.assertEqual(fetch.call_count,1)

if __name__=="__main__":unittest.main()
