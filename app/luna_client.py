import datetime, re, socket, struct, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_HOST = "192.168.42.1"
DEFAULT_CAMERA_URL = "http://" + DEFAULT_HOST + "/storage_internal/DCIM/Camera01/"
STORAGE_ROOTS = [
    {'id': 'internal', 'label': '内置存储', 'path': '/storage_internal/DCIM/Camera01/', 'location': 2},
    {'id': 'external', 'label': '存储卡', 'path': '/DCIM/Camera01/', 'location': 3},
]
AUTH_PAYLOADS = [
    bytes([0x55,0x43,0x44,0x32,0x01,0x0C,0x05,0x0F,0x00,0x00,0x00,0x00,0x37,0x05,0x47,0x7C]),
    bytes([0x55,0x43,0x44,0x32,0x01,0x0C,0x04,0x10,0x0F,0x00,0x00,0x00,0x08,0x00,0x02,0x01,0x00,0x00,0x80,0x00,0x00,0x08,0x30,0x08,0x0F,0x08,0x0B,0x7C,0x00,0x8E,0x7C]),
]
INDEX_RE = re.compile(r'<a href="([^"]+)">([^<]+)</a>\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}:\d{2})\s+(\S+)')
SIZE_CACHE_TTL = 600
FILE_LIST_PAGE_SIZE = 50
FILE_LIST_MAX_OFFSET = 5000
UCD2_MAGIC = b'UCD2'
UCD2_FILE = 4
UCD2_STREAM = 5
UCD2_CHECKSUM_POLY = 0x04C11DB7
_size_cache = {}
_size_cache_lk = threading.Lock()

def parse_size(text):
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([KMG])?', text.strip())
    if not m:
        return None
    mult = {'K': 1024, 'M': 1024**2, 'G': 1024**3}.get(m.group(2), 1)
    return int(float(m.group(1)) * mult)

def is_lrv_name(name):
    lower = str(name or '').lower()
    return lower.startswith('lrv_') or lower.endswith('.lrv') or '.lrv.' in lower

def file_kind(name):
    if is_lrv_name(name):
        return 'LRV'
    s = name.rsplit('.', 1)[-1].upper() if '.' in name else ''
    if s in ('MP4', 'LRV', 'MOV', 'JPG', 'JPEG', 'PNG', 'WEBP', 'GIF', 'LIV', 'INSP'):
        return s
    return s or 'FILE'

def wire_varint(value):
    out = bytearray()
    value = int(value)
    while value > 0x7f:
        out.append((value & 0x7f) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)

def wire_field_varint(field, value):
    return wire_varint(field << 3) + wire_varint(value)

def read_wire_varint(data, offset):
    value = 0
    shift = 0
    while offset < len(data) and shift <= 63:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7f) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError('invalid protobuf varint')

def parse_file_list_body(data):
    paths = []
    total = None
    offset = 0
    while offset < len(data):
        key, offset = read_wire_varint(data, offset)
        field = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            value, offset = read_wire_varint(data, offset)
            if field == 2:
                total = value
        elif wire_type == 2:
            length, offset = read_wire_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError('truncated protobuf field')
            if field == 1:
                path = data[offset:end].decode('utf-8', 'replace')
                if path.startswith('/'):
                    paths.append(path)
            offset = end
        elif wire_type == 1:
            offset += 8
        elif wire_type == 5:
            offset += 4
        else:
            raise ValueError('unsupported protobuf wire type')
        if offset > len(data):
            raise ValueError('truncated protobuf message')
    return paths, total

def file_list_body(location, offset=0, limit=FILE_LIST_PAGE_SIZE):
    parts = [wire_field_varint(1, 2)]
    if offset:
        parts.append(wire_field_varint(2, offset))
    parts.extend([
        wire_field_varint(3, limit),
        wire_field_varint(4, location),
    ])
    return b''.join(parts)

def _checksum_table():
    table = []
    for index in range(256):
        value = index << 24
        for _ in range(8):
            value = ((value << 1) ^ UCD2_CHECKSUM_POLY) if value & 0x80000000 else value << 1
            value &= 0xffffffff
        table.append(value)
    return table

_UCD2_CHECKSUM_TABLE = _checksum_table()

def ucd2_checksum(data):
    checksum = 0xffffffff
    for byte in data:
        checksum = (checksum ^ byte) & 0xffffffff
        for _ in range(4):
            checksum = ((checksum << 8) ^ _UCD2_CHECKSUM_TABLE[(checksum >> 24) & 0xff]) & 0xffffffff
    return checksum

def build_ucd2_command(sequence, code, request_id, body=b''):
    raw = struct.pack('<HBHI', code, 2, request_id, 0x8000) + body
    frame = UCD2_MAGIC + bytes([1, 0x0c, UCD2_FILE, sequence & 0xff]) + struct.pack('<I', len(raw)) + raw
    return frame + struct.pack('<I', ucd2_checksum(frame))

