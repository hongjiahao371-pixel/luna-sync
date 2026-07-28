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
        original_list_paths = luna_client.LunaClient._list_file_paths
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
        luna_client.LunaClient._list_file_paths = lambda *_: (_ for _ in ()).throw(ConnectionError('tcp unavailable'))
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
            luna_client.LunaClient._list_file_paths = original_list_paths
            luna_client._size_cache.clear()


class LunaTcpProtocolTests(unittest.TestCase):
    def test_ucd2_command_matches_known_camera_packet(self):
        body = (
            luna_client.wire_field_varint(1, 48) +
            luna_client.wire_field_varint(1, 15) +
            luna_client.wire_field_varint(1, 11)
        )
        packet = luna_client.build_ucd2_command(0x10, 8, 1, body)
        self.assertEqual(packet, luna_client.AUTH_PAYLOADS[1])

    def test_file_list_body_includes_storage_location(self):
        self.assertEqual(
            luna_client.file_list_body(2, 50),
            bytes.fromhex('0802103218322002'),
        )

    def test_file_list_response_parses_paths_and_total(self):
        paths = [
            '/storage_internal/DCIM/Camera01/A.jpg',
            '/storage_internal/DCIM/Camera01/B.mp4',
        ]
        body = b''.join(
            b'\x0a' + luna_client.wire_varint(len(path)) + path.encode()
            for path in paths
        )
        body += b'\x10' + luna_client.wire_varint(123)
        self.assertEqual(luna_client.parse_file_list_body(body), (paths, 123))

    def test_tcp_paths_preserve_storage_and_capture_time(self):
        items = luna_client.parse_luna_paths(
            ['/DCIM/Camera01/IMG_20260714_180240_018.jpg'],
            '192.168.42.1',
            'external',
            '存储卡',
        )
        self.assertEqual(items[0]['id'], 'external/IMG_20260714_180240_018.jpg')
        self.assertEqual(items[0]['url'], 'http://192.168.42.1/DCIM/Camera01/IMG_20260714_180240_018.jpg')
        self.assertEqual(items[0]['date'], '14-Jul-2026')
        self.assertEqual(items[0]['time'], '18:02')

    def test_file_list_reconnects_after_control_session_failure(self):
        original_session = luna_client.LunaAuthSession

        class Session:
            created = 0

            def __init__(self, *_):
                self.index = Session.created
                Session.created += 1

            def refresh(self):
                pass

            def list_file_paths(self, _):
                if self.index == 0:
                    raise ConnectionError('closed')
                return ['/storage_internal/DCIM/Camera01/A.jpg']

            def close(self):
                pass

        luna_client.LunaAuthSession = Session
        try:
            client = luna_client.LunaClient()
            self.assertEqual(
                client._list_file_paths(2),
                ['/storage_internal/DCIM/Camera01/A.jpg'],
            )
            self.assertEqual(Session.created, 2)
        finally:
            luna_client.LunaAuthSession = original_session


class LrvTests(unittest.TestCase):
    def test_lrv_sidecar_files_are_classified_as_lrv(self):
        self.assertEqual(luna_client.file_kind('LRV_20260710_101942_062.lrv.3ainfo.bin'), 'LRV')
        self.assertEqual(luna_client.file_kind('VID_20260710_101942_062.mp4.3ainfo.bin'), 'BIN')


if __name__ == '__main__':
    unittest.main()
