"""Monthly YTD behavior: one counter per month, persistent reuse, live MTD."""
import datetime as dt
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from annual_rain import AnnualRain,completed_month,local_day,month_start
ZONE='America/New_York'


def current(year=2026,month=10,amount=2.7):
    return {'timestamp':month_start(year,month,ZONE)+21*86400+12*3600,
            'rainMonthMm':None if amount is None else amount*25.4,
            'rainDayMm':.4*25.4,'rainTotalMm':999*25.4}


def boundary(month,amount,year=2026):
    end=month_start(year,month+1,ZONE)
    # Deliberately disagreeing daily/lifetime counters are irrelevant to the
    # monthly sum; duplicate running counters must not be added together.
    return [{'timestamp':end-600,'rainMonthMm':amount*25.4,'rainDayMm':0},
            {'timestamp':end-300,'rainMonthMm':amount*25.4,'rainDayMm':99}]


class MonthEndTests(unittest.TestCase):
    def test_monthly_counter_needs_no_daily_or_lifetime_counter(self):
        r=completed_month(boundary(1,3.2),2026,1,ZONE)
        self.assertAlmostEqual(r['inches'],3.2)
    def test_zero_month_is_valid_and_missing_month_is_not_zero(self):
        self.assertEqual(completed_month(boundary(2,0),2026,2,ZONE)['inches'],0)
        self.assertIsNone(completed_month([],2026,2,ZONE))
    def test_stale_counter_from_earlier_in_month_is_not_a_completed_month(self):
        self.assertIsNone(completed_month([{'timestamp':month_start(2026,1,ZONE)+86400,'rainMonthMm':25.4}],2026,1,ZONE))
    def test_late_midnight_rain_is_recovered_without_adding_next_month_twice(self):
        end=month_start(2026,2,ZONE)
        rows=[{'timestamp':end-300,'rainMonthMm':3*25.4,'rainTotalMm':103*25.4},
              {'timestamp':end+300,'rainMonthMm':.1*25.4,'rainTotalMm':103.3*25.4}]
        self.assertAlmostEqual(completed_month(rows,2026,1,ZONE)['inches'],3.2)
    def test_lifetime_reset_does_not_hide_a_monthly_counter(self):
        rows=boundary(1,3.2);rows[-1]['rainTotalMm']=103.2*25.4
        rows.append({'timestamp':month_start(2026,2,ZONE)+300,'rainMonthMm':0,'rainTotalMm':0})
        self.assertAlmostEqual(completed_month(rows,2026,1,ZONE)['inches'],3.2)
    def test_0003_inch_counter_drift_cannot_trigger_the_old_mismatch(self):
        rows=boundary(1,3.2);rows[-1]['rainTotalMm']=103.2*25.4
        rows.append({'timestamp':month_start(2026,2,ZONE)+300,'rainMonthMm':0,'rainTotalMm':103.203*25.4})
        self.assertAlmostEqual(completed_month(rows,2026,1,ZONE)['inches'],3.2)
    def test_local_month_boundaries_follow_dst_and_new_year(self):
        self.assertEqual(month_start(2026,2,ZONE),dt.datetime(2026,2,1,5,tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(month_start(2026,7,ZONE),dt.datetime(2026,7,1,4,tzinfo=dt.timezone.utc).timestamp())
        self.assertEqual(local_day(dt.datetime(2026,1,1,4,59,tzinfo=dt.timezone.utc).timestamp(),ZONE),dt.date(2025,12,31))


class MonthlyArchiveTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.archive=AnnualRain(self.temp.name)
    def tearDown(self):
        self.archive.cancel()
        for _,thread in self.archive.jobs.values():thread.join(3)
        self.temp.cleanup()
    def run_import(self,c,fetch,station='A',zone=ZONE):
        self.archive.ensure(station,zone,c,fetch)
        job=self.archive.jobs.get((station,zone,local_day(c['timestamp'],zone).year))
        if job:job[1].join(3)
        return self.archive.result(station,zone,c)
    def test_january_to_september_plus_october_once_and_restart_reuses_cache(self):
        amounts=[1.2,0,2.3,4.1,1.05,5,2,.85,7.22];calls=[]
        def fetch(cursor):
            calls.append(cursor)
            month=local_day(cursor/1000,ZONE).month-1
            return boundary(month,amounts[month-1])
        result=self.run_import(current(),fetch)
        self.assertTrue(result['complete']);self.assertAlmostEqual(result['mm']/25.4,26.42)
        self.assertEqual(len(calls),9);self.assertEqual(result['loadedMonths'],10)
        self.assertEqual(result['start'],'2026-01-01');self.assertEqual(result['through'],'2026-10-22')
        restarted=AnnualRain(self.temp.name)
        restarted.ensure('A',ZONE,current(),lambda _:self.fail('Cached months must not download again'))
        self.assertAlmostEqual(restarted.result('A',ZONE,current())['mm']/25.4,26.42)
        # Current MTD changes immediately, without refetching completed months.
        self.assertAlmostEqual(restarted.result('A',ZONE,current(amount=2.9))['mm']/25.4,26.62)
    def test_actual_ambient_monthly_display_values_add_to_2672(self):
        # Read from the station's visible Ambient Tables on 2026-10-22.
        # This regression fixture is not a hardcoded application total.
        amounts=[.88,.34,3.36,1.65,2.54,2.89,5.48,1.4,5.48]
        def fetch(cursor):
            month=local_day(cursor/1000,ZONE).month-1
            # Independently converted API decimals round to the displayed
            # monthly values before their sum, with no daily consistency test.
            return boundary(month,amounts[month-1]-.001)
        result=self.run_import(current(amount=2.701),fetch)
        self.assertTrue(result['complete']);self.assertAlmostEqual(result['mm']/25.4,26.72)

    def test_january_needs_no_history_and_does_not_add_today_twice(self):
        result=self.run_import(current(month=1,amount=1.5),lambda _:self.fail('No previous months in January'))
        self.assertTrue(result['complete']);self.assertAlmostEqual(result['mm']/25.4,1.5)
    def test_missing_month_is_named_and_other_months_are_kept(self):
        def fetch(cursor):
            month=local_day(cursor/1000,ZONE).month-1
            return [] if month==2 else boundary(month,1)
        result=self.run_import(current(month=4),fetch)
        self.assertFalse(result['complete']);self.assertIsNone(result['mm']);self.assertIn('Feb',result['error'])
        self.assertEqual(result['loadedMonths'],3)
    def test_missing_live_month_is_never_zero(self):
        result=self.run_import(current(month=1,amount=None),lambda _:[])
        self.assertFalse(result['complete']);self.assertIsNone(result['mm'])
    def test_station_zone_and_year_do_not_share_totals(self):
        self.run_import(current(month=2),lambda _:boundary(1,1.5))
        self.assertFalse(self.archive.result('B',ZONE,current(month=2))['complete'])
        self.assertFalse(self.archive.result('A','UTC',current(month=2))['complete'])
        self.assertFalse(self.archive.result('A',ZONE,current(year=2027,month=2))['complete'])
    def test_new_month_only_downloads_the_newly_completed_month(self):
        self.run_import(current(month=2),lambda _:boundary(1,1.5))
        calls=[]
        result=self.run_import(current(month=3),lambda cursor:calls.append(cursor) or boundary(2,2))
        self.assertEqual(len(calls),1);self.assertAlmostEqual(result['mm']/25.4,6.2)
    def test_api_error_retains_progress_and_redacts_exception(self):
        def fetch(cursor):
            month=local_day(cursor/1000,ZONE).month-1
            if month==2:raise RuntimeError('SECRET_KEY_URL')
            return boundary(month,1)
        result=self.run_import(current(month=3),fetch)
        self.assertEqual(result['status'],'paused');self.assertEqual(result['loadedMonths'],2)
        self.assertNotIn('SECRET_KEY',str(result))
    def test_inactive_connection_does_not_request_data(self):
        calls=[]
        self.archive.ensure('A',ZONE,current(month=2),lambda c:calls.append(c),lambda:False)
        self.archive.jobs[('A',ZONE,2026)][1].join(3)
        self.assertEqual(calls,[])

if __name__=='__main__':unittest.main()
