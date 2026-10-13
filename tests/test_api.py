import os, tempfile, unittest

_fd, _db_path = tempfile.mkstemp(suffix='.sqlite3'); os.close(_fd)
os.environ['SMART_PLANT_DB'] = _db_path
from backend.app import app

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = app.test_client()
    def test_health(self):
        r = self.client.get('/api/health'); self.assertEqual(r.status_code,200); self.assertTrue(r.json['ok'])
    def test_latest_empty(self):
        self.assertEqual(self.client.get('/api/readings/latest').status_code,404)
    def test_settings_validation(self):
        self.assertEqual(self.client.post('/api/settings',json={'threshold':99}).status_code,400)
    def test_reading_roundtrip(self):
        r = self.client.post('/api/device/reading',json={'moisture':42,'temperature':25.2,'humidity':55,'pump':False,'mode':'auto','reservoir':75,'raw_moisture':2100})
        self.assertEqual(r.status_code,200)
        latest = self.client.get('/api/readings/latest')
        self.assertEqual(latest.status_code,200); self.assertEqual(latest.json['moisture'],42)
        hist = self.client.get('/api/history?hours=24'); self.assertEqual(hist.status_code,200)
    def test_invalid_reading(self):
        self.assertEqual(self.client.post('/api/device/reading',json={'moisture':150}).status_code,400)
    def test_manual_pump_requires_manual_mode(self):
        self.client.post('/api/device/reading',json={'moisture':42,'mode':'auto','reservoir':80})
        self.assertEqual(self.client.post('/api/pump',json={'state':'on'}).status_code,409)

if __name__ == '__main__': unittest.main()
