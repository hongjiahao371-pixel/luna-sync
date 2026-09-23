import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import time
import unittest

from PIL import Image, ImageDraw, ImageFilter


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def make_image(path, sharp=True, tint=(120, 120, 120)):
    img = Image.new('RGB', (400, 400), tint)
    d = ImageDraw.Draw(img)
    for x in range(0, 400, 8):
        d.line([(x, 0), (x, 400)], fill=255)
    if not sharp:
        img = img.filter(ImageFilter.GaussianBlur(8))
    img.convert('RGB').save(path, 'JPEG')


class ProjectsAutoSelectTests(unittest.TestCase):
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
            'web_port': 18771,
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
        for path in (self.web_app.PROJECTS_FILE, self.web_app.PICKS_FILE):
            if os.path.exists(path):
                os.remove(path)

    def write_photo(self, name, sharp=True):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        make_image(path, sharp=sharp)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_project_crud(self):
        self.write_photo('internal/a.jpg')
        created = self.client.post('/api/projects',
                                   json={'name': '越南之旅', 'files': ['internal/a.jpg']})
        self.assertEqual(created.status_code, 200)
        pid = created.get_json()['id']
        listing = self.client.get('/api/projects').get_json()['projects']
        self.assertEqual(listing[pid]['name'], '越南之旅')
        self.assertEqual(listing[pid]['files'], ['internal/a.jpg'])
        renamed = self.client.post('/api/projects/rename', json={'id': pid, 'name': '越南'})
        self.assertEqual(renamed.status_code, 200)
        added = self.client.post('/api/projects/add',
                                 json={'id': pid, 'files': ['internal/extra.jpg']})
        self.assertEqual(added.get_json()['count'], 2)
        removed = self.client.post('/api/projects/remove',
                                   json={'id': pid, 'files': ['internal/extra.jpg']})
        self.assertEqual(removed.get_json()['count'], 1)
        deleted = self.client.post('/api/projects/delete', json={'id': pid})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.client.get('/api/projects').get_json()['projects'], {})

    def test_project_invalid_args(self):
        self.assertEqual(self.client.post('/api/projects',
                                          json={'name': '', 'files': []}).status_code, 400)
        self.assertEqual(self.client.post('/api/projects/delete',
                                          json={'id': 'nope'}).status_code, 404)
        self.assertEqual(self.client.post('/api/projects/add',
                                          json={'id': 'nope', 'files': ['x']}).status_code, 404)

    def test_autoselect_scores_and_recommends(self):
        self.write_photo('internal/day/sharp1.jpg', sharp=True)
        self.write_photo('internal/day/sharp1_copy.jpg', sharp=True)
        self.write_photo('internal/day/blurry.jpg', sharp=False)
        started = self.client.post('/api/autoselect/start', json={'paths': [
            'internal/day/sharp1.jpg', 'internal/day/sharp1_copy.jpg',
            'internal/day/blurry.jpg']})
        self.assertEqual(started.status_code, 200)
        deadline = time.time() + 60
        status = {}
        while time.time() < deadline:
            status = self.client.get('/api/autoselect/status').get_json()
            if not status['running']:
                break
            time.sleep(0.2)
        self.assertFalse(status['running'])
        results = status['results']
        self.assertEqual(len(results), 3)
        s_sharp = results['internal/day/sharp1.jpg']['score']
        s_blur = results['internal/day/blurry.jpg']['score']
        self.assertGreater(s_sharp, s_blur)
        # the two near-duplicates share a burst cluster
        self.assertEqual(results['internal/day/sharp1.jpg']['cluster'],
                         results['internal/day/sharp1_copy.jpg']['cluster'])
        recommended = status['recommended']
        self.assertIn('internal/day/sharp1.jpg', recommended)
        self.assertNotIn('internal/day/blurry.jpg', recommended)
        # concurrent start is rejected while running: n/a after finish, but
        # empty start is rejected
        self.assertEqual(self.client.post('/api/autoselect/start',
                                          json={'paths': []}).status_code, 400)

    def test_autoselect_apply_sets_keep_marks(self):
        self.write_photo('internal/rec.jpg')
        response = self.client.post('/api/autoselect/apply',
                                    json={'paths': ['internal/rec.jpg']})
        self.assertEqual(response.status_code, 200)
        picks = self.client.get('/api/picks').get_json()['picks']
        self.assertEqual(picks['internal/rec.jpg']['mark'], 'keep')

if __name__ == '__main__':
    unittest.main()
