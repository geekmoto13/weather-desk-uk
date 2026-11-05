"""Rain age must preserve real measurements, coverage, source and restart history."""
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from rain_since import RainMemory, interval_rain_since, rain_time
from server import normalize, Desk
from cwop_weather import parse_reports, query_url, PreferredPublicWeather
from test_cwop import xml, NOW


class RainAgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.memory=RainMemory(self.temp.name)
    def result(self,current,history=(),key='station-A',now=None):
        return self.memory.snapshot(key,key,current,list(history),now or current['timestamp'],'UTC')
    def test_native_last_rain_and_native_ten_minute_field_are_retained(self):
        stamp=dt.datetime(2026,11,4,12,tzinfo=dt.timezone.utc).timestamp()
        raw={'dateutc':stamp*1000,'lastRain':'2026-11-01T12:00:00Z',
             'windspeedmph':1,'windspdmph_avg10m':15,'windgustmph':40}
        current=normalize(raw);result=self.result(current)
        self.assertEqual(result['seconds'],3*86400)
        self.assertFalse(result['approximate'])
        self.assertEqual(current['windMean10mMph'],15)
        self.assertEqual(current['windMph'],1)
    def test_future_naive_and_invalid_rain_times_stay_missing(self):
        for value in (None,True,'not-a-date','2026-11-04T12:00:00','2027-01-01T00:00:00Z'):
            self.assertIsNone(rain_time(value,NOW))
    def test_invalid_ten_minute_values_do_not_become_calm(self):
        for bad in (None,True,-1,'NaN',float('inf')):
            self.assertIsNone(normalize({'windspdmph_avg10m':bad})['windMean10mMph'])
        self.assertEqual(normalize({'windspdmph_avg10m':0})['windMean10mMph'],0)
    def test_native_rain_seconds_are_retained_when_observation_is_rounded_to_a_minute(self):
        stamp=dt.datetime(2026,11,4,12,tzinfo=dt.timezone.utc).timestamp()
        current=normalize({'dateutc':stamp*1000,'lastRain':'2026-11-04T12:00:30Z'})
        result=self.result(current,now=stamp+45)
        self.assertEqual(result['seconds'],0);self.assertEqual(result['asOf'],stamp+30)
    def test_complete_interval_series_finds_last_wet_interval(self):
        end=180000;wet=end-2*86400
        rows={t:0 for t in range(wet,end+1,900)};rows[wet]=.2
        result=interval_rain_since(rows,900,end)
        self.assertEqual(result['status'],'known');self.assertEqual(result['seconds'],2*86400)
        self.assertEqual(result['lastRainStart'],wet-900)
    def test_interval_gap_limits_the_supported_dry_period(self):
        end=180000;rows={t:0 for t in range(end-7200,end+1,900)}
        rows[end-3600]=None;rows[end-7200]=1
        result=interval_rain_since(rows,900,end)
        self.assertEqual(result['status'],'at_least');self.assertEqual(result['seconds'],3600)
    def test_all_dry_interval_history_is_a_lower_bound_not_an_invented_rain_date(self):
        end=180000;rows={t:0 for t in range(end-86400+900,end+1,900)}
        result=interval_rain_since(rows,900,end)
        self.assertEqual(result['seconds'],86400);self.assertEqual(result['status'],'at_least')
        self.assertIsNone(result['lastRainAt'])
    def test_missing_boolean_and_negative_intervals_do_not_count_as_dry(self):
        for bad in (None,False,-1,float('nan')):
            self.assertEqual(interval_rain_since({900:bad},900,900)['status'],'unavailable')
    def test_rolling_zeros_persist_and_extend_through_restart(self):
        self.result({'timestamp':100000,'rainLast24hMm':0})
        self.memory=RainMemory(self.temp.name)
        result=self.result({'timestamp':100000+86400,'rainLast24hMm':0})
        self.assertEqual(result['seconds'],2*86400);self.assertEqual(result['status'],'at_least')
    def test_unobserved_offline_gap_does_not_become_dry_time(self):
        self.result({'timestamp':100000,'rainLastHourMm':0})
        result=self.result({'timestamp':100000+3*86400,'rainLastHourMm':0})
        self.assertEqual(result['seconds'],3600);self.assertEqual(result['status'],'at_least')
    def test_sources_never_share_rain_history(self):
        self.result({'timestamp':100000,'rainLastHourMm':0})
        result=self.result({'timestamp':200000},key='station-B')
        self.assertEqual(result['status'],'unavailable')
    def test_rolling_wet_and_later_zero_bound_the_event_instead_of_resetting_to_now(self):
        self.result({'timestamp':100000,'rainLastHourMm':1})
        result=self.result({'timestamp':100900,'rainLastHourMm':0})
        self.assertEqual(result['status'],'known');self.assertEqual(result['seconds'],3600)
        self.assertTrue(result['approximate']);self.assertLess(result['lastRainAt'],100000)
    def test_conflicting_rolling_totals_do_not_prove_a_dry_day(self):
        result=self.result({'timestamp':100000,'rainLastHourMm':1,'rainLast24hMm':0})
        self.assertEqual(result['status'],'unavailable')
    def test_counter_increase_and_unchanged_counter_establish_wet_then_dry(self):
        history=[{'timestamp':100000,'rainDayMm':1,'stationLocalDay':'2026-11-04'},
                 {'timestamp':100600,'rainDayMm':2,'stationLocalDay':'2026-11-04'}]
        result=self.result({'timestamp':101200,'rainDayMm':2,'stationLocalDay':'2026-11-04'},history)
        self.assertEqual(result['seconds'],600);self.assertEqual(result['status'],'known')
    def test_counter_reset_is_not_treated_as_a_dry_day(self):
        start=dt.datetime(2026,11,4,12,tzinfo=dt.timezone.utc).timestamp()
        base={'stationTimezone':'UTC','stationLocalDay':'2026-11-04'}
        history=[{**base,'timestamp':start,'rainDayMm':10},
                 {**base,'timestamp':start+300,'rainDayMm':0}]
        result=self.result({**base,'timestamp':start+600,'rainDayMm':0},history)
        self.assertLessEqual(result['seconds'],600)
    def test_native_rain_age_stays_at_the_observation_time_when_feed_stops(self):
        current={'timestamp':100000,'lastRainAt':10000}
        a=self.result(current,now=100000);b=self.result(current,now=200000)
        self.assertEqual(a['seconds'],b['seconds']);self.assertEqual(b['asOf'],100000)
    def test_history_can_seed_the_last_wet_report_after_first_current_request(self):
        current={'timestamp':100000,'rainLastHourMm':0}
        self.result(current)
        history=[{'timestamp':96400,'rainLastHourMm':1},
                 {'timestamp':98200,'rainLastHourMm':0}]
        result=self.result(current,history)
        self.assertEqual(result['status'],'known');self.assertGreaterEqual(result['seconds'],3600)


