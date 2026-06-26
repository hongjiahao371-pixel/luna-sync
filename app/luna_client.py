import re, socket, threading, time
from html import unescape
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_HOST = "192.168.42.1"
DEFAULT_CAMERA_URL = "http://" + DEFAULT_HOST + "/storage_internal/DCIM/Camera01/"
AUTH_PAYLOADS = [
    bytes([0x55,0x43,0x44,0x32,0x01,0x0C,0x05,0x0F,0x00,0x00,0x00,0x00,0x37,0x05,0x47,0x7C]),
    bytes([0x55,0x43,0x44,0x32,0x01,0x0C,0x04,0x10,0x0F,0x00,0x00,0x00,0x08,0x00,0x02,0x01,0x00,0x00,0x80,0x00,0x00,0x08,0x30,0x08,0x0F,0x08,0x0B,0x7C,0x00,0x8E,0x7C]),
]
INDEX_RE = re.compile(r'<a href="([^"]+)">([^<]+)</a>\s+(\d{2}-[A-Za-z]{3}-\d{4})\s+(\d{2}:\d{2})\s+(\S+)')

def parse_size(text):
    m = re.fullmatch(r'(\d+(?:\.\d+)?)([KMG])?', text.strip())
    if not m:
        return None
    mult = {'K': 1024, 'M': 1024**2, 'G': 1024**3}.get(m.group(2), 1)
    return int(float(m.group(1)) * mult)

def file_kind(name):
    s = name.rsplit('.', 1)[-1].upper() if '.' in name else ''
    if s in ('MP4', 'LRV', 'MOV', 'JPG', 'JPEG', 'PNG', 'WEBP', 'GIF', 'LIV', 'INSP'):
        return s
    return s or 'FILE'

def parse_luna_index(html, base=DEFAULT_CAMERA_URL):
    out = []
    for m in INDEX_RE.finditer(html):
        name = unescape(m.group(2))
        href = unescape(m.group(1))
        if name == '../' or href == '../':
            continue
        out.append({
            'name': name, 'href': href, 'url': urljoin(base, href),
            'date': m.group(3), 'time': m.group(4),
            'size_text': m.group(5), 'bytes': parse_size(m.group(5)),
            'kind': file_kind(name),
        })
    return out

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
        self.url = "http://" + host + "/storage_internal/DCIM/Camera01/"
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

    def list_files(self):
        req = Request(self.url, headers={'User-Agent': 'LunaDL/0.1'})
        html = urlopen(req, timeout=8).read().decode('utf-8', 'replace')
        return parse_luna_index(html, self.url)
