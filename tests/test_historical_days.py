"""Historical day boundaries, pagination, persistent reuse and same-date comparisons."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfoNotFoundError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import historical_days as h
import timezones
import server
from test_climate import series, records
from test_annual_rain import boundary

ZONE = 'America/New_York'
DAY = dt.date(2026, 10, 8)


def samples(day=DAY, step=300, year=26.0):
    start, end = h.day_bounds(day, ZONE)
    return [{'timestamp':t, 'tempF':50+(i % 20), 'humidity':60, 'gustMph':9,
             'maxGustMph':15, 'pressureHpa':1010, 'rainDayMm':2.54,
             'rainMonthMm':25.4, 'rainYearMm':None if year is None else year*25.4,
             'stationTimezone':ZONE} for i,t in enumerate(range(int(start),int(end),step))]


def page_source(rows, limit=288):
    return lambda cursor: sorted([p for p in rows if p['timestamp']*1000 <= cursor],
                                 key=lambda p:p['timestamp'],reverse=True)[:limit]


def quiet_climate(self, key, state, day, active, force, site):
    with self.lock:
        state['loadingParts']['climate'] = state['loadingParts']['records'] = False


def join_days(archive):
    for state in list(archive.states.values()):
        for name in ('stationThread','climateThread'):
            if state.get(name):state[name].join(3)


class DayBoundaryTests(unittest.TestCase):
    def test_daylight_saving_days_have_23_and_25_hours(self):
        for date, hours in [('2026-03-08',23),('2026-11-01',25),('2026-10-08',24)]:
            start,end=h.day_bounds(dt.date.fromisoformat(date),ZONE)
            self.assertEqual((end-start)/3600,hours)

    def test_windows_fallback_matches_transition_midnights(self):
        dates=[dt.date(2026,3,8),dt.date(2026,3,9),dt.date(2026,11,1),dt.date(2026,11,2)]
        expected=[h.midnight(d,ZONE) for d in dates]
        with patch.object(timezones,'ZoneInfo',side_effect=ZoneInfoNotFoundError):
            self.assertEqual([h.midnight(d,'US/Eastern') for d in dates],expected)
            with self.assertRaises(ValueError):h.midnight(DAY,'Invalid/Zone')

    def test_completed_day_uses_local_date_not_utc(self):
        now=dt.datetime(2026,10,23,1,tzinfo=h.UTC).timestamp()
        self.assertEqual(h.validate_day('2026-10-21',ZONE,now),dt.date(2026,10,21))
        for invalid in ('2026-10-22','2026-10-23','2026-02-30','2026-1-02','1969-12-31','','../../'):
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):h.validate_day(invalid,ZONE,now)

    def test_unknown_timezone_is_rejected(self):
        with self.assertRaises(ValueError):h.validate_day('2026-10-01','Invalid/Zone')


class StationDayTests(unittest.TestCase):
    def test_pagination_assembles_entire_25_hour_day_and_deduplicates(self):
        day=dt.date(2026,11,1);rows=samples(day,step=60)
        fetch=Mock(side_effect=page_source(rows))
        result=h.fetch_station_day(fetch,day,ZONE)
        self.assertEqual(result['coverage']['count'],1500)
        self.assertEqual(result['coverage']['dayHours'],25)
        self.assertTrue(result['coverage']['fullSpan'])
        cursors=[call.args[0] for call in fetch.call_args_list]
        self.assertEqual(cursors,sorted(set(cursors),reverse=True))
        self.assertLessEqual(len(cursors),7)

    def test_daily_counter_is_maximum_not_sum_and_last_counters_are_not_aggregated(self):
        rows=samples();rows[30]['rainDayMm']=5.08
        result=h.summarize_station(rows+rows,DAY,ZONE)
        self.assertEqual(result['rainDayMm'],5.08)
        self.assertEqual(result['last']['rainMonthMm'],25.4)
        self.assertAlmostEqual(result['last']['rainYearMm'],26*25.4)
        self.assertEqual(result['high']['value'],69)
        self.assertEqual(result['low']['value'],50)
        self.assertEqual(result['maxGustMph'],15)
        self.assertEqual(len(result['points']),288)

    def test_adjacent_days_are_excluded_even_if_api_ignores_cursor(self):
        start,end=h.day_bounds(DAY,ZONE)
        rows=samples()+[{'timestamp':start-1,'tempF':-99},{'timestamp':end,'tempF':150}]
        fetch=Mock(return_value=rows)
        result=h.fetch_station_day(fetch,DAY,ZONE)
        self.assertEqual(result['high']['value'],69)
        self.assertEqual(result['low']['value'],50)
        self.assertLessEqual(fetch.call_count,2)

    def test_missing_values_are_not_zero_and_coarse_history_is_counted(self):
        rows=samples(step=1800)
        for p in rows:p['rainDayMm']=p['rainMonthMm']=p['rainYearMm']=p['tempF']=None
        result=h.summarize_station(rows,DAY,ZONE)
        self.assertIsNone(result['rainDayMm']);self.assertIsNone(result['high']['value'])
        self.assertEqual(result['coverage']['count'],48)
        self.assertEqual(result['coverage']['intervalSeconds'],1800)
        self.assertTrue(result['coverage']['fullSpan'])
        self.assertIsNone(h.summarize_station([],DAY,ZONE))

    def test_zero_is_preserved_and_partial_day_is_labeled(self):
        rows=samples()[-12:]
        for p in rows:p['rainDayMm']=p['rainMonthMm']=p['rainYearMm']=0
        result=h.summarize_station(rows,DAY,ZONE)
        self.assertEqual(result['rainDayMm'],0)
        self.assertEqual(result['last']['rainYearMm'],0)
        self.assertFalse(result['coverage']['fullSpan'])

    def test_nearby_missing_counter_recovery_is_bounded(self):
        rows=samples();rows[-1]['rainYearMm']=None
        self.assertAlmostEqual(h.summarize_station(rows,DAY,ZONE)['last']['rainYearMm'],26*25.4)
        for p in rows[-13:]:p['rainYearMm']=None
        self.assertIsNone(h.summarize_station(rows,DAY,ZONE)['last']['rainYearMm'])

    def test_cancellation_does_not_request_another_page(self):
        calls=[];enabled=[True]
        def fetch(cursor):
            calls.append(cursor);enabled[0]=False
            return samples()[-10:]
        self.assertIsNone(h.fetch_station_day(fetch,DAY,ZONE,lambda:enabled[0]))
        self.assertEqual(len(calls),1)


class CompletedClimateTests(unittest.TestCase):
    def test_selected_day_is_included_in_boston_actual_mtd_and_ytd(self):
        day=dt.date(2026,1,4)
        result=h.completed_almanac(series(day),records(),day)
        self.assertEqual(result['observed']['precipIn'],99)
        self.assertEqual(result['precipitation']['year']['observedIn'],99.3)
        self.assertEqual(result['precipitation']['month']['through'],day.isoformat())
        self.assertEqual(result['normal']['rainMonthToDateIn'],.8)
        self.assertEqual(result['records']['high']['years'],[1920,1979])

    def test_leap_day_normal_window_and_missing_observations(self):
        day=dt.date(2024,2,29);raw=series(day)
        raw['data'][-1][1:4]=[['M',' '],['M',' '],['M',' ']]
        result=h.completed_almanac(raw,records(),day)
        self.assertIsNone(result['observed']['highF'])
        self.assertIsNone(result['observed']['precipIn'])
        self.assertIsNone(result['precipitation']['year']['observedIn'])
        self.assertEqual(result['normal']['rainYearToDateIn'],12)
        self.assertEqual(result['normal']['rainMonthToDateIn'],5.8)

    def test_trace_is_not_fabricated_measurable_rain(self):
        day=dt.date(2026,1,1);raw=series(day);raw['data'][0][3]=['T',' ']
        result=h.completed_almanac(raw,records(),day)
        self.assertEqual(result['observed']['precipIn'],0)
        self.assertTrue(result['observed']['trace'])
        self.assertTrue(result['precipitation']['year']['trace'])

    def test_same_date_cache_reused_and_failure_never_returns_other_date(self):
        day=dt.date(2026,1,4)
        with tempfile.TemporaryDirectory() as directory:
            fetch=Mock(side_effect=[series(day),records()])
            first=h.historical_climate(directory,day,fetch)
            second=h.historical_climate(directory,day,Mock(side_effect=AssertionError('must use cache')))
            self.assertEqual(first,second)
            query=json.loads(parse_qs(urlsplit(fetch.call_args_list[0].args[0]).query)['params'][0])
            self.assertEqual(query['edate'],'2026-01-04')
            stale=h.historical_climate(directory,day,Mock(side_effect=OSError),force=True)
            self.assertTrue(stale['stale']);self.assertEqual(stale['observed'],first['observed'])
            other=h.historical_climate(directory,dt.date(2026,1,3),Mock(side_effect=OSError))
            self.assertEqual(other['date'],'2026-01-03');self.assertNotIn('observed',other)


@patch.object(h.HistoricalDays,'_climate',quiet_climate)
class PersistentDayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.archive=h.HistoricalDays(self.temp.name,Mock())
    def tearDown(self):
        self.archive.cancel();join_days(self.archive);self.temp.cleanup()
    def get(self,fetch,day=DAY,station='A',force=False):
        self.archive.get(day,ZONE,station,fetch,lambda:True,force)
        join_days(self.archive)
        return self.archive.get(day,ZONE,station,fetch,lambda:True)
    def test_restart_reuses_saved_day_without_ambient_request(self):
        first=self.get(page_source(samples()))
        self.assertFalse(first['loading'])
        self.archive=h.HistoricalDays(self.temp.name,Mock())
        fetch=Mock(side_effect=AssertionError('must use saved day'))
        saved=self.get(fetch)
        self.assertTrue(saved['fromCache']);fetch.assert_not_called()
        self.assertEqual(saved['stationData'],first['stationData'])
    def test_other_station_and_other_day_do_not_reuse_cache(self):
        self.get(page_source(samples()))
        for day,station in [(DAY,'B'),(DAY-dt.timedelta(days=1),'A')]:
            result=self.get(lambda _:[],day,station)
            self.assertIsNone(result['stationData'])
            self.assertIn('no readings',result['stationError'])
    def test_refresh_failure_keeps_saved_day_and_redacts_exception(self):
        self.get(page_source(samples()))
        result=self.get(Mock(side_effect=RuntimeError('apiKey=SECRET')),force=True)
        self.assertIsNotNone(result['stationData']);self.assertTrue(result['fromCache'])
        self.assertNotIn('SECRET',json.dumps(result))
        self.assertIn('could not load',result['stationError'])
    def test_selection_change_prevents_old_result_overwriting_new_day(self):
        entered=threading.Event();release=threading.Event()
        def old_fetch(cursor):
            entered.set();release.wait(2);return samples()
        self.archive.get(DAY,ZONE,'A',old_fetch,lambda:True)
        self.assertTrue(entered.wait(2))
        new_day=DAY-dt.timedelta(days=1)
        self.archive.get(new_day,ZONE,'A',page_source(samples(new_day)),lambda:True)
        release.set();join_days(self.archive)
        result=self.archive.get(new_day,ZONE,'A',Mock(),lambda:True)
        self.assertEqual(result['date'],new_day.isoformat())
        self.assertEqual(result['stationData']['date'],new_day.isoformat())
        old=self.archive.states[('A',ZONE,DAY.isoformat())]
        self.assertIsNone(old['stationData'])


@patch.object(h.HistoricalDays,'_climate',quiet_climate)
class DeskHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.desk=server.Desk(self.temp.name)
        self.desk.settings['timezone']=ZONE
        self.desk.api_key='private-api';self.desk.app_key='private-app';self.desk.mac='AA:BB:CC:DD:EE:FF'
    def tearDown(self):
        self.desk.past_days.cancel();join_days(self.desk.past_days)
        for _,thread in self.desk.past_days.annual.jobs.values():thread.join(3)
        self.desk.db.close();self.temp.cleanup()
    def test_api_adapter_uses_saved_credentials_and_selected_end_date(self):
        raw=[{'dateutc':int(p['timestamp']*1000),'tempf':p['tempF'],'yearlyrainin':26,
              'monthlyrainin':1,'dailyrainin':.1,'tz':ZONE} for p in samples()]
        with patch.object(self.desk,'ambient',return_value=raw) as fetch:
            self.desk.past_day(DAY.isoformat());join_days(self.desk.past_days)
            result=self.desk.past_day(DAY.isoformat())
        self.assertEqual(result['annual']['status'],'reported')
        self.assertAlmostEqual(result['annual']['mm']/25.4,26)
        kwargs=fetch.call_args.kwargs
        self.assertEqual(kwargs['limit'],288)
        self.assertEqual(kwargs['_credentials'],('private-api','private-app'))
        self.assertEqual(kwargs['endDate'],int(h.day_bounds(DAY,ZONE)[1]*1000)-1)
        self.assertNotIn('private-api',json.dumps(result));self.assertNotIn('private-app',json.dumps(result))
    def test_past_ytd_uses_saved_completed_months_plus_past_mtd(self):
        for month in range(1,10):
            self.desk.annual_rain._save((self.desk.mac,ZONE,2026),{'month':month,'inches':month/10,
                'timestamp':boundary(month,month/10)[-1]['timestamp'],'method':'month-end counter'})
        value=h.summarize_station(samples(year=None),DAY,ZONE)
        with patch.object(self.desk.past_days,'get',return_value={'stationData':value,'loading':False}):
            result=self.desk.past_day(DAY.isoformat())
        self.assertTrue(result['annual']['complete'])
        self.assertAlmostEqual(result['annual']['mm']/25.4,5.5)
        self.assertEqual(result['annual']['through'],'2026-10-08')
        self.assertIsNot(self.desk.annual_rain,self.desk.past_days.annual)
    def test_mismatched_station_timezone_does_not_claim_calendar_year_total(self):
        value=h.summarize_station(samples(),DAY,ZONE);value['last']['stationTimezone']='UTC'
        with patch.object(self.desk.past_days,'get',return_value={'stationData':value,'loading':False}):
            result=self.desk.past_day(DAY.isoformat())
        self.assertFalse(result['annual']['complete'])
        self.assertIn('timezone',result['annual']['error'])


if __name__=='__main__':unittest.main()
