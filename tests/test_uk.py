import datetime as dt
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfoNotFoundError
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server,timezones
from uk_climate import UKArchive,DEFAULT_CLIMATE,parse_daily,references
from uk_location import LocationResolver
from uk_sources import UKForecast,UKPublicWeather,UKWarnings,RadarData,parse_warnings,contains,metar_reading
from uk_monthly import parse_monthly,summarize

SID='UKE00105906'
def dly(field,values):
    cells=[]
    for day in range(1,32):
        value,m,q=values.get(day,(-9999,' ',' '));cells.append(f'{value:5d}{m}{q}E')
    return SID+'202001'+field+''.join(cells)

class UKClimateTests(unittest.TestCase):
    def test_daily_units_zero_trace_qc_and_presumed_zero(self):
        raw='\n'.join([dly('TMAX',{1:(0,' ',' '),2:(200,' ','X')}),
                       dly('PRCP',{1:(10,'B',' '),2:(0,'T',' '),3:(0,'P',' '),4:(24,'D',' ')})])
        days=parse_daily(raw,SID)
        self.assertEqual(days[dt.date(2020,1,1)]['TMAX'],32)
        self.assertNotIn('TMAX',days[dt.date(2020,1,2)])
        self.assertAlmostEqual(days[dt.date(2020,1,1)]['PRCP'],1/25.4)
        self.assertEqual(days[dt.date(2020,1,2)]['PRCP'],'T')
        self.assertNotIn(dt.date(2020,1,3),days)
        self.assertAlmostEqual(days[dt.date(2020,1,4)]['PRCP'],2.4/25.4)
    def test_baseline_requires_24_ordinary_and_7_leap_years(self):
        days={dt.date(y,1,1):{'TMAX':50} for y in range(1991,2015)}
        days.update({dt.date(y,2,29):{'TMAX':40} for y in range(1992,2021,4)})
        self.assertEqual(references(days)[1,1,'TMAX'],50)
        self.assertEqual(references(days)[2,29,'TMAX'],40)
        del days[dt.date(1991,1,1)]
        del days[dt.date(1992,2,29)];del days[dt.date(1996,2,29)]
        self.assertNotIn((1,1,'TMAX'),references(days));self.assertNotIn((2,29,'TMAX'),references(days))
    def test_public_seed_and_restart_need_no_history_import(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch=Mock(side_effect=RuntimeError('offline'))
            first=UKArchive(folder,fetch);data=first.load(SID)
            self.assertEqual(data['through'],'2026-09-30');self.assertGreater(len(data['normal']),1000)
            second=UKArchive(folder,fetch);self.assertEqual(second.load(SID)['normal'],data['normal']);fetch.assert_not_called()
    def test_climate_ledger_uses_matching_to_date_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            desk=server.Desk(folder)
            value=desk.climate.get(dt.date(2026,10,24));rain=desk.rain_records.get(dt.date(2026,10,24))
            self.assertEqual(value['normal']['through'],'2026-10-24')
            self.assertEqual(rain['through'],'2026-10-24');self.assertEqual(rain['throughYear'],2025)
            self.assertEqual(value['source'],'NOAA NCEI GHCN-Daily · UK observations')
            self.assertIn('calculated',value['normalPeriod']);self.assertEqual(value['archiveThrough'],'2026-09-30')
            self.assertIsNotNone(rain['month']['wettest']);desk.db.close()
    def test_monthly_archive_marks_estimates_and_never_invents_missing(self):
        rows=parse_monthly('Sutton Bonington\n2026 9 19.2* 10.8 0 45.1 120.0# Provisional\n2026 10 --- --- --- --- ---')
        self.assertTrue(rows[0]['estimated']);self.assertTrue(rows[0]['provisional']);self.assertEqual(rows[0]['meanC'],15)
        self.assertIsNone(rows[1]['rainMm']);self.assertIsNone(summarize(rows,2026)[9]['rainMm'])

class UKLocationTests(unittest.TestCase):
    def test_full_postcode_and_town_country_filter(self):
        fetch=Mock(return_value={'result':{'postcode':'SW1A 1AA','latitude':51.501,'longitude':-.142,'country':'England'}})
        resolver=LocationResolver(fetch,None);self.assertEqual(resolver.search('sw1a 1aa')['matches'][0]['latitude'],51.501)
        self.assertIn('/SW1A1AA',fetch.call_args.args[0])
        fetch.return_value={'results':[{'name':'Swadlincote','country_code':'GB','latitude':52.77,'longitude':-1.56},{'name':'Other','country_code':'US','latitude':40,'longitude':-75}]}
        self.assertEqual(len(resolver.search('Swadlincote')['matches']),1)
    def test_grid_and_nearest_audited_reference(self):
        resolver=LocationResolver(Mock(),None)
        self.assertEqual(resolver.search('IO92')['matches'][0]['latitude'],52.5)
        selected=resolver.resolve(52.77,-1.56,'Swadlincote')
        self.assertEqual(selected['climateStation']['id'],SID);self.assertEqual(selected['timezone'],'Europe/London')
        with self.assertRaises(ValueError):resolver.resolve(42.4668,-70.9495)
    def test_uk_setting_profile_survives_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            desk=server.Desk(folder);desk.settings_update({'location':'Swadlincote','timezone':'Europe/London','title':'UK test'})
            desk.db.close();again=server.Desk(folder)
            self.assertEqual(again.settings['title'],'UK test');self.assertEqual(again.settings['climateStation']['id'],SID);again.db.close()

class UKFeedTests(unittest.TestCase):
    def test_real_metar_units_qnh_and_missing_counters(self):
        row={'temp':12,'dewp':9,'wspd':26,'wgst':38,'wdir':'VRB','altim':984,'obsTime':1700000000}
        current=metar_reading(row,server.feels_like)
        self.assertAlmostEqual(current['tempF'],53.6);self.assertAlmostEqual(current['windMph'],29.920265648)
        self.assertEqual(current['pressureHpa'],984);self.assertEqual(current['pressureKind'],'QNH')
        self.assertIsNone(current['windDeg']);self.assertIsNone(current['maxGustMph']);self.assertIsNone(current['rainYearMm'])
    def test_nearest_reporting_airport_and_cache(self):
        now=time.time();rows=[{'icaoId':'EGNX','name':'East Midlands','lat':52.833,'lon':-1.333,'temp':12,'precip':0,'obsTime':now-600},
                             {'icaoId':'EGBB','name':'Birmingham','lat':52.45,'lon':-1.75,'temp':14,'obsTime':now-300}]
        fetch=Mock(return_value=rows)
        with tempfile.TemporaryDirectory() as folder:
            public=UKPublicWeather(folder,fetch,None,server.feels_like,server.pressure_trend)
            value=public.snapshot({'latitude':52.77,'longitude':-1.56});again=public.snapshot({'latitude':52.77,'longitude':-1.56})
            self.assertEqual(value['stationId'],'EGNX');self.assertEqual(value['source'],'metar');self.assertFalse(value['stale']);self.assertEqual(fetch.call_count,1)
            self.assertEqual(again['current']['timestamp'],now-600)
    def test_forecast_uses_utc_epochs_and_recognized_weather_codes(self):
        now=time.time();start=int(now//3600*3600)
        raw={'hourly':{'time':[start+i*3600 for i in range(25)],'weather_code':[3]*25,'precipitation_probability':[10]*25}}
        fetch=Mock(return_value=raw);value=UKForecast(fetch).get(52.77,-1.56)
        self.assertEqual(value['periods'][0]['summary'],'Overcast');self.assertEqual(value['timeBasis'],'retrieved')
        self.assertIn('models=icon_seamless',fetch.call_args.args[0]);self.assertIn('timeformat=unixtime',fetch.call_args.args[0])
    def test_radar_uses_manifest_paths_and_only_past_six_frames(self):
        now=time.time();past=[{'time':now-600*i,'path':f'/v2/radar/hash{i}'} for i in range(13)]
        value=RadarData(Mock(return_value={'host':'https://tilecache.rainviewer.com','radar':{'past':past,'nowcast':[{'time':now+600}]}})).get()
        self.assertEqual(len(value['frames']),6);self.assertTrue(value['frames'][-1]['path'].startswith('/v2/radar/hash'))
        self.assertLessEqual(value['frames'][-1]['time'],now)
    def test_warning_polygon_holes_multipolygons_and_expiry(self):
        geometry={'type':'Polygon','coordinates':[[[-2,52],[0,52],[0,54],[-2,54],[-2,52]],[[-1.5,52.5],[-.5,52.5],[-.5,53.5],[-1.5,53.5],[-1.5,52.5]]]}
        self.assertTrue(contains(geometry,52.2,-1.8));self.assertFalse(contains(geometry,53,-1))
        now=time.time();raw={'type':'FeatureCollection','features':[{'geometry':geometry,'properties':{'validfromdate':(now-100)*1000,'validtodate':(now+300)*1000,'issuedDate':(now-200)*1000,'warninglevel':'YELLOW','weathertype':'WIND','GlobalID':'test','warningheadline':'Original headline'}}]}
        self.assertEqual(parse_warnings(raw,52.2,-1.8,now)[0]['headline'],'Original headline')
        self.assertEqual(parse_warnings(raw,53,-1,now),[]);self.assertEqual(parse_warnings(raw,52.2,-1.8,now+301),[])
    def test_outage_is_not_no_warning_and_stale_operational_content_hides(self):
        warnings=UKWarnings(Mock(side_effect=ValueError('offline')))
        value=warnings.get(52.77,-1.56);self.assertTrue(value['stale']);self.assertTrue(value['error'])
        warnings.saved[(52.77,-1.56)]={'checkedAt':time.time()-181,'alerts':[{'id':'old'}]}
        self.assertEqual(warnings.get(52.77,-1.56)['alerts'],[])

class UKClockTests(unittest.TestCase):
    def test_windows_fallback_gmt_bst_and_23_25_hour_days(self):
        with patch.object(timezones,'ZoneInfo',side_effect=ZoneInfoNotFoundError):
            spring=dt.date(2026,3,29);autumn=dt.date(2026,10,25)
            self.assertEqual(timezones.midnight(spring+dt.timedelta(days=1),'Europe/London')-timezones.midnight(spring,'Europe/London'),23*3600)
            self.assertEqual(timezones.midnight(autumn+dt.timedelta(days=1),'Europe/London')-timezones.midnight(autumn,'Europe/London'),25*3600)
            stamp=dt.datetime(2026,10,24,23,30,tzinfo=dt.timezone.utc).timestamp()
            self.assertEqual(timezones.local_date(stamp,'Europe/London'),autumn)

if __name__=='__main__':unittest.main()
