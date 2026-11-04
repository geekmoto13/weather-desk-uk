"""Public feed schemas, freshness, caching and path identity without station keys."""
import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from urllib.parse import parse_qs,urlsplit

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import radio_space as r

NOW=dt.datetime(2026,10,23,2,tzinfo=r.UTC).timestamp()
ISO=lambda t:dt.datetime.fromtimestamp(t,r.UTC).isoformat()


def raw_path():
    return {'bands':[[b,f] for b,f in zip(r.BANDS,[28.85,24.94,21.225,18.118,14.175,10.12,7.15,5.33,3.65,1.9])],
            'table':[[{'bcr':75,'pr':-130,'noise':-145,'pop':80} for _ in range(24)] for _ in r.BANDS]}


class FeedParserTests(unittest.TestCase):
    def test_noaa_daily_uses_latest_completed_day_with_zero_and_missing_values(self):
        text=':Issued: 0100 UT 23 Oct 2026\n2026 10 22 106 0 270\n2026 10 21 115 66 590\n2026 10 23 999 999 1\n'
        value=r.daily_solar(text,NOW)
        self.assertEqual(value['date'],'2026-10-22');self.assertEqual(value['sfi'],106)
        self.assertEqual(value['sn'],0);self.assertEqual(value['timestamp'],NOW-7200)
        value=r.daily_solar(text.replace('106 0','-999 *'),NOW)
        self.assertIsNone(value['sfi']);self.assertIsNone(value['sn'])
        self.assertEqual(value['date'],'2026-10-22','A missing latest day is never filled with an older day.')
    def test_noaa_daily_rejects_missing_time_future_reports_and_invalid_dates(self):
        for text in ('2026 10 22 106 49',':Issued: 0100 UT 24 Oct 2026\n2026 10 22 106 49',
                     ':Issued: 0100 UT 23 Oct 2026\n2026 02 30 106 49'):
            with self.subTest(text=text),self.assertRaises(ValueError):r.daily_solar(text,NOW)
    def test_old_daily_observations_do_not_become_fresh_when_report_is_reissued(self):
        value=r.daily_solar(':Issued: 0100 UT 23 Oct 2026\n2026 10 19 106 49',NOW)
        self.assertGreater(NOW-value['timestamp'],36*3600)
    def test_noaa_scale_zero_is_valid_missing_is_not_zero(self):
        raw={'0':{'DateStamp':'2026-10-23','TimeStamp':'02:00:00','R':{'Scale':'0'},'S':{'Scale':None},'G':{'Scale':'2'}},'1':{'R':{'MinorProb':'20','MajorProb':'5'}}}
        v=r.scales(raw,NOW)
        self.assertEqual(v['levels'],{'R':0,'S':None,'G':2})
        self.assertEqual(v['outlook'][0]['minorBlackout'],20)
        with self.assertRaises(ValueError):r.scales({'0':{}},NOW)
    def test_kp_future_predictions_never_replace_current_observations(self):
        raw=[{'time_tag':ISO(NOW+3600),'kp':8,'observed':'predicted'},
             {'time_tag':ISO(NOW-3600),'kp':2.33,'observed':'estimated'},
             {'time_tag':ISO(NOW+7200),'kp':4,'observed':'observed'}]
        value=r.kp_forecast(raw,NOW)
        self.assertEqual(value['current']['kp'],2.33)
        self.assertEqual(value['current']['kind'],'estimated')
    def test_wind_uses_latest_active_good_quality_spacecraft_not_last_array_row(self):
        rows=[{'time_tag':ISO(NOW-60),'active':True,'overall_quality':0,'source':'SOLAR1','bz_gsm':-4.5,'bt':8},
              {'time_tag':ISO(NOW),'active':False,'overall_quality':0,'source':'ACE','bz_gsm':99,'bt':99},
              {'time_tag':ISO(NOW),'active':True,'overall_quality':1,'source':'SOLAR1','bz_gsm':80,'bt':90},
              {'time_tag':ISO(NOW-86400),'active':True,'overall_quality':0,'source':'ACE','bz_gsm':1,'bt':2}]
        value=r.wind_data(rows,NOW,True)
        self.assertEqual(value['bz'],-4.5);self.assertEqual(value['spacecraft'],'SOLAR1')
        self.assertEqual(value['timestamp'],NOW-60)
    def test_wind_missing_and_sentinel_values_do_not_become_measurements(self):
        row={'time_tag':ISO(NOW),'active':True,'overall_quality':0,'proton_speed':-9999,'proton_density':None}
        with self.assertRaises(ValueError):r.wind_data([row],NOW,False)
        row.update(proton_speed=0,proton_density=0)
        self.assertIsNone(r.wind_data([row],NOW,False)['speed'])
        self.assertEqual(r.wind_data([row],NOW,False)['density'],0)
    def test_xray_uses_long_wave_uncontaminated_flux(self):
        rows=[{'time_tag':ISO(NOW-60),'energy':'0.1-0.8nm','flux':1.2e-6,'electron_contaminaton':False},
              {'time_tag':ISO(NOW),'energy':'0.05-0.4nm','flux':1e-3},
              {'time_tag':ISO(NOW),'energy':'0.1-0.8nm','flux':.1,'electron_contaminaton':True}]
        self.assertEqual(r.xray(rows,NOW)['flux'],1.2e-6)
    def test_geophysical_summary_has_real_issued_time_and_daily_indices(self):
        value=r.wwv(':Issued: 2026 Oct 23 0010 UTC\n#\nSolar flux 106 and estimated planetary A-index 20.\nNo space weather storms were observed.',NOW)
        self.assertEqual(value['sfi'],106);self.assertEqual(value['aIndex'],20)
        self.assertEqual(value['timestamp'],NOW-6600)
    def test_wwv_daily_date_survives_the_utc_new_year(self):
        value=r.wwv(':Issued: 2027 Jan 01 0010 UTC\nSolar-terrestrial indices for 31 December follow.\nSolar flux 106 and estimated planetary A-index 20.',NOW)
        self.assertEqual(value['date'],'2026-12-31')
    def test_bulletins_are_a_recent_issue_list_without_claiming_active_status(self):
        rows=[{'issue_datetime':ISO(NOW-60),'product_id':'TEST','message':'WARNING: TEST\nValid To: earlier'},
              {'issue_datetime':ISO(NOW-3*86400),'message':'OLD'},
              {'issue_datetime':ISO(NOW+86400),'message':'FUTURE'}]
        value=r.bulletins(rows,NOW)
        self.assertEqual(len(value['items']),1)
        self.assertNotIn('active',value['items'][0]);self.assertIn('earlier',value['items'][0]['message'])


