"""Synthetic fixtures exercise periods/QC. No synthetic observations ship as user data."""
import base64
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
import zipfile
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from uk_midas import parse_file,quality,MidasArchive
from uk_rain import parse_readings,totals,period,UKRain,stamp
from timezones import midnight

NAME='midas-open_uk-daily-temperature-obs_dv-202607_nottinghamshire_00556_nottingham-watnall_qcv-1_2020.csv'
TEMP_HEADER='ob_end_time,src_id,version_num,ob_hour_count,max_air_temp,min_air_temp,max_air_temp_q,min_air_temp_q'
RAIN_HEADER='ob_date,src_id,version_num,ob_end_ctime,ob_day_cnt,prcp_amt,prcp_amt_q,ob_day_cnt_q'
def temp(lines):return ('Conventions,G,BADC-CSV,1\nlocation_name,G,Nottingham Watnall\ndata\n'+TEMP_HEADER+'\n'+'\n'.join(lines)+'\nend data').encode()
def upload(name,raw):return {'name':name,'data':base64.b64encode(raw).decode()}
MEASURE='https://environment.data.gov.uk/hydrology/id/measures/test-rainfall-t-900-mm-qualified'
def rain_csv(lines):return 'measure,dateTime,value,quality,completeness,invalid,missing\n'+'\n'.join(MEASURE+','+v for v in lines)

class MidasTests(unittest.TestCase):
    def test_qc_uses_five_digits_not_only_zero(self):
        self.assertIsNotNone(quality('9'));self.assertIsNotNone(quality('609'))
        self.assertIsNone(quality('109'));self.assertIsNone(quality('1009'));self.assertIsNone(quality('20009'))
        self.assertTrue(quality('10009')['trace']);self.assertIsNone(quality(''))
    def test_only_version_one_and_exact_24h_ending_09(self):
        raw=temp(['2020-01-01 09:00:00,556,1,24,10,2,9,9',
                  '2020-01-01 21:00:00,556,1,12,20,4,9,9',
                  '2020-01-02 09:00:00,556,0,24,21,3,9,9',
                  '2020-01-03 09:00:00,99999,1,24,22,5,9,9'])
        values,names,rejected=parse_file(NAME,raw)
        self.assertEqual(len(values),2);self.assertEqual(values['556','2020-01-01','highC'][0],10)
        self.assertEqual(names['556'],'Nottingham Watnall');self.assertEqual(rejected,3)
    def test_qc_and_missing_values_never_become_zero(self):
        raw=temp(['2020-01-01 09:00:00,556,1,24,-999,2,9,9',
                  '2020-01-02 09:00:00,556,1,24,10,3,109,9'])
        values,_,_=parse_file(NAME,raw);self.assertEqual(len(values),2)
        self.assertNotIn(('556','2020-01-01','highC'),values)
    def test_rain_trace_multiday_and_non_0900(self):
        raw=(RAIN_HEADER+'\n2020-01-01,556,1,0900,1,0,10009,9\n2020-01-02,556,1,0900,2,15,9,9\n2020-01-03,556,1,1800,1,2,9,9').encode()
        values,_,rejected=parse_file('rain.csv',raw)
        self.assertEqual(len(values),1);self.assertTrue(values['556','2020-01-01','rainMm'][1]);self.assertEqual(rejected,2)
    def test_conflicting_duplicates_are_rejected(self):
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            parse_file(NAME,temp(['2020-01-01 09:00:00,556,1,24,10,2,9,9','2020-01-01 09:00:00,556,1,24,11,2,9,9']))
    def test_import_saved_idempotent_and_baseline_coverage(self):
        raw=temp([f'{year}-01-01 09:00:00,556,1,24,10,2,9,9' for year in range(1991,2015)])
        with tempfile.TemporaryDirectory() as folder:
            archive=MidasArchive(folder);archive.import_files({'files':[upload(NAME,raw)]})
            archive.import_files({'files':[upload(NAME,raw)]})
            self.assertEqual(archive.status()['stations'][0]['values'],48)
            again=MidasArchive(folder);view=again.view('556','2026-01-01')
            self.assertEqual(view['normal']['meanC'],6);self.assertEqual(view['records']['highC']['value'],10)
            self.assertIsNone(view['yearRainMm']);self.assertIn('ending 09:00',view['note'])
            self.assertIsNone(view['months'][0]['meanC'])
    def test_zip_import_and_atomic_invalid_batch(self):
        raw=temp(['2020-01-01 09:00:00,556,1,24,10,2,9,9']);buffer=io.BytesIO()
        with zipfile.ZipFile(buffer,'w') as z:z.writestr('nested/'+NAME,raw)
        with tempfile.TemporaryDirectory() as folder:
            archive=MidasArchive(folder);archive.import_files({'files':[upload('observations.zip',buffer.getvalue())]})
            self.assertEqual(len(archive.status()['stations']),1)
            with self.assertRaises(ValueError):archive.import_files({'files':[upload('bad.csv',b'not observations')]})
            self.assertEqual(archive.status()['stations'][0]['values'],2)
    def test_complete_month_averages_and_to_date_extremes(self):
        temperatures=temp([f'{y}-01-{d:02d} 09:00:00,556,1,24,10,2,9,9' for y in range(1991,2021) for d in range(1,32)])
        rains=(RAIN_HEADER+'\n'+'\n'.join(f'{y}-01-{d:02d},556,1,0900,1,1,9,9' for y in range(1991,2021) for d in range(1,32))).encode()
        with tempfile.TemporaryDirectory() as folder:
            archive=MidasArchive(folder);archive.import_files({'files':[upload(NAME,temperatures),upload('daily-rain.csv',rains)]})
            view=archive.view('556','2020-01-15');month=view['months'][0]
            self.assertEqual(month['normalMeanC'],6);self.assertEqual(month['normalRainMm'],31)
            self.assertEqual(month['rainMm'],31);self.assertEqual(view['yearRainMm'],15)
            self.assertEqual(view['yearRainWettest']['value'],15)
            self.assertEqual(month['wettest']['years'][-1],2019)

