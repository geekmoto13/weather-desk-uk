from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from forecast_values import temperature_f, wind_mph

class ForecastValueTests(unittest.TestCase):
    def test_temperature_units_and_zero(self):
        self.assertEqual(temperature_f(0,'C'),32)
        self.assertEqual(temperature_f(0,'F'),0)
        self.assertEqual(temperature_f({'unitCode':'wmoUnit:degC','value':10}),50)
        for value,unit in [(True,'F'),(None,'F'),(10,'K'),('nan','F')]:
            self.assertIsNone(temperature_f(value,unit))

    def test_wind_range_uses_upper_hourly_speed_without_claiming_a_gust(self):
        self.assertEqual(wind_mph('5 to 15 mph'),15)
        self.assertEqual(wind_mph('0 mph'),0)
        self.assertAlmostEqual(wind_mph('16.09344 km/h'),10)
        self.assertAlmostEqual(wind_mph({'unitCode':'wmoUnit:m_s-1','value':10}),22.369362921)
        for value in ['10 to 5 mph','gusting 30 mph','Calm',True,None,'-3 mph',{'unitCode':'unknown','value':10}]:
            self.assertIsNone(wind_mph(value))

if __name__=='__main__':unittest.main()
