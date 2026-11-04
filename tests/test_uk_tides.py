import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from uk_tides import UKTides,candidates,valid_reading,official_uri,BASE
NOW=1792893600
TS=dt.datetime.fromtimestamp(NOW,dt.timezone.utc).isoformat()
SID=BASE+'/id/stations/E70139-anglian'
MID=BASE+'/id/measures/E70139-level-tidal_level-Mean-15_min-mAOD'
STATIONS=[{'@id':SID.replace('https:','http:'),'stationReference':'E70139','notation':'E70139-anglian','label':'Liverpool','lat':53.449,'long':-3.018}]
MEASURES=[{'@id':MID,'station':SID,'unitName':'mAOD','qualifier':'Tidal Level','latestReading':{'dateTime':TS,'value':.726}}]

class TidesTests(unittest.TestCase):
    def test_exact_uri_and_datum_selection(self):
        r=candidates(STATIONS,MEASURES,52.77,-1.56,NOW)
        self.assertEqual(r[0]['stationUrl'],SID);self.assertEqual(r[0]['stationId'],'E70139-anglian')
        self.assertIsNone(official_uri('https://example.com/id/stations/A','stations'))
        self.assertIsNone(official_uri(BASE+'/id/stations/A?secret=1','measures'))
    def test_stale_and_other_datum_excluded(self):
        measures=[{**MEASURES[0],'unitName':'m'},{**MEASURES[0],'latestReading':{'dateTime':'2026-01-01T00:00:00Z','value':1}}]
        self.assertEqual(candidates(STATIONS,measures,52.77,-1.56,NOW),[])
    def test_invalid_and_suspect_not_zero(self):
        for value in [None,True,float('nan'),-999]:self.assertIsNone(valid_reading({'dateTime':TS,'value':value},NOW))
        self.assertIsNone(valid_reading({'dateTime':TS,'value':1,'quality':'Suspect'},NOW))
        self.assertEqual(valid_reading({'dateTime':TS,'value':-2},NOW)['heightM'],-2)
    def test_nearest_reporting_port_curve_and_cache(self):
        rows=[{'dateTime':dt.datetime.fromtimestamp(NOW-i*900,dt.timezone.utc).isoformat(),'value':.726-i*.1} for i in range(96)]
        def fetch(url):
            if '/id/stations?' in url:return {'items':STATIONS}
            if '/id/measures?' in url:return {'items':MEASURES}
            self.assertTrue(url.startswith(MID+'/readings?'));return {'items':rows}
        with tempfile.TemporaryDirectory() as directory,patch('uk_tides.time.time',return_value=NOW),patch('uk_tides.threading.Thread'):
            p=UKTides(directory,fetch);settings={'latitude':52.77,'longitude':-1.56}
            p.snapshot(settings);key=next(iter(p.states));state=p.states[key]
            p._load(key,settings,state);r=p.snapshot(settings)
            self.assertEqual(r['stationName'],'Liverpool');self.assertEqual(len(r['observations']),96)
            self.assertFalse(r['stale']);self.assertTrue(r['observed']);self.assertNotIn('predictions',r)
            q=UKTides(directory,Mock());self.assertEqual(q.snapshot(settings)['latest']['heightM'],.726)
    def test_nonblocking_first_request(self):
        with tempfile.TemporaryDirectory() as directory,patch('uk_tides.threading.Thread'):
            fetch=Mock();p=UKTides(directory,fetch)
            self.assertTrue(p.snapshot({'latitude':52.77,'longitude':-1.56})['loading']);fetch.assert_not_called()

if __name__=='__main__':unittest.main()
