"""Met Éireann hourly means must not be labelled ten-minute means."""
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ireland_sources import ten_minute_wind, parse_observations, rain_ledger

END=1792893600
def fixture():
    return {'collection_id':'observations-swob-nrt-1min','station_id':'102','items':[
        {'parameter_name':'wind_speed','observed_at':dt.datetime.fromtimestamp(END-i*60,dt.timezone.utc).isoformat(),
         'value_num':i+1,'sensor_source':'suit_a'} for i in range(10)]}

class IrishWindMeanTests(unittest.TestCase):
    def test_ten_one_minute_means_make_the_ten_minute_scalar_mean(self):
        value=ten_minute_wind(fixture(),'0102',END)
        self.assertAlmostEqual(value['windMean10mMph'],5.5*1.150779448)
        self.assertEqual(value['windMean10mTimestamp'],END)
    def test_missing_minute_or_invalid_sensor_reading_cannot_be_filled_as_zero(self):
        for change in ('missing','bad','other-sensor'):
            raw=fixture()
            if change=='missing':raw['items'].pop(4)
            elif change=='bad':raw['items'][4]['value_num']=-99
            else:raw['items'][4]['sensor_source']='suit_b'
            self.assertEqual(ten_minute_wind(raw,'0102',END),{})
    def test_other_station_wrong_period_or_truncated_response_is_rejected(self):
        for changes in ({'station_id':'518'},{'collection_id':'observations-swob-nrt-60min'},{'numberMatched':100}):
            raw=fixture();raw.update(changes)
            self.assertEqual(ten_minute_wind(raw,'0102',END),{})
    def test_conflicting_duplicate_minute_is_not_averaged(self):
        raw=fixture();row=dict(raw['items'][2]);row['value_num']=100;raw['items'].append(row)
        self.assertEqual(ten_minute_wind(raw,'0102',END),{})
    def test_filtered_live_api_shape_inherits_the_top_level_parameter_name(self):
        raw=fixture();raw['parameter_name']='wind_speed'
        for item in raw['items']:item.pop('parameter_name')
        self.assertAlmostEqual(ten_minute_wind(raw,'0102',END)['windMean10mMph'],5.5*1.150779448)
    def test_hourly_weather_wind_retains_its_own_period(self):
        raw={'collection_id':'observations-swob-nrt-60min','items':[
            {'parameter_name':field,'value_num':value,'observed_at':dt.datetime.fromtimestamp(END,dt.timezone.utc).isoformat()}
            for field,value in [('air_temperature',10),('wind_speed_mean',20),('precipitation_amount',0)]]}
        row=parse_observations(raw,lambda *a:(None,''),END)[0]
        self.assertNotIn('windMean10mMph',row)
        self.assertEqual(rain_ledger([row],{'stationName':'Test'},END)['rainSince']['status'],'at_least')

if __name__=='__main__':unittest.main()
