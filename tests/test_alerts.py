"""Met Office warnings remain independent of saved personal-station keys."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import server


class UKAlertTests(unittest.TestCase):
    def test_uk_alerts_need_no_ambient_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            desk = server.Desk(folder)
            try:
                with patch.object(server, 'fetch_json', return_value={"type":"FeatureCollection","features":[]}) as fetch:
                    result = desk.alerts()
                self.assertFalse(result['stale'])
                self.assertEqual(desk.api_key, '')
                self.assertEqual(set(parse_qs(urlsplit(fetch.call_args.args[0]).query)), {'where','outFields','outSR','f'})
            finally:
                desk.db.close()


if __name__=="__main__":unittest.main()
