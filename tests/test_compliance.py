import importlib
import importlib.util
import json
import os
import subprocess
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

    def test_managed_wifi_requires_saved_or_luna_ssid(self):
        app = self.web_app
        original_requires = app.wifi.requires_target_ssid
        original_current = app.current_ssid
        original_cam_on = app.cam_on
        original_ensure = app.ensure_camera_ipv4
        try:
            app.wifi.requires_target_ssid = lambda: True
            app.cam_on = lambda: True
            app.ensure_camera_ipv4 = lambda: None
            with app.lk:
                app.ST['wifi_target'] = 'Luna Ultra TEST'
            app.current_ssid = lambda: 'Home WiFi'
            self.assertFalse(app.wifi_on_target())
            app.current_ssid = lambda: 'Luna Ultra TEST'
            self.assertTrue(app.wifi_on_target())
            with app.lk:
                app.ST['wifi_target'] = ''
            app.current_ssid = lambda: 'Luna Ultra DIRECT'
            self.assertTrue(app.wifi_on_target())
        finally:
            app.wifi.requires_target_ssid = original_requires
            app.current_ssid = original_current
            app.cam_on = original_cam_on
            app.ensure_camera_ipv4 = original_ensure
            with app.lk:
                app.ST['wifi_target'] = app.CAM_SSID

    def test_manual_wifi_check_does_not_probe_camera_twice(self):
        app = self.web_app
        original_requires = app.wifi.requires_target_ssid
        original_cam_on = app.cam_on
        try:
            app.wifi.requires_target_ssid = lambda: False
            app.cam_on = lambda: self.fail('camera reachability is checked by the caller')
            self.assertTrue(app.wifi_on_target())
        finally:
            app.wifi.requires_target_ssid = original_requires
            app.cam_on = original_cam_on

    def test_connected_state_requires_two_consecutive_probe_failures(self):
        app = self.web_app
        connected, failures = app.debounced_connection(False, True, 0)
        self.assertTrue(connected)
        self.assertEqual(failures, 1)
        connected, failures = app.debounced_connection(False, connected, failures)
        self.assertFalse(connected)
        self.assertEqual(failures, 2)
        self.assertEqual(app.debounced_connection(True, False, failures), (True, 0))

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

    def test_cancelled_connection_wait_is_not_requeued(self):
        app = self.web_app
        try:
            with app.lk:
                app.ST['privacy_version'] = app.PRIVACY_VERSION
                app.ST['auto_sync'] = True
                app.ST['queue'] = []
                app.ST['active_key'] = 'internal/waiting.mp4'
                app.ST['current'] = None
                app.auto_downloads.clear()
                app.auto_downloads.add('internal/waiting.mp4')
                app.cancel.set()
            self.assertTrue(app.postpone_unavailable_download('internal/waiting.mp4', 'auto'))
            with app.lk:
                self.assertEqual(app.ST['queue'], [])
                self.assertIsNone(app.ST['active_key'])
                self.assertNotIn('internal/waiting.mp4', app.auto_downloads)
            self.assertFalse(app.cancel.is_set())
        finally:
            with app.lk:
                app.ST['queue'] = []
                app.ST['active_key'] = None
                app.ST['current'] = None
                app.auto_downloads.clear()
                app.cancel.clear()

    def test_wrong_sized_local_file_can_be_queued_again(self):
        app = self.web_app
        path = os.path.join(app.DLDIR, 'internal', 'mismatch.mp4')
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, 'wb') as f:
                f.write(b'bad')
            item = {
                'id': 'internal/mismatch.mp4',
                'name': 'mismatch.mp4',
                'storage': 'internal',
                'url': 'http://camera/mismatch.mp4',
                'bytes': 6,
                'bytes_exact': True,
            }
            with app.lk:
                app.ST['privacy_version'] = app.PRIVACY_VERSION
                app.ST['files'] = [item]
                app.ST['queue'] = []
                app.ST['current'] = None
            added, skipped = app.enqueue([item['id']])
            self.assertEqual(added, 1)
            self.assertEqual(skipped, [])
        finally:
            if os.path.exists(path):
                os.remove(path)
            with app.lk:
                app.ST['files'] = []
                app.ST['queue'] = []
                app.ST['current'] = None

    def test_dng_preview_is_rendered_as_jpeg(self):
        app = self.web_app
        name = 'internal/raw.dng'
        source = os.path.join(app.DLDIR, name)
        original_run = app.run
        os.makedirs(os.path.dirname(source), exist_ok=True)
        try:
            with open(source, 'wb') as f:
                f.write(b'dng')

            def fake_run(args, _timeout=30):
                with open(args[-1], 'wb') as output:
                    output.write(b'\xff\xd8\xff\xd9')
                return subprocess.CompletedProcess(args, 0, '', '')

            app.run = fake_run
            with app.lk:
                app.ST['privacy_version'] = app.PRIVACY_VERSION
            response = self.client.get('/thumb/' + name)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, 'image/jpeg')
            response.close()
            home = self.client.get('/')
            self.assertIn("'DNG'", home.get_data(as_text=True))
            home.close()
        finally:
            app.run = original_run
            for path in (
                source,
                os.path.join(app.THUMB_DIR, name + '.jpg'),
                os.path.join(app.THUMB_DIR, name + '.jpg.part.jpg'),
            ):
                if os.path.exists(path):
                    os.remove(path)

    def test_legal_pages_are_public(self):
        self.assertIn('Luna Sync 隐私政策', self.client.get('/privacy').get_data(as_text=True))
        self.assertIn('Luna Sync 用户协议', self.client.get('/terms').get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
