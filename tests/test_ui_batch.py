import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def jpeg_bytes(width=32, height=32, color=(60, 120, 200)):
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, 'JPEG')
    return buf.getvalue()


class UiBatchTests(unittest.TestCase):
    """Covers the 9-item UI batch: share thumbs, XMP zip download,
    photos-only auto cut."""

    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('flask') is None:
            raise unittest.SkipTest('Flask is not installed in this Python environment')
        cls.tempdir = tempfile.TemporaryDirectory()
        root = cls.tempdir.name
        config = {
            'camera_host': '192.0.2.1', 'camera_ssid': '', 'camera_password': '',
            'wifi_backend': 'none', 'wifi_iface': None,
            'download_dir': os.path.join(root, 'downloads'),
            'state_dir': os.path.join(root, 'state'), 'web_port': 18776,
        }
        config_path = os.path.join(root, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(config, f)
        os.environ['LUNA_CONFIG'] = config_path
        os.environ['DOWNLOAD_DIR'] = config['download_dir']
        os.environ['STATE_DIR'] = config['state_dir']
        os.environ['LUNA_WIFI_BACKEND'] = 'none'
        app_dir = os.path.abspath(os.path.join(REPO, 'app'))
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        cls.web_app = importlib.import_module('web_app')
        cls.web_app.SETTINGS['web_password'] = cls.web_app.hash_password('test-pass')
        cls.client = cls.web_app.app.test_client()
        assert cls.client.post('/api/auth/login', json={'password': 'test-pass'}).status_code == 200

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = self.web_app.PRIVACY_VERSION
        for f in (self.web_app.PICKS_FILE, self.web_app.PROJECTS_FILE, self.web_app.SHARES_FILE):
            if os.path.exists(f):
                os.remove(f)

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def set_mark(self, path, mark):
        return self.client.post('/api/picks', json={'path': path, 'mark': mark})

    def test_share_thumb_serves_small_image(self):
        self.write_local('internal/big.jpg', jpeg_bytes(1200, 800, (10, 100, 10)))
        self.write_local('internal/small.jpg', jpeg_bytes(40, 40, (100, 10, 10)))
        token = self.client.post('/api/share/create', json={
            'paths': ['internal/big.jpg', 'internal/small.jpg'], 'days': 1}).get_json()['token']
        page = self.client.get('/share/' + token)
        self.assertEqual(page.status_code, 200)
        thumb = self.client.get('/share/%s/thumb/0' % token)
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.mimetype, 'image/jpeg')
        self.assertLess(len(thumb.data), len(self.client.get('/share/%s/img/0' % token).data),
                        'thumb must be smaller than the original')
        # out of range / bad token
        self.assertEqual(self.client.get('/share/%s/thumb/9' % token).status_code, 404)
        self.assertEqual(self.client.get('/share/nope/thumb/0').status_code, 404)

    def test_xmp_zip_download(self):
        self.write_local('internal/keep1.jpg', jpeg_bytes(color=(10, 200, 10)))
        self.write_local('internal/rej1.jpg', jpeg_bytes(color=(200, 10, 10)))
        self.set_mark('internal/keep1.jpg', 'keep')
        self.set_mark('internal/rej1.jpg', 'reject')
        response = self.client.post('/api/picks/xmp/download', json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/zip')
        z = zipfile.ZipFile(io.BytesIO(response.data))
        names = z.namelist()
        # basenames only — Lightroom-friendly flat layout
        self.assertIn('keep1.jpg.xmp', names)
        self.assertIn('rej1.jpg.xmp', names)
        content = z.read('keep1.jpg.xmp').decode()
        self.assertIn('xmp:Rating="5"', content)
        # no marks -> 400
        self.set_mark('internal/keep1.jpg', '')
        self.set_mark('internal/rej1.jpg', '')
        self.assertEqual(self.client.post('/api/picks/xmp/download', json={}).status_code, 400)

    def test_autocut_accepts_photos_only(self):
        self.write_local('internal/p1.jpg', jpeg_bytes(color=(10, 200, 10)))
        self.write_local('internal/p2.jpg', jpeg_bytes(color=(200, 10, 10)))
        response = self.client.post('/api/autocut/start', json={
            'videos': [], 'photos': ['internal/p1.jpg', 'internal/p2.jpg'], 'duration': 15})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])

    def test_autocut_rejects_empty(self):
        self.assertEqual(self.client.post('/api/autocut/start', json={
            'videos': [], 'photos': []}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