class RecentWindTests(unittest.TestCase):
    def test_parser_can_return_each_actual_report_without_changing_station_selection(self):
        rows=[('A',52.77,-1.56,600,{'T':280,'FF':1}),('A',52.77,-1.56,300,{'T':280,'FF':2}),('A',52.77,-1.56,0,{'T':280,'FF':3})]
        self.assertEqual(len(parse_reports(xml(rows),52.77,-1.56,lambda *a:(None,''),NOW)),1)
        result=parse_reports(xml(rows),52.77,-1.56,lambda *a:(None,''),NOW,keep_all=True)
        self.assertEqual(len(result),3)
        self.assertEqual([r['current']['timestamp'] for r in result],[NOW-600,NOW-300,NOW])
    def test_selected_station_history_query_is_small_and_requests_all_window_reports(self):
        url=query_url(52.77,-1.56,wind_time=NOW)
        self.assertIn('minbck=-20',url);self.assertIn('recwin=4',url)
    def test_beaufort_native_mean_is_kept_in_the_api_weather_response(self):
        with tempfile.TemporaryDirectory() as folder:
            desk=Desk(folder);desk.api_key='fixture';desk.app_key='fixture';desk.mac='AA:BB:CC:DD:EE:FF'
            current=normalize({'dateutc':NOW*1000,'windspdmph_avg10m':10,'windspeedmph':1,'lastRain':'2026-01-01T00:00:00Z'})
            desk.cache=current;desk.cache_at=1e20
            with patch('server.time.time',return_value=NOW):
                value=desk.weather()
            self.assertEqual(value['current']['windMean10mMph'],10)
            self.assertEqual(value['rainSince']['status'],'known');desk.db.close()


if __name__=='__main__':unittest.main()