class PathTests(unittest.TestCase):
    def test_all_ten_bands_have_24_hours_and_snr_is_in_mode_bandwidth(self):
        v=r.parse_path(raw_path(),NOW,{'test':True})
        self.assertEqual([b['band'] for b in v['bands']],r.BANDS)
        self.assertEqual(v['bands'][-1]['mhz'],1.9)
        self.assertEqual(v['bands'][0]['hours'][0]['snr'],15)
    def test_incomplete_forecast_and_invalid_numeric_values(self):
        raw=raw_path();raw['table'].pop()
        with self.assertRaises(ValueError):r.parse_path(raw,NOW,{})
        raw=raw_path();raw['table'][0][0]={'bcr':True,'pr':-9999,'noise':-100}
        cell=r.parse_path(raw,NOW,{})['bands'][0]['hours'][0]
        self.assertIsNone(cell['bcr']);self.assertIsNone(cell['snr'])
    def test_grid_centres_and_station_coordinates_are_rounded_for_provider(self):
        self.assertEqual(r.grid_location('AA00'),(-89.5,-179))
        lat,lon=r.grid_location('FN42ml');self.assertAlmostEqual(lat,42.479166,places=5);self.assertAlmostEqual(lon,-70.958333,places=5)
        prefs=r.clean_preferences({'target':'custom','customGrid':'io92aa'})
        context=r.path_context({'latitude':42.4668,'longitude':-70.9495},prefs)
        self.assertEqual((context['txLat'],context['txLon']),(42.47,-70.95))
        self.assertEqual(context['destination'],'IO92AA')
    def test_settings_validation_rejects_invalid_grids_parameters_and_unknown_modes(self):
        for values in ({'power':True},{'power':0},{'power':'NaN'},{'txGain':100},{'noise':'garbage'},{'mode':'unknown'},{'mode':[]},{'target':{}},{'customGrid':'../../'},{'target':'evil-url'}):
            with self.subTest(values=values),self.assertRaises(ValueError):r.clean_preferences(values)
    def test_path_adapter_uses_public_parameters_and_detects_model_rollover(self):
        with tempfile.TemporaryDirectory() as directory:
            desk=r.RadioDesk(directory,Mock(side_effect=[{'run_id':7,'target_time':NOW},raw_path(),{'run_id':7,'target_time':NOW}]))
            prefs=r.clean_preferences({'mode':'CW','power':50})
            context=r.path_context({'latitude':42.4668,'longitude':-70.9495},prefs)
            value=desk._path(context,NOW)
            url=desk.fetch.call_args_list[1].args[0];query=parse_qs(urlsplit(url).query)
            self.assertEqual(query['txloc'],['42.47,-70.95']);self.assertEqual(query['bw'],['500'])
            self.assertEqual(query['snrr'],['0']);self.assertEqual(query['txant'],['ISOTROPIC'])
            self.assertNotIn('apiKey',url);self.assertEqual(value['runId'],7)
            desk.fetch=Mock(side_effect=[{'run_id':7,'target_time':NOW},raw_path(),{'run_id':8,'target_time':NOW}])
            with self.assertRaises(ValueError):desk._path(context,NOW)
    def test_preferences_survive_restart_without_using_station_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            desk=r.RadioDesk(directory,Mock());desk.update({'mode':'FT8','power':10,'target':'japan','apiKey':'DO_NOT_STORE'})
            again=r.RadioDesk(directory,Mock())
            self.assertEqual(again.prefs['mode'],'FT8');self.assertEqual(again.prefs['target'],'japan')
            self.assertNotIn('DO_NOT_STORE',again.preferences_path.read_text())
    def test_default_path_cache_is_reused_after_saving_and_restarting(self):
        with tempfile.TemporaryDirectory() as directory:
            weather={'latitude':42.4668,'longitude':-70.9495}
            fetch=Mock(side_effect=AssertionError('No upstream request expected for saved cache'))
            desk=r.RadioDesk(directory,fetch)
            # Keep independent global feeds out of this path-cache check.
            desk.feeds={}
            context=r.path_context(weather,desk.prefs)
            now=time.time()
            key=r.hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest()[:20]
            r.write_private_json(desk.directory/('path-'+key+'.json'),
                {'schema':1,'fetchedAt':now,'value':r.parse_path(raw_path(),now,context)})
            desk.update({})
            again=r.RadioDesk(directory,fetch);again.feeds={}
            result=again.snapshot(weather)
            self.assertFalse(result['path']['stale'])
            self.assertFalse(result['path']['loading'])
            self.assertEqual(len(result['path']['value']['bands']),10)
            fetch.assert_not_called()


