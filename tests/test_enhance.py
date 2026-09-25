import importlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw, ImageFilter, ImageStat


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))



class EnhanceWatermarkBlurryTests(unittest.TestCase):
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
            'state_dir': os.path.join(root, 'state'), 'web_port': 18774,
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

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def flat_image(self, path):
        Image.new('RGB', (400, 300), (128, 128, 128)).save(path, 'JPEG')

    def test_enhance_increases_contrast(self):
        src = os.path.join(self.tempdir.name, 'flat.jpg')
        self.flat_image(src)
        dst = os.path.join(self.tempdir.name, 'enhanced.jpg')
        img = Image.open(src).convert('RGB')
        enhanced = self.web_app._enhance(img)
        before = ImageStat.Stat(img.convert('L')).stddev[0]
        after = ImageStat.Stat(enhanced.convert('L')).stddev[0]
        self.assertGreater(after, before, 'auto levels must widen contrast')
        enhanced.save(dst, 'JPEG')
        self.assertTrue(os.path.getsize(dst) > 0)

    def test_watermark_changes_pixels(self):
        src = os.path.join(self.tempdir.name, 'w.jpg')
        self.flat_image(src)
        img = Image.open(src).convert('RGB')
        marked = self.web_app._watermark(img.copy(), 'Luna Sync')
        self.assertEqual(img.size, marked.size)
        self.assertNotEqual(list(img.getdata()), list(marked.getdata()),
                            'watermark must alter pixels')

    def test_process_for_export_copies_without_flags(self):
        src = os.path.join(self.tempdir.name, 'a.jpg')
        dst = os.path.join(self.tempdir.name, 'out.jpg')
        self.flat_image(src)
        mode = self.web_app._process_for_export(src, dst, False, '')
        self.assertEqual(mode, 'copied')

    def test_process_for_export_applies_flags(self):
        src = os.path.join(self.tempdir.name, 'b.jpg')
        dst = os.path.join(self.tempdir.name, 'out2.jpg')
        self.flat_image(src)
        mode = self.web_app._process_for_export(src, dst, True, 'Luna Sync')
        self.assertEqual(mode, 'processed')
        processed = Image.open(dst)
        self.assertEqual(processed.size, (400, 300))

    def test_blurry_detection_thresholds(self):
        sharp = os.path.join(self.tempdir.name, 'sharp.jpg')
        blurry = os.path.join(self.tempdir.name, 'blurry.jpg')
        img = Image.new('RGB', (400, 300), (128, 128, 128))
        d = ImageDraw.Draw(img)
        for x in range(0, 400, 6):
            d.line([(x, 0), (x, 300)], fill=255)
        img.save(sharp, 'JPEG')
        img.filter(ImageFilter.GaussianBlur(10)).save(blurry, 'JPEG')
        s_sharp = self.web_app._analyze_image(sharp)
        s_blur = self.web_app._analyze_image(blurry)
        self.assertGreater(s_sharp['score'], s_blur['score'])
        self.assertLess(s_blur['score'], 40, 'heavy blur must fall below the blurry threshold')
        self.assertGreaterEqual(s_sharp['score'], 40)


if __name__ == '__main__':
    unittest.main()
