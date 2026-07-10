import re, socket, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_HOST = "192.168.42.1"
DEFAULT_CAMERA_URL = "http://" + DEFAULT_HOST + "/storage_internal/DCIM/Camera01/"
STORAGE_ROOTS = [
    {'id': 'internal', 'label': '内置存储', 'path': '/storage_internal/DCIM/Camera01/'},
    {'id': 'external', 'label': '存储卡', 'path': '/storage_external/DCIM/Camera01/'},
]
AUTH_PAYLOADS = [
    bytes([0x55,0x43,0x44,0x32,0x01,0x0C,0x05,0x0F,0x00,0x00,0x00,0x00,0x37,0x05,0x47,0x7C]),
    bytes([0x55,0x43,0x44,0x32,0x01,0x0C,0x04,0x10,0x0F,0x00,0x00,0x00,0x08,0x00,0x02,0x01,0x00,0x00,0x80,0x00,0x00,0x08,0x30,0x08,0x0F,0x08,0x0B,0x7C,0x00,0x8E,0x7C]),
]
INDEX_RE = re.compile(r'<a href="([^"]+)">([^<]+)</a>\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}:\d{2})\s+(\S+)')
SIZE_CACHE_TTL = 600
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

    def open(self):
        if self._sock is not None:
            return
        last = None
        for _ in range(3):
            sock = None
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                sock.settimeout(self.timeout)
                self._send_auth(sock)
                self._sock = sock
                return
            except OSError as exc:
                last = exc
                if sock:
                    sock.close()
                time.sleep(0.2)
        if last:
            raise last
        raise ConnectionError('no session')

    def refresh(self):
        if self._sock is None:
            self.open()
            return
        try:
            self._send_auth(self._sock)
        except OSError:
            self.close()
            self.open()

    def close(self):
        if self._sock is not None:
            self._sock.close()
            self._sock = None

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

    def list_files(self, include_external=True):
        out = []
        last_error = None
        any_success = False
        roots = self.roots if include_external else self.roots[:1]
        for root in roots:
            try:
                req = Request(root['url'], headers={'User-Agent': 'LunaDL/0.1'})
                html = urlopen(req, timeout=8).read().decode('utf-8', 'replace')
                any_success = True
                items = parse_luna_index(html, root['url'], root['id'], root['label'])
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
            except Exception as exc:
                last_error = exc
        if not any_success and last_error:
            raise last_error
        return out