class RainTests(unittest.TestCase):
    def test_ea_gmt_units_qc_and_missing(self):
        rows=parse_readings(rain_csv(['2026-07-01T00:15:00,0.2,Unchecked,,,',
                                     '2026-07-01T00:30:00,0,Good,Complete,0,0',
                                     '2026-07-01T00:45:00,2,Suspect,,,']),MEASURE)
        self.assertEqual(rows[stamp('2026-07-01T00:15:00Z')][0],.2)
        self.assertIsNone(rows[stamp('2026-07-01T00:45:00Z')][0])
        value=period(rows,stamp('2026-07-01T00:00Z'),stamp('2026-07-01T01:00Z'))
        self.assertEqual(value['mm'],.2);self.assertEqual(value['missing'],2);self.assertFalse(value['complete'])
    def test_intervals_end_at_timestamp_and_boundaries_exclude_previous_day(self):
        start=stamp('2026-01-01T00:00Z');rows={start:(10,'Good'),start+900:(1,'Good')}
        self.assertEqual(period(rows,start,start+900)['mm'],1)
    def test_dst_days_have_92_and_100_intervals(self):
        for day,expected in [(dt.date(2026,3,29),92),(dt.date(2026,10,25),100)]:
            start=midnight(day,'Europe/London');end=midnight(day+dt.timedelta(days=1),'Europe/London')
            rows={t:(.2,'Good') for t in range(int(start)+900,int(end)+1,900)}
            result=period(rows,start,end);self.assertEqual(result['expected'],expected);self.assertTrue(result['complete'])
    def test_all_totals_share_calendar_windows_and_retain_gap(self):
        start=stamp('2026-01-01T00:00Z');end=start+86400
        rows={t:(.2,'Unchecked') for t in range(start+900,end+1,900)}
        del rows[start+1800]
        result=totals(rows,end+60)
        self.assertEqual(result['periods']['year']['missing'],1)
        self.assertEqual(result['periods']['year']['mm'],19)
        self.assertEqual(result['periods']['year']['mm'],result['months'][0]['mm'])
        self.assertIsNone(period(rows,end+900,end)['mm'])
    def test_bad_final_interval_is_still_a_gap_and_no_rate(self):
        start=stamp('2026-01-01T00:00Z');rows={start+900:(0,'Good'),start+1800:(None,'Suspect')}
        result=totals(rows,start+1801)
        self.assertEqual(result['timestamp'],start+1800);self.assertIsNone(result['rateMmHr'])
        self.assertEqual(result['periods']['year']['missing'],1)
    def test_cache_survives_restart_and_incremental_fetch(self):
        now=time.time();last=int(now//900)*900;first=last-900
        station={'items':[{'lat':52.77,'long':-1.56,'label':'Test gauge','measures':[{'period':900,'parameter':'rainfall','@id':MEASURE}]}]}
        csvdata=rain_csv([dt.datetime.fromtimestamp(t,dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')+',0.2,Unchecked,,,' for t in (first,last)])
        with tempfile.TemporaryDirectory() as folder:
            fetch=Mock(return_value=station);service=UKRain(folder,fetch,Mock(return_value=csvdata))
            service._refresh('52.7700,-1.5600',52.77,-1.56)
            self.assertEqual(service.states['52.7700,-1.5600']['stationName'],'Test gauge')
            again=UKRain(folder,fetch,Mock(return_value=csvdata))
            again.attempts['52.7700,-1.5600']=time.time()
            value=again.snapshot({'latitude':52.77,'longitude':-1.56})
            self.assertEqual(value['stationName'],'Test gauge');self.assertIn('periods',value)

if __name__=='__main__':unittest.main()
