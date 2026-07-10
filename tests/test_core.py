import os
import sys
import tempfile
import unittest
from threading import Event

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

import downloader
import luna_client


class Response:
    status = 200
    headers = {'Content-Length': '6'}

    def __init__(self):
        self.sent = False

    def read(self, _):
        if self.sent:
            return b''
        self.sent = True
        return b'abcdef'

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class DownloaderTests(unittest.TestCase):
    def test_cancel_keeps_partial_file_for_resume(self):
        original = downloader.urlopen
        downloader.urlopen = lambda *args, **kwargs: Response()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                target = os.path.join(tmp, 'clip.mp4')
                cancel = Event()
                cancel.set()
                with self.assertRaisesRegex(Exception, 'cancelled'):
                    downloader.download_file('http://camera/clip.mp4', target, cancel=cancel)
                self.assertTrue(os.path.exists(target + '.part'))
                self.assertFalse(os.path.exists(target))
        finally:
            downloader.urlopen = original


class SizeCacheTests(unittest.TestCase):
    def test_second_scan_reuses_exact_sizes(self):
        original_open = luna_client.urlopen
        original_parse = luna_client.parse_luna_index
        original_probe = luna_client.probe_size
        luna_client._size_cache.clear()
        items = [
            {'url': 'http://camera/A.mp4', 'bytes': None},
            {'url': 'http://camera/B.mp4', 'bytes': None},
        ]

        class IndexResponse:
            def read(self):
                return b'<html></html>'

            def close(self):
                pass

        calls = []
        luna_client.urlopen = lambda *args, **kwargs: IndexResponse()
        luna_client.parse_luna_index = lambda *args, **kwargs: [dict(item) for item in items]
        luna_client.probe_size = lambda url: calls.append(url) or 123
        try:
            client = luna_client.LunaClient()
            self.assertEqual(len(client.list_files(include_external=False)), 2)
            self.assertEqual(len(client.list_files(include_external=False)), 2)
            self.assertEqual(calls, ['http://camera/A.mp4', 'http://camera/B.mp4'])
        finally:
            luna_client.urlopen = original_open
            luna_client.parse_luna_index = original_parse
            luna_client.probe_size = original_probe
            luna_client._size_cache.clear()


class LrvTests(unittest.TestCase):
    def test_lrv_sidecar_files_are_classified_as_lrv(self):
        self.assertEqual(luna_client.file_kind('LRV_20260710_101942_062.lrv.3ainfo.bin'), 'LRV')
        self.assertEqual(luna_client.file_kind('VID_20260710_101942_062.mp4.3ainfo.bin'), 'BIN')


if __name__ == '__main__':
    unittest.main()
