# -*- coding: utf-8 -*-
"""视频拼接台测试：无损/标准化/掐头去尾/照片拒绝/LRV 排除"""
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

from PIL import Image

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

HAS_FFMPEG = bool(shutil.which('ffmpeg'))


def mp4_bytes(seconds=1, size='320x240', rate=24, with_audio=False):
    if not HAS_FFMPEG:
        return None
    out = tempfile.mktemp(suffix='.mp4')
    cmd = ['ffmpeg', '-v', 'error', '-y',
           '-f', 'lavfi', '-i', 'testsrc2=duration=%d:size=%s:rate=%d' % (seconds, size, rate)]
    if with_audio:
        cmd += ['-f', 'lavfi', '-i', 'sine=frequency=440:duration=%d' % seconds]
    cmd += ['-pix_fmt', 'yuv420p', '-movflags', '+faststart']
    if with_audio:
        cmd += ['-c:a', 'aac', '-shortest']
    cmd += [out]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0 or not os.path.exists(out):
        raise RuntimeError('ffmpeg fixture failed')
    with open(out, 'rb') as f:
        data = f.read()
    os.remove(out)
    return data


def jpeg_bytes(width=64, height=48, color=(150, 90, 60)):
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), color).save(buffer, 'JPEG')
    return buffer.getvalue()


class ConcatTests(unittest.TestCase):
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
            'web_port': 18777,
        }
        config_path = os.path.join(root, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(config, f)
        os.environ['LUNA_CONFIG'] = config_path
        os.environ['DOWNLOAD_DIR'] = config['download_dir']
        os.environ['STATE_DIR'] = config['state_dir']
        os.environ['LUNA_WIFI_BACKEND'] = 'none'
        os.environ['LUNA_TLS'] = 'off'
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
        self.web_app.CCTASK.update({'running': False, 'phase': '', 'done': 0,
                                    'total': 0, 'error': '', 'output': ''})

    def write_local(self, name, data):
        path = os.path.join(self.web_app.DLDIR, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'wb') as f:
            f.write(data)
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return path

    def probe_duration(self, path):
        r = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                            '-of', 'json', path], capture_output=True, timeout=60)
        return float(json.loads(r.stdout)['format']['duration'])

    def probe_size(self, path):
        r = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                            '-show_entries', 'stream=width,height',
                            '-of', 'json', path], capture_output=True, timeout=60)
        st = json.loads(r.stdout)['streams'][0]
        return int(st['width']), int(st['height'])

    # ---- 无损拼接：同规格两段，时长≈之和
    @unittest.skipUnless(HAS_FFMPEG, '需要 ffmpeg')
    def test_concat_lossless(self):
        self.write_local('Luna/2026-10-23/A.MP4', mp4_bytes(seconds=2, with_audio=True))
        self.write_local('Luna/2026-10-23/B.MP4', mp4_bytes(seconds=3, with_audio=True))
        resp = self.client.post('/api/concat/start',
                                json={'videos': ['Luna/2026-10-23/A.MP4', 'Luna/2026-10-23/B.MP4']})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])
        for _ in range(120):
            st = self.client.get('/api/concat/status').get_json()
            if not st['running']:
                break
            import time
            time.sleep(0.5)
        self.assertEqual(st['error'], '', st)
        out = os.path.join(self.web_app.DLDIR, st['output'])
        self.assertTrue(os.path.isfile(out))
        self.assertGreaterEqual(self.probe_duration(out), 4.5)
        self.assertTrue(st['output'].startswith('拼接/'))

    # ---- 混规格：以第一段画幅为基准标准化
    @unittest.skipUnless(HAS_FFMPEG, '需要 ffmpeg')
    def test_concat_normalized(self):
        self.write_local('Luna/2026-10-23/S.MP4', mp4_bytes(seconds=1, size='320x240'))
        self.write_local('Luna/2026-10-23/L.MP4', mp4_bytes(seconds=1, size='640x360'))
        resp = self.client.post('/api/concat/start',
                                json={'videos': ['Luna/2026-10-23/S.MP4', 'Luna/2026-10-23/L.MP4']})
        self.assertEqual(resp.status_code, 200)
        for _ in range(120):
            st = self.client.get('/api/concat/status').get_json()
            if not st['running']:
                break
            import time
            time.sleep(0.5)
        self.assertEqual(st['error'], '', st)
        out = os.path.join(self.web_app.DLDIR, st['output'])
        self.assertEqual(self.probe_size(out), (320, 240))

    # ---- 掐头去尾：各留 1s，成片时长≈2s（-to 必须在输入侧）
    @unittest.skipUnless(HAS_FFMPEG, '需要 ffmpeg')
    def test_concat_trims(self):
        self.write_local('Luna/2026-10-23/T1.MP4', mp4_bytes(seconds=3))
        self.write_local('Luna/2026-10-23/T2.MP4', mp4_bytes(seconds=3))
        resp = self.client.post('/api/concat/start', json={
            'videos': ['Luna/2026-10-23/T1.MP4', 'Luna/2026-10-23/T2.MP4'],
            'trims': [{'path': 'Luna/2026-10-23/T1.MP4', 'start': 1, 'end': 2},
                      {'path': 'Luna/2026-10-23/T2.MP4', 'start': 0, 'end': 1}]})
        self.assertEqual(resp.status_code, 200)
        for _ in range(120):
            st = self.client.get('/api/concat/status').get_json()
            if not st['running']:
                break
            import time
            time.sleep(0.5)
        self.assertEqual(st['error'], '', st)
        out = os.path.join(self.web_app.DLDIR, st['output'])
        self.assertLessEqual(self.probe_duration(out), 2.8)
        self.assertGreaterEqual(self.probe_duration(out), 1.4)

    # ---- 非视频路径被过滤（照片/LRV 不可拼接）
    def test_concat_rejects_non_video(self):
        self.write_local('Luna/2026-10-23/P.JPG', jpeg_bytes())
        resp = self.client.post('/api/concat/start', json={'videos': ['Luna/2026-10-23/P.JPG']})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()['ok'])   # 受理，worker 过滤后报 no_valid_videos
        for _ in range(30):
            st = self.client.get('/api/concat/status').get_json()
            if not st['running']:
                break
            import time
            time.sleep(0.3)
        self.assertEqual(st['status'] if 'status' in st else '', '')
        self.assertTrue(st['error'])

    def test_concat_empty_rejected(self):
        resp = self.client.post('/api/concat/start', json={'videos': []})
        self.assertEqual(resp.status_code, 400)

    def test_probe_endpoint(self):
        if HAS_FFMPEG:
            self.write_local('Luna/2026-10-23/V.MP4', mp4_bytes(seconds=1))
        else:
            self.skipTest('ffmpeg 不可用')
        resp = self.client.post('/api/concat/probe',
                                json={'paths': ['Luna/2026-10-23/V.MP4']})
        items = resp.get_json()['items']
        self.assertEqual(len(items), 1)
        self.assertGreater(items[0]['duration'], 0.5)


if __name__ == '__main__':
    unittest.main()