class PublicCacheTests(unittest.TestCase):
    def test_concurrent_requests_share_one_fetch_and_saved_data_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            entered=threading.Event();release=threading.Event()
            def load(now):entered.set();release.wait(2);return {'timestamp':now,'sfi':0}
            loader=Mock(side_effect=load);feed=r.PublicFeed(directory,'test',loader,3600,43200)
            feed.get();self.assertTrue(entered.wait(1))
            for _ in range(5):self.assertTrue(feed.get()['loading'])
            release.set();feed.thread.join(2)
            self.assertEqual(loader.call_count,1);self.assertEqual(feed.get()['value']['sfi'],0)
            other=Mock(side_effect=AssertionError('saved cache expected'))
            saved=r.PublicFeed(directory,'test',other,3600,43200).get()
            self.assertEqual(saved['value']['sfi'],0);self.assertFalse(saved['stale']);other.assert_not_called()
    def test_failed_refresh_retains_values_but_never_reports_them_fresh(self):
        with tempfile.TemporaryDirectory() as directory:
            loader=Mock(return_value={'timestamp':time.time(),'levels':{'R':0,'S':0,'G':0}})
            feed=r.PublicFeed(directory,'test',loader,120,900);feed.get();feed.thread.join(2)
            feed.loader=Mock(side_effect=OSError('PRIVATE_URL'));feed.next_attempt=0
            feed.get();feed.thread.join(2);value=feed.get()
            self.assertEqual(value['value']['levels']['R'],0);self.assertTrue(value['stale'])
            self.assertNotIn('PRIVATE_URL',json.dumps(value));self.assertFalse(value['loading'])
    def test_future_observations_and_future_cache_times_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            feed=r.PublicFeed(directory,'test',lambda now:{'timestamp':now+7200},300,1200)
            feed.get();feed.thread.join(2);self.assertIsNone(feed.get()['value'])
            r.write_private_json(Path(directory)/'test.json',{'schema':1,'fetchedAt':time.time()+86400,'value':{'timestamp':time.time(),'sfi':999}})
            restored=r.PublicFeed(directory,'test',lambda now:{'timestamp':now,'sfi':106},300,1200)
            self.assertIsNone(restored.value)


if __name__=='__main__':unittest.main()
