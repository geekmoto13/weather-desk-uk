import datetime as dt
from pathlib import Path
import sys,unittest
from unittest.mock import Mock,patch
from urllib.parse import urlsplit,parse_qs
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from uk_sources import UKForecast
from ireland_sources import parse_forecast

class RegionalOutlookTests(unittest.TestCase):
    def test_uk_requests_units_and_a_complete_48_hour_buffer(self):
        now=dt.datetime(2026,11,7,12,tzinfo=dt.timezone.utc).timestamp()
        raw={'hourly':{'time':[now+i*3600 for i in range(60)],'weather_code':[3]*60,
                       'precipitation_probability':[0]*60,'temperature_2m':[32]*60,'wind_speed_10m':[0]*60}}
        fetch=Mock(return_value=raw)
        with patch('uk_sources.time.time',return_value=now):value=UKForecast(fetch).get(52.77,-1.56)
        params=parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(params['temperature_unit'],['fahrenheit'])
        self.assertEqual(params['wind_speed_unit'],['mph'])
        self.assertGreaterEqual(value['periods'][-1]['end'],now+48*3600)
        self.assertEqual(value['periods'][0]['tempF'],32)
        self.assertEqual(value['periods'][0]['windMph'],0)
        self.assertEqual(value['periods'][0]['pop'],0)

    def test_irish_point_forecast_converts_documented_celsius_and_kmh(self):
        now=dt.datetime(2026,11,7,12,59,tzinfo=dt.timezone.utc)
        raw=[]
        for i in range(1,51):
            t=now.replace(minute=0)+dt.timedelta(hours=i)
            raw.append({'date':t.strftime('%Y-%m-%d'),'time':t.strftime('%H:%M'),
                        'weatherDescription':'Fair','temperature':10,'wind-speed':16.09344})
        rows=parse_forecast(raw,now.timestamp())
        self.assertGreaterEqual(rows[-1]['end'],now.timestamp()+49*3600)
        self.assertEqual(rows[0]['tempF'],50)
        self.assertAlmostEqual(rows[0]['windMph'],10)
        self.assertIsNone(rows[0]['pop'])

if __name__=='__main__':unittest.main()
