import importlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

from PIL import Image


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def jpeg_bytes(color=(60, 120, 200)):
    buf = io.BytesIO()
    Image.new('RGB', (32, 32), color).save(buf, 'JPEG')
    return buf.getvalue()


class TrashTests(unittest.TestCase):
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
            'web_port': 18772,
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
        shutil.rmtree(self.web_app.TRASH_DIR, ignore_errors=True)
        os.makedirs(self.web_app.TRASH_DIR, exist_ok=True)
        p = os.path.join(self.web_app.DLDIR, 'internal', 'item.jpg')
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, 'wb') as f:
            f.write(jpeg_bytes())

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_delete_moves_to_trash_and_list_shows_original_name(self):
        response = self.client.delete('/api/file/internal/item.jpg')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['trashed'])
        self.assertFalse(os.path.exists(os.path.join(self.web_app.DLDIR, 'internal', 'item.jpg')))
        items = self.client.get('/api/trash').get_json()['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['name'], 'internal/item.jpg')
        self.assertGreater(items[0]['size'], 0)

    def test_restore_returns_file_to_exact_original_path(self):
        self.client.delete('/api/file/internal/item.jpg')
        items = self.client.get('/api/trash').get_json()['items']
        restored = self.client.post('/api/trash/restore',
                                    json={'unique': items[0]['unique']})
        self.assertEqual(restored.get_json()['restored'], ['internal/item.jpg'])
        self.assertTrue(os.path.isfile(os.path.join(self.web_app.DLDIR,
                                                    'internal', 'item.jpg')))
        self.assertEqual(self.client.get('/api/trash').get_json()['items'], [])

    def test_purge_removes_entry_forever(self):
        self.client.delete('/api/file/internal/item.jpg')
        items = self.client.get('/api/trash').get_json()['items']
        purged = self.client.post('/api/trash/purge',
                                  json={'unique': items[0]['unique']})
        self.assertEqual(purged.get_json()['purged'], 1)
        self.assertEqual(self.client.get('/api/trash').get_json()['items'], [])

    def test_expired_trash_is_cleaned_on_list(self):
        self.client.delete('/api/file/internal/item.jpg')
        items = self.client.get('/api/trash').get_json()['items']
        stamp = os.path.join(self.web_app.TRASH_DIR, items[0]['unique'], '.trashed-at')
        old = time.time() - 30 * 86400
        os.utime(stamp, (old, old))
        items = self.client.get('/api/trash').get_json()['items']
        self.assertEqual(items, [], 'expired trash must be purged automatically')

    def test_purge_all(self):
        self.client.delete('/api/file/internal/item.jpg')
        self.write_local('internal/second.jpg', jpeg_bytes(color=(9, 90, 90)))
        self.client.delete('/api/file/internal/second.jpg')
        purged = self.client.post('/api/trash/purge', json={'unique': 'all'})
        self.assertGreaterEqual(purged.get_json()['purged'], 2)
        self.assertEqual(self.client.get('/api/trash').get_json()['items'], [])


class ExifTests(unittest.TestCase):
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
            'web_port': 18773,
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
        with cls.web_app.lk:
            cls.web_app.ST['privacy_version'] = cls.web_app.PRIVACY_VERSION

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_exif_fields_mapped(self):
        img = Image.new('RGB', (400, 300), (100, 150, 100))
        exif = Image.Exif()
        exif[272] = 'Insta360 Luna Ultra'
        exif[33437] = (17, 10)
        exif[34855] = 800
        exif[37386] = (62, 10)
        exif[33434] = (1, 125)
        path = os.path.join(self.web_app.DLDIR, 'exif.jpg')
        with open(path, 'wb') as f:
            img.save(f, 'JPEG', exif=exif.tobytes())
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        meta = self.client.get('/api/exif/exif.jpg').get_json()
        self.assertEqual(meta['model'], 'Insta360 Luna Ultra')
        self.assertEqual(meta['fnumber'], 1.7)
        self.assertEqual(meta['iso'], 800)
        self.assertEqual(meta['focal'], 6)
        self.assertEqual(meta['exposure'], '1/125')

    def test_exif_absent_returns_empty(self):
        path = os.path.join(self.web_app.DLDIR, 'plain.jpg')
        with open(path, 'wb') as f:
            Image.new('RGB', (16, 16)).save(f, 'JPEG')
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        self.assertEqual(self.client.get('/api/exif/plain.jpg').get_json(), {})


if __name__ == '__main__':
    unittest.main()
