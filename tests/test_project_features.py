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


class ProjectFeaturesTests(unittest.TestCase):
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
        if os.path.exists(self.web_app.PROJECTS_FILE):
            os.remove(self.web_app.PROJECTS_FILE)

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def create(self, name, files=None, parent=''):
        return self.client.post('/api/projects', json={'name': name, 'files': files or [], 'parent': parent})

    def test_create_with_parent_and_invalid_parent(self):
        parent = self.create('父项目').get_json()['id']
        child = self.create('子项目', parent=parent).get_json()
        self.assertTrue(child['ok'])
        projects = self.client.get('/api/projects').get_json()['projects']
        self.assertEqual(projects[child['id']]['parent'], parent)
        self.assertEqual(projects[parent]['parent'], '')
        self.assertEqual(self.create('孤儿', parent='p_nonexistent').status_code, 400)

    def test_delete_parent_with_children_refused(self):
        parent = self.create('父项目').get_json()['id']
        self.create('子项目', parent=parent)
        response = self.client.post('/api/projects/delete', json={'id': parent})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()['error'], 'has_children')
        # leaf deletes fine
        projects = self.client.get('/api/projects').get_json()['projects']
        child = [k for k, v in projects.items() if v['name'] == '子项目'][0]
        self.assertEqual(self.client.post('/api/projects/delete', json={'id': child}).status_code, 200)

    def test_set_cover_roundtrip_and_clear(self):
        pid = self.create('封面项目').get_json()['id']
        response = self.client.post('/api/projects/cover', json={'id': pid, 'file': 'internal/a.jpg'})
        self.assertEqual(response.status_code, 200)
        projects = self.client.get('/api/projects').get_json()['projects']
        self.assertEqual(projects[pid]['cover'], 'internal/a.jpg')
        self.client.post('/api/projects/cover', json={'id': pid, 'file': ''})
        projects = self.client.get('/api/projects').get_json()['projects']
        self.assertNotIn('cover', projects[pid])

    def test_haversine_known_distance(self):
        # Da Nang ~ 17km from Hue city center
        d = self.web_app._haversine_km(16.0544, 108.2022, 16.2037, 108.1484)
        self.assertAlmostEqual(d, 17.5, delta=2.0)

    def test_auto_gps_adds_only_within_radius(self):
        self.write_local('internal/near.jpg', jpeg_bytes(color=(10, 200, 10)))
        self.write_local('internal/far.jpg', jpeg_bytes(color=(200, 10, 10)))
        self.write_local('internal/member.jpg', jpeg_bytes(color=(10, 10, 200)))
        pid = self.create('越南行', files=['internal/member.jpg']).get_json()['id']
        # monkeypatch GPS reads: near ~ 5km from center, far ~ 300km
        real_read = self.web_app._read_gps

        def fake_read(path):
            if path.endswith('near.jpg'):
                return {'lat': 16.10, 'lon': 108.20}
            if path.endswith('far.jpg'):
                return {'lat': 13.78, 'lon': 109.22}
            return {}

        self.web_app._read_gps = fake_read
        try:
            response = self.client.post('/api/projects/auto-gps', json={
                'id': pid, 'lat': 16.0544, 'lng': 108.2022, 'radius_km': 50})
        finally:
            self.web_app._read_gps = real_read
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data['added'], 1)
        self.assertEqual(data['total'], 2)
        projects = self.client.get('/api/projects').get_json()['projects']
        self.assertIn('internal/near.jpg', projects[pid]['files'])
        self.assertNotIn('internal/far.jpg', projects[pid])
        self.assertEqual(projects[pid]['gps_rule']['radius_km'], 50)

    def test_same_millisecond_creates_do_not_collide(self):
        # regression: two creates in the same ms used to overwrite each other
        import time as time_mod
        created = []
        real_module = self.web_app.time

        class _FrozenTime:
            @staticmethod
            def time():
                return 1790000000.0

            @staticmethod
            def strftime(fmt, tup=None):
                return '2026-01-01'

        self.web_app.time = _FrozenTime
        try:
            for i in range(3):
                r = self.create('项目%d' % i).get_json()
                created.append(r['id'])
        finally:
            self.web_app.time = real_module
            self.assertEqual(time_mod.time, time_mod.time)
        self.assertEqual(len(set(created)), 3, 'ids must be unique')
        projects = self.client.get('/api/projects').get_json()['projects']
        self.assertEqual(len(projects), 3)

    def test_auto_gps_rejects_bad_coords(self):
        pid = self.create('坐标项目').get_json()['id']
        self.assertEqual(self.client.post('/api/projects/auto-gps', json={
            'id': pid, 'lat': 999, 'lng': 0, 'radius_km': 50}).status_code, 400)
        self.assertEqual(self.client.post('/api/projects/auto-gps', json={
            'id': pid, 'lat': 1, 'lng': 1, 'radius_km': 5000}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