def build_stream_hello(sequence):
    return UCD2_MAGIC + bytes([1, 0x0c, UCD2_STREAM, sequence & 0xff]) + b'\0\0\0\0\xf6\xcc\x4f\x09'

def _timestamp_from_name(name):
    match = re.search(r'_(\d{8})_(\d{6})(?:_|\.)', name)
    if not match:
        return None
    try:
        return datetime.datetime.strptime(''.join(match.groups()), '%Y%m%d%H%M%S')
    except ValueError:
        return None

def parse_luna_paths(paths, host, storage='', storage_label=''):
    out = []
    for camera_path in dict.fromkeys(paths):
        name = unescape(camera_path.rsplit('/', 1)[-1])
        if not name:
            continue
        captured = _timestamp_from_name(name)
        out.append({
            'id': (storage + '/' + name) if storage else name,
            'name': name, 'href': camera_path,
            'url': 'http://' + host + camera_path,
            'date': captured.strftime('%d-%b-%Y') if captured else '',
            'time': captured.strftime('%H:%M') if captured else '',
            'size_text': '', 'bytes': None,
            'kind': file_kind(name), 'storage': storage, 'storage_label': storage_label,
        })
    return out

def parse_luna_index(html, base=DEFAULT_CAMERA_URL, storage='', storage_label=''):
    out = []
    for m in INDEX_RE.finditer(html):
        name = unescape(m.group(2))
        href = unescape(m.group(1))
        if name == '../' or href == '../':
            continue
        if name.endswith('/') or href.endswith('/'):
            continue
        out.append({
            'id': (storage + '/' + name) if storage else name,
            'name': name, 'href': href, 'url': urljoin(base, href),
            'date': m.group(3), 'time': m.group(4),
            'size_text': m.group(5), 'bytes': parse_size(m.group(5)),
            'kind': file_kind(name), 'storage': storage, 'storage_label': storage_label,
        })
    return out

def probe_size(url, timeout=4):
    try:
        req = Request(url, headers={'User-Agent': 'LunaDL/0.1', 'Range': 'bytes=0-0'})
        resp = urlopen(req, timeout=timeout)
        try:
            cr = resp.headers.get('Content-Range')
            if cr:
                m = re.search(r'/(\d+)', cr)
                if m:
                    return int(m.group(1))
            cl = resp.headers.get('Content-Length')
            if cl:
                return int(cl)
        finally:
            resp.close()
    except Exception:
        return None
    return None

def cached_size(url):
    with _size_cache_lk:
        entry = _size_cache.get(url)
        if entry and time.monotonic() - entry[0] < SIZE_CACHE_TTL:
            return entry[1]
    return None

def remember_size(url, size):
    if size is None:
        return
    with _size_cache_lk:
        _size_cache[url] = (time.monotonic(), size)

