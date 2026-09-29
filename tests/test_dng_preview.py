import importlib
import importlib.util
import io
import json
import os
import struct
import sys
import tempfile
import unittest

from PIL import Image, ImageStat

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


def make_bayer_dng(path, width=64, height=64):
    """Minimal uncompressed 16-bit RGGB DNG with a synthetic scene:
    red block / white block / gray ramp on a dark background."""
    px = [[0] * width for _ in range(height)]
    for y in range(height):
        for x in range(width):
            if x < width // 4:
                v = (600 << 4)          # red-ish, dim
            elif x < width // 2 and y < height // 2:
                v = (200 << 6)          # bright white area
            else:
                v = int((x / width) * (160 << 6))  # ramp
            px[y][x] = min(65535, v)
    # Bayer RGGB: R G / G B — sample a channel-true plane
    raw = bytearray()
    for y in range(height):
        for x in range(width):
            raw += struct.pack('<H', px[y][x])
    tags = {
        256: struct.pack('<I', width), 257: struct.pack('<I', height),
        258: struct.pack('<H', 16), 259: struct.pack('<H', 1),
        273: struct.pack('<I', 8 + 12 * 6 + 4),
        279: struct.pack('<I', len(raw)),
    }
    ifd = struct.pack('<H', len(tags))
    for tag in sorted(tags):
        typ, cnt = (4, 1)
        val = tags[tag]
        if len(val) == 2:
            typ = 3
            ifd += struct.pack('<HHI', tag, typ, 1) + val + b'\x00\x00'
        else:
            ifd += struct.pack('<HHI', tag, typ, 1) + val
    ifd += struct.pack('<I', 0)
    header = b'II*\x00' + struct.pack('<I', 8)
    strip_at = 8 + 12 * len(tags) + 4 + 2  # header + entries + next-ifd + pad
    ifd = ifd.replace(struct.pack('<I', 8 + 12 * len(tags) + 4),
                      struct.pack('<I', strip_at))
    with open(path, 'wb') as f:
        f.write(header + ifd + b'\x00\x00' + bytes(raw))
    return strip_at, len(raw)


class DngPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('flask') is None:
            raise unittest.SkipTest('Flask is not installed in this Python environment')
        cls.tempdir = tempfile.TemporaryDirectory()
        root = cls.tempdir.name
        config = {'camera_host': '192.0.2.1', 'camera_ssid': '', 'camera_password': '',
                  'wifi_backend': 'none', 'wifi_iface': None,
                  'download_dir': os.path.join(root, 'downloads'),
                  'state_dir': os.path.join(root, 'state'), 'web_port': 18777}
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

    def test_decode_bayer_produces_visible_image(self):
        dng = os.path.join(self.tempdir.name, 'sample.dng')
        make_bayer_dng(dng)
        out = self.web_app._decode_bayer_dng(dng, 128)
        self.assertIsNotNone(out, 'decoder must handle uncompressed 16-bit bayer')
        self.assertEqual(out.width, 128)
        st = ImageStat.Stat(out.convert('RGB'))
        # the decode must NOT be crushed to black: means well above the old
        # ffmpeg path (which landed ~10/255 on this class of files)
        self.assertGreater(min(st.mean), 40, 'image must not be near-black: %s' % (st.mean,))

    def test_decode_rejects_unsupported_layout(self):
        p = os.path.join(self.tempdir.name, 'bad.dng')
        with open(p, 'wb') as f:
            f.write(b'MM\x00*\x00\x00\x00\x08')  # big-endian — unsupported
        self.assertIsNone(self.web_app._decode_bayer_dng(p, 64))


if __name__ == '__main__':
    unittest.main()
