"""Provider boundaries: units, QC, nearest fresh site, fallback and persistence."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cwop_weather import PreferredPublicWeather, parse_reports, query_url, MAX_AGE
from server import feels_like, pressure_trend, Desk

NOW=1792893600

def xml(reports):
    elements=[]
    for sid,lat,lon,age,values in reports:
        stamp=dt.datetime.fromtimestamp(NOW-age,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
        for var,value in values.items():
            val,qc=value if isinstance(value,tuple) else (value,'S')
            elements.append(f'<record var="V-{var}" shef_id="{sid}" lat="{lat}" lon="{lon}" ObTime="{stamp}" provider="APRSWXNET" data_value="{val}" QCD="{qc}"/>')
    return '<mesonet>'+''.join(elements)+'</mesonet>'

class CWOPTests(unittest.TestCase):
    def parse(self, rows):return parse_reports(xml(rows),52.77,-1.56,feels_like,now=NOW)
    def test_nearest_fresh_temperature_wins(self):
        rows=self.parse([('old',52.77,-1.56,6000,{'T':280}),('noTemp',52.77,-1.56,0,{'RH':80}),
                         ('close',52.78,-1.56,600,{'T':281}),('far',52.9,-1.56,0,{'T':282})])
        self.assertEqual([r['stationId'] for r in rows],['close','far'])
    def test_filter_qc_per_sensor_and_ranges(self):
        r=self.parse([('A',52.77,-1.56,0,{'T':(280,'V'),'RH':(90,'Q'),'FF':(-1,'S'),
                                             'TD':('NaN','S'),'DD':(200,'B'),'FFGUST':(5,'C')})])[0]['current']
        self.assertIsNone(r['humidity']);self.assertIsNone(r['windMph']);self.assertIsNone(r['dewF']);self.assertIsNone(r['windDeg'])
        self.assertAlmostEqual(r['gustMph'],11.1846814605)
    def test_units_and_pressure_datum(self):
        r=self.parse([('A',52.77,-1.56,0,{'T':273.15,'TD':270,'RH':80,'FF':10,'ALTSE':101325,'PCP1H':(.001,'C'),'PCP24H':(.02,'C')})])[0]['current']
        self.assertAlmostEqual(r['tempF'],32);self.assertAlmostEqual(r['windMph'],22.369362921)
        self.assertEqual(r['pressureHpa'],1013.25);self.assertEqual(r['pressureKind'],'QNH')
        self.assertEqual(r['rainLast24hMm'],20);self.assertEqual(r['rainLastHourMm'],1)
        for key in ['rainDayMm','rainYearMm','rainMonthMm','rainRateMmHr','maxGustMph','solar']:self.assertIsNone(r[key])
    def test_slp_preferred_over_altimeter(self):
        r=self.parse([('A',52.77,-1.56,0,{'T':280,'SLP':100800,'ALTSE':101000})])[0]['current']
        self.assertEqual(r['pressureKind'],'sea-level');self.assertEqual(r['pressureHpa'],1008)
    def test_unchecked_temperature_rejected_and_metadata_preserved(self):
        rows=self.parse([('bad',52.77,-1.56,0,{'T':(280,'Z')}),('good',52.77,-1.56,0,{'T':280,'STALOC':(' G4YTK  Staffordshire UK ','Z')})])
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['stationName'],'G4YTK Staffordshire UK')
    def test_timestamps_separate_latest_for_station(self):
        r=self.parse([('A',52.77,-1.56,300,{'T':280,'RH':80}),('A',52.77,-1.56,0,{'T':281})])[0]['current']
        self.assertAlmostEqual(r['tempF'],46.13);self.assertIsNone(r['humidity'])
    def test_out_of_radius_and_future_reports_rejected(self):
        self.assertEqual(self.parse([('A',50,-1.56,0,{'T':280}),('B',52.77,-1.56,-600,{'T':280})]),[])
    def test_query_is_geographic_cwop_only(self):
        url=query_url(42.46,-70.95)
        self.assertIn('pvd=APRSWXNET',url);self.assertIn('nvars=PCP24H',url);self.assertIn('xml=1',url);self.assertIn('recwin=3',url)
    def test_entities_not_supported(self):
        with self.assertRaises(ValueError):parse_reports('<!DOCTYPE test><mesonet/>',0,0,feels_like,NOW)
    def test_current_persisted_and_no_new_request_after_restart(self):
        settings={'latitude':52.77,'longitude':-1.56,'weatherSource':'auto'}
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.time.time',return_value=NOW):
            fetch=Mock(return_value=xml([('A',52.77,-1.56,0,{'T':280,'ALTSE':101000,'PCP1H':0})]))
            p=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend,fetch)
            key,state=p._state(settings);p._load(key,settings,state);state['checked']=time.monotonic()
            self.assertEqual(p.snapshot(settings)['source'],'cwop')
            q=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend,Mock())
            self.assertEqual(q.snapshot(settings)['stationId'],'A');q.fetch.assert_not_called()
            self.assertEqual(len(q.history(settings)['points']),1)
    def test_switching_station_does_not_mix_history(self):
        settings={'latitude':52.77,'longitude':-1.56}
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.time.time',return_value=NOW):
            p=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend,Mock(return_value=xml([('B',52.77,-1.56,0,{'T':280,'PCP1H':0})])))
            key,state=p._state(settings);state['payload']={'stationId':'A','points':[{'timestamp':NOW-10800,'pressureHpa':900}]}
            p._load(key,settings,state);self.assertEqual(len(state['payload']['points']),1)
    def test_official_fallback_and_explicit_official_preference(self):
        settings={'latitude':52.77,'longitude':-1.56,'weatherSource':'auto'}
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.time.time',return_value=NOW),patch('cwop_weather.threading.Thread'):
            p=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend)
            key,state=p._state(settings);state['checked']=state['officialChecked']=time.monotonic()
            state['official']={'source':'metar','current':{'timestamp':NOW,'tempF':50,'rainLastHourMm':0}}
            self.assertEqual(p.snapshot(settings)['source'],'metar')
            state['payload']={'source':'cwop','current':{'timestamp':NOW,'tempF':50,'rainLastHourMm':0}}
            self.assertEqual(p.snapshot(settings)['source'],'cwop')
            settings['weatherSource']='noaa';self.assertEqual(p.snapshot(settings)['source'],'metar')
    def test_old_cwop_uses_fresh_official_and_network_failure_backs_off(self):
        settings={'latitude':52.77,'longitude':-1.56}
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.time.time',return_value=NOW),patch('cwop_weather.threading.Thread') as thread:
            p=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend,Mock(side_effect=TimeoutError()))
            key,state=p._state(settings)
            state['payload']={'source':'cwop','current':{'timestamp':NOW-MAX_AGE-1,'tempF':50,'rainLastHourMm':0}}
            state['official']={'source':'metar','current':{'timestamp':NOW,'tempF':50,'rainLastHourMm':0}}
            state['checked']=state['officialChecked']=time.monotonic()
            p._load(key,settings,state)
            self.assertEqual(p.snapshot(settings)['source'],'metar')
            self.assertEqual(state['failures'],1);self.assertIn('MADIS',state['error']);thread.assert_not_called()
    def test_pressure_datum_change_does_not_create_false_trend(self):
        settings={'latitude':52.77,'longitude':-1.56}
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.time.time',return_value=NOW):
            p=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend,Mock(return_value=xml([('A',52.77,-1.56,0,{'T':280,'ALTSE':101000,'PCP1H':0})])))
            key,state=p._state(settings)
            state['payload']={'stationId':'A','points':[{'timestamp':NOW-10800,'pressureHpa':1000,'pressureKind':'sea-level'}]}
            p._load(key,settings,state)
            self.assertEqual(len(state['payload']['points']),1);self.assertIsNone(state['payload']['trend']['delta'])
    def test_initial_search_does_not_block_or_call_official_synchronously(self):
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.threading.Thread'):
            official=Mock();p=PreferredPublicWeather(directory,official,feels_like,pressure_trend)
            r=p.snapshot({'latitude':52.77,'longitude':-1.56})
            self.assertTrue(r['loading']);self.assertTrue(r['publicSearchLoading']);official.snapshot.assert_not_called()
    def test_first_lookup_runs_immediately_after_pc_boot(self):
        with tempfile.TemporaryDirectory() as directory,patch('cwop_weather.time.monotonic',return_value=20),patch('cwop_weather.threading.Thread') as thread:
            p=PreferredPublicWeather(directory,Mock(),feels_like,pressure_trend)
            self.assertTrue(p.snapshot({'latitude':52.77,'longitude':-1.56})['loading'])
            self.assertEqual(thread.call_count,2)
    def test_settings_preserve_own_station_priority_and_allow_cwop(self):
        with tempfile.TemporaryDirectory() as directory:
            d=Desk(directory);d.api_key='api';d.app_key='app';self.assertEqual(d.weather_mode(),'ambient')
            d.settings_update({'weatherSource':'cwop'});self.assertEqual(d.weather_mode(),'noaa')
            d.settings_update({'weatherSource':'auto'});self.assertEqual(d.weather_mode(),'ambient');d.db.close()

if __name__=='__main__':unittest.main()