class LunaAuthSession:
    def __init__(self, host=DEFAULT_HOST, port=6666, timeout=3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock = None
        self._buffer = b''
        self._sequence = 0x2c
        self._request_id = 8
        self._lk = threading.RLock()

    def open(self):
        with self._lk:
            if self._sock is not None:
                return
            last = None
            for delay in (0, 0.8, 3):
                if delay:
                    time.sleep(delay)
                sock = None
                try:
                    sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                    sock.settimeout(self.timeout)
                    self._send_auth(sock)
                    self._sock = sock
                    self._buffer = b''
                    self._sequence = 0x2c
                    self._request_id = 8
                    return
                except OSError as exc:
                    last = exc
                    if sock:
                        sock.close()
            if last:
                raise last
            raise ConnectionError('no session')

    def refresh(self):
        with self._lk:
            if self._sock is None:
                self.open()
                return
            try:
                self._sock.sendall(build_stream_hello(self._sequence))
                self._sequence = (self._sequence + 1) & 0xff
            except OSError:
                self.close()
                self.open()

    def close(self):
        with self._lk:
            if self._sock is not None:
                self._sock.close()
                self._sock = None
            self._buffer = b''

    def _send_auth(self, sock):
        for payload in AUTH_PAYLOADS:
            sock.sendall(payload)
            time.sleep(0.03)
        sock.settimeout(0.05)
        try:
            while True:
                if not sock.recv(65536):
                    break
        except Exception:
            pass

    def _receive_frame(self, timeout):
        deadline = time.monotonic() + timeout
        while True:
            start = self._buffer.find(UCD2_MAGIC)
            if start >= 0:
                if start:
                    self._buffer = self._buffer[start:]
                if len(self._buffer) >= 8:
                    frame_type = self._buffer[6]
                    if frame_type == UCD2_STREAM:
                        frame_length = 16
                    elif frame_type == UCD2_FILE and len(self._buffer) >= 12:
                        frame_length = 12 + struct.unpack_from('<I', self._buffer, 8)[0] + 4
                    else:
                        self._buffer = self._buffer[8:]
                        continue
                    if frame_length and len(self._buffer) >= frame_length:
                        frame = self._buffer[:frame_length]
                        self._buffer = self._buffer[frame_length:]
                        return frame
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('camera response timeout')
            self._sock.settimeout(remaining)
            chunk = self._sock.recv(65536)
            if not chunk:
                raise ConnectionError('camera control connection closed')
            self._buffer += chunk

    def command(self, code, body=b'', timeout=8):
        with self._lk:
            if self._sock is None:
                self.open()
            sequence = self._sequence
            request_id = self._request_id
            self._sequence = (self._sequence + 1) & 0xff
            self._request_id += 1
            self._sock.sendall(build_ucd2_command(sequence, code, request_id, body))
            deadline = time.monotonic() + timeout
            while True:
                frame = self._receive_frame(max(0.1, deadline - time.monotonic()))
                if frame[6] != UCD2_FILE:
                    continue
                raw_length = struct.unpack_from('<I', frame, 8)[0]
                raw = frame[12:12 + raw_length]
                if len(raw) < 9:
                    continue
                status, _, response_id, _ = struct.unpack_from('<HBHI', raw, 0)
                if response_id == request_id:
                    return status, raw[9:]
                if time.monotonic() >= deadline:
                    raise TimeoutError('camera command response timeout')

    def keepalive(self):
        with self._lk:
            self.refresh()
            status, _ = self.command(15, timeout=3)
            if status != 200:
                raise RuntimeError('camera keepalive returned status %s' % status)
            status, _ = self.command(
                8,
                wire_field_varint(1, 48) + wire_field_varint(1, 15) + wire_field_varint(1, 11),
                timeout=3,
            )
            if status != 200:
                raise RuntimeError('camera options returned status %s' % status)

    def list_file_paths(self, location):
        paths = []
        for offset in range(0, FILE_LIST_MAX_OFFSET + 1, FILE_LIST_PAGE_SIZE):
            status, data = self.command(13, file_list_body(location, offset))
            if status != 200:
                raise RuntimeError('camera file list returned status %s' % status)
            page, _ = parse_file_list_body(data)
            paths.extend(page)
            if len(page) < FILE_LIST_PAGE_SIZE:
                break
            time.sleep(0.02)
        return list(dict.fromkeys(paths))

class LunaClient:
    def __init__(self, host=DEFAULT_HOST):
        self.host = host
        self.roots = [
            dict(root, url="http://" + host + root['path'])
            for root in STORAGE_ROOTS
        ]
        self.url = self.roots[0]['url']
        self.auth = None
        self._lk = threading.RLock()

    def connect(self):
        with self._lk:
            if self.auth is None:
                self.auth = LunaAuthSession(self.host)
            self.auth.refresh()

    def close(self):
        with self._lk:
            if self.auth:
                self.auth.close()
                self.auth = None

    def keepalive(self):
        with self._lk:
            if self.auth is None:
                self.auth = LunaAuthSession(self.host)
            try:
                self.auth.keepalive()
            except Exception:
                self.auth.close()
                self.auth = LunaAuthSession(self.host)
                try:
                    self.auth.keepalive()
                except Exception:
                    self.auth.close()
                    self.auth = None
                    raise

    def _list_file_paths(self, location):
        with self._lk:
            last_error = None
            for _ in range(2):
                if self.auth is None:
                    self.auth = LunaAuthSession(self.host)
                try:
                    self.auth.refresh()
                    return self.auth.list_file_paths(location)
                except Exception as exc:
                    last_error = exc
                    self.auth.close()
                    self.auth = None
            raise last_error

    def list_files(self, include_external=True):
        out = []
        last_error = None
        any_success = False
        roots = self.roots if include_external else self.roots[:1]
        for root in roots:
            try:
                paths = self._list_file_paths(root['location'])
                items = parse_luna_paths(paths, self.host, root['id'], root['label'])
                any_success = True
            except Exception as exc:
                last_error = exc
                try:
                    req = Request(root['url'], headers={'User-Agent': 'LunaDL/0.1'})
                    html = urlopen(req, timeout=8).read().decode('utf-8', 'replace')
                    items = parse_luna_index(html, root['url'], root['id'], root['label'])
                    any_success = True
                except Exception as http_exc:
                    last_error = http_exc
                    continue
            missing = []
            for item in items:
                exact = cached_size(item['url'])
                if exact is None:
                    missing.append(item)
                else:
                    item['bytes'] = exact
                    item['bytes_exact'] = True
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(probe_size, item['url']): item for item in missing}
                for future in as_completed(futures):
                    exact = future.result()
                    if exact is not None:
                        item = futures[future]
                        remember_size(item['url'], exact)
                        item['bytes'] = exact
                        item['bytes_exact'] = True
            out.extend(items)
        if not any_success and last_error:
            raise last_error
        return out
