"""NOWData matching MTD/YTD windows, trace amounts and daily cache rollover."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from urllib.parse import parse_qs,urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from climate_rain import rainfall_records,RainfallRecords
TODAY=dt.date(2026,10,22)

def raw_records():
    return {'meta':{'name':'Boston Area'},'data':[
        ['1947',[['T',0]],[['27.70',0]]],['1958',[['0.90',0]],[['52.80',0]]],
        ['1965',[['1.57',0]],[['19.88',0]]],['1996',[['10.29',0]],[['44.26',0]]]]}

class RainRecordTests(unittest.TestCase):
    def test_nowdata_october_22_screenshot_regression(self):
        data=rainfall_records(raw_records(),TODAY)
        self.assertEqual(data['year']['wettest'],{'valueIn':52.8,'trace':False,'years':[1958]})
        self.assertEqual(data['year']['driest']['valueIn'],19.88)
        self.assertEqual(data['year']['driest']['years'],[1965])
        self.assertEqual(data['month']['wettest']['valueIn'],10.29)
        self.assertEqual(data['month']['driest'],{'valueIn':0,'trace':True,'years':[1947]})
        self.assertEqual(data['through'],'2026-10-22')
    def test_zero_is_lower_than_trace_and_tied_years_remain(self):
        raw=raw_records();raw['data'] += [['2001',[['0.00',0]],[['52.80',0]]]]
        data=rainfall_records(raw,TODAY)
        self.assertEqual(data['month']['driest'],{'valueIn':0,'trace':False,'years':[2001]})
        self.assertEqual(data['year']['wettest']['years'],[1958,2001])
    def test_missing_days_cannot_establish_a_driest_window(self):
        raw=raw_records();raw['data'][2][2]=[['0.01',1]]
        self.assertNotEqual(rainfall_records(raw,TODAY)['year']['driest']['years'],[1965])
    def test_missing_value_does_not_become_zero(self):
        raw=raw_records();raw['data'][0][1]=[['M',0]]
        self.assertEqual(rainfall_records(raw,TODAY)['month']['driest']['valueIn'],.9)
    def test_current_year_is_not_a_historical_record(self):
        raw=raw_records();raw['data'] += [['2026',[['100',0]],[['999',0]]]]
        self.assertEqual(rainfall_records(raw,TODAY)['year']['wettest']['valueIn'],52.8)
    def test_duplicate_years_and_wrong_station_are_rejected(self):
        raw=raw_records();raw['data'].append(copy.deepcopy(raw['data'][1]))
        self.assertEqual(rainfall_records(raw,TODAY)['year']['wettest']['valueIn'],44.26)
        raw['meta']['name']='Other'
        with self.assertRaises(ValueError):rainfall_records(raw,TODAY)
    def test_all_missing_data_has_no_fabricated_extremes(self):
        raw=raw_records()
        for row in raw['data']:row[1:]=[[['M',1]],[['M',1]]]
        with self.assertRaises(ValueError):rainfall_records(raw,TODAY)
    def test_query_uses_ytd_and_mtd_at_the_same_calendar_date(self):
        with tempfile.TemporaryDirectory() as folder:
            fetch=Mock(return_value=raw_records());source=RainfallRecords(folder,fetch)
            result=source.get(TODAY)
            self.assertEqual(source.get(TODAY),result);self.assertEqual(fetch.call_count,1)
            p=json.loads(parse_qs(urlsplit(fetch.call_args.args[0]).query)['params'][0])
            self.assertEqual([e['duration'] for e in p['elems']],['mtd','ytd'])
            for e in p['elems']:
                self.assertEqual(e['groupby'],['year','10-22','10-22']);self.assertEqual(e['maxmissing'],0)
            self.assertEqual(RainfallRecords(folder,Mock(side_effect=AssertionError('Use cache'))).get(TODAY),result)
    def test_old_full_period_cache_is_not_reused(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder)/'boston-rain-records.json').write_text(json.dumps({'schemaVersion':1,'stationId':'BOSthr','months':{},'year':{'wettest':65.53}}))
            fetch=Mock(return_value=raw_records());data=RainfallRecords(folder,fetch).get(TODAY)
            self.assertEqual(fetch.call_count,1);self.assertEqual(data['schemaVersion'],2)
    def test_next_day_requires_new_records_and_failure_is_throttled(self):
        with tempfile.TemporaryDirectory() as folder:
            source=RainfallRecords(folder,Mock(return_value=raw_records()));source.get(TODAY)
            source.fetch=Mock(side_effect=OSError('offline'))
            result=source.get(TODAY+dt.timedelta(days=1))
            self.assertTrue(result['stale']);self.assertEqual(result['date'],TODAY.isoformat())
            source.get(TODAY+dt.timedelta(days=1));self.assertEqual(source.fetch.call_count,1)
    def test_failure_without_cache_keeps_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            data=RainfallRecords(folder,Mock(side_effect=OSError('offline'))).get(TODAY)
            self.assertIsNone(data['month']);self.assertIsNone(data['year']);self.assertTrue(data['stale'])
    def test_leap_day_skips_nonexistent_historical_date(self):
        raw=raw_records();raw['data'].append(['2000',[['1',0]],[['2',0]]])
        data=rainfall_records(raw,dt.date(2028,2,29))
        self.assertEqual(data['year']['wettest']['years'],[1996])
        self.assertNotIn(1958,data['year']['wettest']['years'])

if __name__=='__main__':unittest.main()
