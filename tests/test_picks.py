import importlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def jpeg_bytes(width=16, height=16, color=(60, 120, 200)):
    import io
    buf = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buf, 'JPEG')
    return buf.getvalue()


class PhotoPicksTests(unittest.TestCase):
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
            'web_port': 18770,
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
        if os.path.exists(self.web_app.PICKS_FILE):
            os.remove(self.web_app.PICKS_FILE)
        for name in ('internal', 'external', '精选'):
            shutil.rmtree(os.path.join(self.web_app.DLDIR, name), ignore_errors=True)

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def set_mark(self, path, mark):
        return self.client.post('/api/picks',
                                json={'path': path, 'mark': mark})

    def test_set_get_and_clear_mark(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        response = self.set_mark('internal/a.jpg', 'keep')
        self.assertEqual(response.status_code, 200)
        picks = self.client.get('/api/picks').get_json()['picks']
        self.assertEqual(picks['internal/a.jpg']['mark'], 'keep')
        self.set_mark('internal/a.jpg', 'reject')
        picks = self.client.get('/api/picks').get_json()['picks']
        self.assertEqual(picks['internal/a.jpg']['mark'], 'reject')
        self.set_mark('internal/a.jpg', '')
        picks = self.client.get('/api/picks').get_json()['picks']
        self.assertNotIn('internal/a.jpg', picks)

    def test_invalid_mark_rejected(self):
        self.assertEqual(self.set_mark('internal/a.jpg', 'maybe').status_code, 400)
        self.assertEqual(self.set_mark('', 'keep').status_code, 400)

    def test_marks_persist_to_disk(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.set_mark('internal/a.jpg', 'keep')
        with open(self.web_app.PICKS_FILE) as f:
            data = json.load(f)
        self.assertEqual(data['internal/a.jpg']['mark'], 'keep')

    def test_export_copies_only_keepers(self):
        self.write_local('internal/keep1.jpg', jpeg_bytes(color=(10, 200, 10)))
        self.write_local('internal/keep2.jpg', jpeg_bytes(color=(200, 10, 10)))
        self.write_local('internal/skip.jpg', jpeg_bytes(color=(10, 10, 200)))
        self.set_mark('internal/keep1.jpg', 'keep')
        self.set_mark('internal/keep2.jpg', 'keep')
        self.set_mark('internal/skip.jpg', 'reject')
        response = self.client.post('/api/picks/export', json={'folder': '精选'})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['exported'], 2)
        self.assertEqual(data['missing'], 0)
        dest = os.path.join(self.web_app.DLDIR, '精选')
        exported = sorted(os.listdir(dest))
        self.assertEqual(exported, ['keep1.jpg', 'keep2.jpg'])

    def test_export_rejects_traversal_folder(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.set_mark('internal/a.jpg', 'keep')
        for folder in ('../evil', 'a/b', '..', ''):
            response = self.client.post('/api/picks/export', json={'folder': folder})
            self.assertEqual(response.status_code, 400, folder)

    def test_missing_keeper_counted_not_fatal(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = self.web_app.PRIVACY_VERSION
        with picks_lock(self.web_app):
            self.web_app.save_picks({'internal/ghost.jpg': {'mark': 'keep', 'ts': 1}})
        response = self.client.post('/api/picks/export', json={'folder': '精选'})
        data = response.get_json()
        self.assertEqual(data['exported'], 0)
        self.assertEqual(data['missing'], 1)

    def test_delete_file_drops_pick_entry(self):
        self.write_local('internal/gone.jpg', jpeg_bytes())
        self.set_mark('internal/gone.jpg', 'keep')
        response = self.client.delete('/api/file/internal/gone.jpg')
        self.assertEqual(response.status_code, 200)
        picks = self.client.get('/api/picks').get_json()['picks']
        self.assertNotIn('internal/gone.jpg', picks)

    def test_clear_all(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.set_mark('internal/a.jpg', 'keep')
        self.set_mark('internal/b.jpg', 'reject')
        self.assertEqual(self.client.post('/api/picks/clear').status_code, 200)
        self.assertEqual(self.client.get('/api/picks').get_json()['picks'], {})


def picks_lock(web_app):
    return web_app.picks_lk


if __name__ == '__main__':
    unittest.main()
