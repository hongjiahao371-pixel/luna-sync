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


class TagsTests(unittest.TestCase):
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
        if os.path.exists(self.web_app.TAGS_FILE):
            os.remove(self.web_app.TAGS_FILE)

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def assign(self, files, add=None, remove=None):
        return self.client.post('/api/tags/assign',
                                json={'files': files, 'add': add or [], 'remove': remove or []})

    def test_assign_add_and_list_counts(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.write_local('internal/b.jpg', jpeg_bytes(color=(200, 10, 10)))
        response = self.assign(['internal/a.jpg', 'internal/b.jpg'], add=['越南'])
        self.assertEqual(response.status_code, 200)
        response = self.assign(['internal/a.jpg'], add=['美食'])
        self.assertEqual(response.status_code, 200)
        tags = self.client.get('/api/tags').get_json()['tags']
        by_name = {t['name']: t['count'] for t in tags}
        self.assertEqual(by_name.get('越南'), 2)
        self.assertEqual(by_name.get('美食'), 1)

    def test_local_files_carry_tags(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.assign(['internal/a.jpg'], add=['风景'])
        items = self.client.get('/api/local-files').get_json()['items']
        item = [x for x in items if x['id'] == 'internal/a.jpg'][0]
        self.assertEqual(item['tags'], ['风景'])

    def test_remove_tag_and_empty_entry_dropped(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.assign(['internal/a.jpg'], add=['风景', '美食'])
        self.assign(['internal/a.jpg'], remove=['风景'])
        with open(self.web_app.TAGS_FILE) as f:
            data = json.load(f)
        self.assertEqual(data['internal/a.jpg'], ['美食'])
        self.assign(['internal/a.jpg'], remove=['美食'])
        with open(self.web_app.TAGS_FILE) as f:
            data = json.load(f)
        self.assertNotIn('internal/a.jpg', data)

    def test_assign_unknown_file_skipped(self):
        response = self.assign(['internal/ghost.jpg'], add=['风景'])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get('/api/tags').get_json()['tags'], [])

    def test_invalid_names_rejected(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        for bad in ('', '   ', 'x' * 25, 'a/b', 'a\\b', 'a"b', "a'b", 'a#b', 'a\nb'):
            response = self.assign(['internal/a.jpg'], add=[bad])
            self.assertEqual(response.status_code, 400, 'tag %r must be rejected' % bad)
        tags = self.client.get('/api/tags').get_json()['tags']
        self.assertEqual(tags, [])

    def test_add_and_remove_conflict(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        response = self.assign(['internal/a.jpg'], add=['风景'], remove=['风景'])
        self.assertEqual(response.status_code, 400)

    def test_missing_files_list_rejected(self):
        self.assertEqual(self.assign([], add=['风景']).status_code, 400)

    def test_delete_tag_everywhere(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.write_local('internal/b.jpg', jpeg_bytes(color=(200, 10, 10)))
        self.assign(['internal/a.jpg', 'internal/b.jpg'], add=['旧标签'])
        self.assign(['internal/a.jpg'], add=['保留'])
        response = self.client.post('/api/tags/delete', json={'name': '旧标签'})
        self.assertEqual(response.status_code, 200)
        tags = self.client.get('/api/tags').get_json()['tags']
        names = {t['name'] for t in tags}
        self.assertNotIn('旧标签', names)
        self.assertIn('保留', names)
        items = self.client.get('/api/local-files').get_json()['items']
        b = [x for x in items if x['id'] == 'internal/b.jpg'][0]
        self.assertEqual(b['tags'], [])

    def test_withdraw_cleans_tags(self):
        # withdraw (privacy) must remove tag metadata together with picks;
        # keep consent ACTIVE when calling or the request is gated with 451
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.assign(['internal/a.jpg'], add=['私密'])
        response = self.client.post('/api/privacy/withdraw')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(self.web_app.TAGS_FILE))

    def test_tag_file_permissions(self):
        self.write_local('internal/a.jpg', jpeg_bytes())
        self.assign(['internal/a.jpg'], add=['风景'])
        mode = os.stat(self.web_app.TAGS_FILE).st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == '__main__':
    unittest.main()
