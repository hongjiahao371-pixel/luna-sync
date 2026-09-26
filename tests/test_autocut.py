import importlib
import importlib.util
import json
import os
import tempfile
import sys
import unittest


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class HighlightPickerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('flask') is None:
            raise unittest.SkipTest('Flask is not installed in this Python environment')
        root = tempfile.mkdtemp()
        config = {'camera_host': '192.0.2.1', 'camera_ssid': '', 'camera_password': '',
                  'wifi_backend': 'none', 'wifi_iface': None,
                  'download_dir': os.path.join(root, 'downloads'),
                  'state_dir': os.path.join(root, 'state'), 'web_port': 18775}
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
        cls.mod = importlib.import_module('web_app')

    def test_picks_highest_window(self):
        scores = [0] * 4 + [30, 32, 28, 31, 29] + [5, 5]
        cuts = []
        segs = self.mod._pick_highlights(scores, cuts)
        self.assertTrue(segs)
        best = max(segs, key=lambda s: s['start'])
        # the action burst is at seconds 4-9
        self.assertGreaterEqual(segs[0]['start'], 3)

    def test_static_segments_filtered_by_relative_floor(self):
        scores = [0, 0, 0, 0, 30, 32, 28, 31, 29, 5, 5, 5]
        cuts = []
        segs = self.mod._pick_highlights(scores, cuts)
        for seg in segs:
            self.assertGreaterEqual(seg['start'], 3, 'static lead-in must not be picked')

    def test_scene_cut_inside_window_skipped(self):
        # cut at second 5: windows spanning it are invalid
        scores = [10, 10, 40, 0, 40, 10, 10]
        cuts = [4]
        segs = self.mod._pick_highlights(scores, cuts, window=4, max_segments=3)
        for seg in segs:
            self.assertFalse(seg['start'] < 4 < seg['start'] + seg['dur'],
                             'segment must not span a scene cut')

    def test_short_video_whole_clip(self):
        segs = self.mod._pick_highlights([10, 12, 11], [], window=4)
        self.assertEqual(segs[0]['start'], 0)
        self.assertGreaterEqual(segs[0]['dur'], 2)

    def test_find_blurry_relative(self):
        entries = [{'path': 'a', 'sharp': 40, 'score': 80},
                   {'path': 'b', 'sharp': 6, 'score': 55},
                   {'path': 'c', 'sharp': 2, 'score': 30}]
        flagged = self.mod._find_blurry(entries)
        self.assertIn('c', flagged)
        self.assertNotIn('a', flagged)


if __name__ == '__main__':
    unittest.main()
