import importlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

FAKE_MP4 = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom' + b'\x00' * 32


def jpeg_bytes(width=8, height=8, color=(180, 60, 60)):
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, 'JPEG')
    return buffer.getvalue()


class LivePhotoPreviewTests(unittest.TestCase):
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
            'web_port': 18769,
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
        cls.photo = jpeg_bytes()
        cls.web_app.SETTINGS['web_password'] = cls.web_app.hash_password('test-pass')
        cls.client = cls.web_app.app.test_client()
        assert cls.client.post('/api/auth/login', json={'password': 'test-pass'}).status_code == 200

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = self.web_app.PRIVACY_VERSION
        shutil.rmtree(self.web_app.LIV_DIR, ignore_errors=True)
        os.makedirs(self.web_app.LIV_DIR, exist_ok=True)

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def make_zip_liv(self, name, with_photo=True, with_video=True):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with zipfile.ZipFile(path, 'w') as archive:
            if with_photo:
                archive.writestr('photo.jpg', self.photo)
            if with_video:
                archive.writestr('video.mp4', FAKE_MP4)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def test_zip_liv_serves_photo_thumb_and_video(self):
        self.make_zip_liv('internal/live1.liv')
        thumb = self.client.get('/thumb/internal/live1.liv')
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.mimetype, 'image/jpeg')
        img = self.client.get('/img/internal/live1.liv')
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.data, self.photo)
        video = self.client.get('/video/internal/live1.liv')
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.data, FAKE_MP4)

    def test_plain_jpeg_liv_serves_photo_without_video(self):
        self.write_local('internal/live2.liv', self.photo)
        img = self.client.get('/img/internal/live2.liv')
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.data, self.photo)
        video = self.client.get('/video/internal/live2.liv')
        self.assertEqual(video.status_code, 404)

    def test_video_only_liv_generates_still(self):
        self.make_zip_liv('internal/live3.liv', with_photo=False)
        original_run = self.web_app.run

        def fake_run(args, _t=30):
            if 'ffmpeg' in args:
                with open(args[-1], 'wb') as out:
                    out.write(jpeg_bytes())
                return subprocess.CompletedProcess(args, 0, '', '')
            return original_run(args, _t)

        self.web_app.run = fake_run
        try:
            thumb = self.client.get('/thumb/internal/live3.liv')
            self.assertEqual(thumb.status_code, 200)
            self.assertEqual(thumb.mimetype, 'image/jpeg')
        finally:
            self.web_app.run = original_run

    def test_delete_removes_liv_cache(self):
        self.make_zip_liv('internal/live4.liv')
        self.client.get('/thumb/internal/live4.liv')
        cache_dir = os.path.join(self.web_app.LIV_DIR, 'internal', 'live4.liv.d')
        self.assertTrue(os.path.isdir(cache_dir))
        response = self.client.delete('/api/file/internal/live4.liv')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.exists(cache_dir))
        self.assertFalse(os.path.exists(os.path.join(self.web_app.DLDIR, 'internal', 'live4.liv')))

    def test_cache_clear_supports_liv_scope(self):
        self.make_zip_liv('internal/live5.liv')
        self.client.get('/thumb/internal/live5.liv')
        cache_dir = os.path.join(self.web_app.LIV_DIR, 'internal', 'live5.liv.d')
        self.assertTrue(os.path.isdir(cache_dir))
        response = self.client.post('/api/cache/clear', json={'scope': 'liv'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(os.path.isdir(cache_dir))

    def test_source_size_change_reextracts(self):
        path = self.make_zip_liv('internal/live6.liv')
        first = self.client.get('/img/internal/live6.liv')
        self.assertEqual(first.data, self.photo)
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('photo.jpg', jpeg_bytes(color=(30, 120, 220)))
            archive.writestr('video.mp4', FAKE_MP4 + b'x')
        second = self.client.get('/img/internal/live6.liv')
        self.assertEqual(second.status_code, 200)
        self.assertNotEqual(second.data, self.photo)

    def test_insp_streams_through_img(self):
        pano = jpeg_bytes(64, 32)
        self.write_local('internal/pano.insp', pano)
        img = self.client.get('/img/internal/pano.insp')
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.data, pano)

    def test_liv_prefix_jpg_maps_to_liv_kind(self):
        from luna_client import file_kind
        self.assertEqual(file_kind('LIV_20260919_151905_564.jpg'), 'LIV')
        self.assertEqual(file_kind('IMG_20260919_151828_562.jpg'), 'JPG')

    def test_motion_jpeg_live_still_splits_still_and_clip(self):
        FAKE_BOX = b'\x00\x00\x00\x20ftypmp42\x00\x00\x00\x00mp42isom' + b'\x01' * 48
        still = jpeg_bytes(32, 32)
        self.write_local('external/LIV_20260919_151905_564.jpg', still + FAKE_BOX)
        img = self.client.get('/img/external/LIV_20260919_151905_564.jpg')
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.data, still)
        video = self.client.get('/video/external/LIV_20260919_151905_564.jpg')
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.data, FAKE_BOX)
        thumb = self.client.get('/thumb/external/LIV_20260919_151905_564.jpg')
        self.assertEqual(thumb.status_code, 200)
        self.assertEqual(thumb.mimetype, 'image/jpeg')

    def test_plain_jpg_is_not_treated_as_live(self):
        still = jpeg_bytes(32, 32)
        self.write_local('external/IMG_20260919_151828_562.jpg', still)
        # /video passes non-live files through untouched; nothing is split off
        video = self.client.get('/video/external/IMG_20260919_151828_562.jpg')
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.data, still)
        img = self.client.get('/img/external/IMG_20260919_151828_562.jpg')
        self.assertEqual(img.status_code, 200)
        self.assertEqual(img.data, still)


    def test_camera_table_has_sorting_ui(self):
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        body = home.get_data(as_text=True)
        self.assertIn("SORT={key:'date',dir:'desc'}", body)
        self.assertIn('bindSort', body)
        self.assertIn('sort-asc', body)


if __name__ == '__main__':
    unittest.main()
