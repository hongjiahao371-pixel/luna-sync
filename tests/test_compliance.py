import importlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest


class ComplianceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('flask') is None:
            raise unittest.SkipTest('Flask is not installed in this Python environment')
        cls.tempdir = tempfile.TemporaryDirectory()
        root = cls.tempdir.name
        config = {
            'camera_host': '192.0.2.1',
            'camera_ssid': '',
            'camera_password': '',
            'wifi_backend': 'none',
            'wifi_iface': None,
            'download_dir': os.path.join(root, 'downloads'),
            'state_dir': os.path.join(root, 'state'),
            'web_port': 18767,
        }
        config_path = os.path.join(root, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(config, f)
        os.environ['LUNA_CONFIG'] = config_path
        os.environ['DOWNLOAD_DIR'] = config['download_dir']
        os.environ['STATE_DIR'] = config['state_dir']
        os.environ['LUNA_WIFI_BACKEND'] = 'none'
        app_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'app'))
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        cls.web_app = importlib.import_module('web_app')
        cls.client = cls.web_app.app.test_client()

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = ''

    def test_sensitive_routes_are_blocked_before_consent(self):
        state = self.client.get('/api/state')
        self.assertEqual(state.status_code, 200)
        self.assertFalse(state.get_json()['privacy_accepted'])
        blocked = self.client.get('/api/files')
        self.assertEqual(blocked.status_code, 451)
        self.assertEqual(blocked.get_json()['error'], 'privacy_consent_required')

    def test_consent_must_be_explicit_and_is_persisted(self):
        rejected = self.client.post('/api/privacy', json={'accepted': False})
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post('/api/privacy', json={'accepted': True})
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.get_json()['accepted'])
        with open(self.web_app.SETTINGS_FILE) as f:
            settings = json.load(f)
        self.assertEqual(settings['privacy_version'], self.web_app.PRIVACY_VERSION)

    def test_disabling_auto_sync_stops_only_auto_downloads(self):
        app = self.web_app
        try:
            with app.lk:
                app.ST['privacy_version'] = app.PRIVACY_VERSION
                app.ST['auto_sync'] = True
                app.ST['queue'] = ['auto/queued.mp4', 'manual/queued.mp4']
                app.ST['active_key'] = 'auto/current.mp4'
                app.ST['current'] = {
                    'id': 'auto/current.mp4',
                    'name': 'current.mp4',
                    'source': 'auto',
                }
                app.auto_downloads.clear()
                app.auto_downloads.update({'auto/queued.mp4', 'auto/current.mp4'})
                app.cancel.clear()
            response = self.client.post('/api/auto-sync', json={'enabled': False})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()['removed'], 1)
            self.assertTrue(response.get_json()['cancelled'])
            with app.lk:
                self.assertEqual(app.ST['queue'], ['manual/queued.mp4'])
                self.assertFalse(app.ST['auto_sync'])
            self.assertTrue(app.cancel.is_set())
        finally:
            with app.lk:
                app.ST['queue'] = []
                app.ST['active_key'] = None
                app.ST['current'] = None
                app.auto_downloads.clear()
                app.cancel.clear()

    def test_auto_sync_does_not_rescan_while_queue_is_active(self):
        app = self.web_app
        original_prepare = app.prepare_auto_sync_connection
        try:
            with app.lk:
                app.ST['privacy_version'] = app.PRIVACY_VERSION
                app.ST['auto_sync'] = True
                app.ST['queue'] = ['auto/pending.mp4']
            app.prepare_auto_sync_connection = lambda: self.fail('must not reconnect while downloads are pending')
            self.assertEqual(app.auto_sync_once(), 0)
        finally:
            app.prepare_auto_sync_connection = original_prepare
            with app.lk:
                app.ST['queue'] = []
                app.ST['auto_sync'] = False

    def test_cancel_clears_queue_and_stops_active_download(self):
        app = self.web_app
        try:
            with app.lk:
                app.ST['privacy_version'] = app.PRIVACY_VERSION
                app.ST['auto_sync'] = False
                app.ST['queue'] = ['manual/queued.mp4', 'auto/queued.mp4']
                app.ST['active_key'] = 'manual/current.mp4'
                app.ST['current'] = None
                app.auto_downloads.clear()
                app.auto_downloads.add('auto/queued.mp4')
                app.cancel.clear()
            response = self.client.post('/api/cancel')
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()['cancelled'])
            self.assertEqual(response.get_json()['removed'], 2)
            with app.lk:
                self.assertEqual(app.ST['queue'], [])
                self.assertEqual(app.auto_downloads, set())
            self.assertTrue(app.cancel.is_set())
        finally:
            with app.lk:
                app.ST['queue'] = []
                app.ST['active_key'] = None
                app.ST['current'] = None
                app.auto_downloads.clear()
                app.cancel.clear()

    def test_cancel_when_idle_does_not_arm_next_download(self):
        app = self.web_app
        with app.lk:
            app.ST['privacy_version'] = app.PRIVACY_VERSION
            app.ST['queue'] = []
            app.ST['active_key'] = None
            app.ST['current'] = None
            app.cancel.clear()
        response = self.client.post('/api/cancel')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['cancelled'])
        self.assertEqual(response.get_json()['removed'], 0)
        self.assertFalse(app.cancel.is_set())

    def test_legal_pages_are_public(self):
        self.assertIn('Luna Sync 隐私政策', self.client.get('/privacy').get_data(as_text=True))
        self.assertIn('Luna Sync 用户协议', self.client.get('/terms').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
