import os, sys, json, time, threading, socket, subprocess, logging, io, mimetypes, ipaddress
import hashlib, hmac, re, secrets, ssl, datetime, shutil, zipfile, base64
import luna_client
import urllib.request, urllib.error
from flask import Flask, jsonify, request, render_template, send_file, Response, abort, redirect
from werkzeug.exceptions import HTTPException
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import luna_client
from luna_client import LunaClient, file_kind
from downloader import download_file
import wifi
try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageStat
except Exception:
    Image = None

with open(os.environ.get('LUNA_CONFIG', '/app/config.json')) as config_file:
    CFG = json.load(config_file)
logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
log = logging.getLogger('luna')
app = Flask(__name__)

PRIVACY_VERSION = '2026-09-25'
PRIVACY_POLICY_URL = '/privacy'
TERMS_URL = '/terms'
DECLINED_URL = '/declined'

TLS_ENABLED = False

@app.after_request
def security_headers(response):
    if request.path == '/':
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    if TLS_ENABLED and request.is_secure:
        response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000')
    return response

HOST = CFG['camera_host']
DLDIR = os.environ.get('DOWNLOAD_DIR') or CFG['download_dir']
def configured_wifi_backend():
    return os.environ.get('LUNA_WIFI_BACKEND') or CFG.get('wifi_backend', 'auto')

# Appliance deployments (UGOS store package) set this to swap the in-app Wi-Fi
# controls for guidance pointing at the system's own Wi-Fi settings.
WIFI_GUIDANCE = (os.environ.get('LUNA_WIFI_GUIDANCE') or str(CFG.get('wifi_guidance') or '')).strip().lower()
# Store builds must not expose any in-app Wi-Fi collection surface at all:
# the form is hidden in the UI and the collection endpoints refuse to run.
STORE_WIFI_LOCKED = bool(WIFI_GUIDANCE)

WIFI_BACKEND = wifi.configure(configured_wifi_backend(), CFG.get('wpa_ctrl'))
IFACE = None
backend_lk = threading.Lock()

def config_value(value):
    text = str(value or '').strip()
    return '' if text.upper().startswith('YOUR_') else text

CAM_SSID = config_value(CFG.get('camera_ssid'))
DEF_PW = config_value(CFG.get('camera_password')) or None
AUTO_INTERVAL = max(10, int(CFG.get('auto_sync_interval_sec', 30)))
STATE_DIR = os.environ.get('STATE_DIR') or CFG.get('state_dir', '/state')
THUMB_DIR = os.path.join(STATE_DIR, 'thumbs')
ENC_DIR = os.path.join(STATE_DIR, 'encoded')
PREVIEW_SRC_DIR = os.path.join(STATE_DIR, 'preview_sources')
LIV_DIR = os.path.join(STATE_DIR, 'liv')
WIFI_FILE = os.path.join(STATE_DIR, 'wifi.json')
SETTINGS_FILE = os.path.join(STATE_DIR, 'settings.json')
PICKS_FILE = os.path.join(STATE_DIR, 'picks.json')
TRASH_DIR = os.path.join(DLDIR, '.trash')
TRASH_KEEP_DAYS = 7
PROJECTS_FILE = os.path.join(STATE_DIR, 'projects.json')
SHARES_FILE = os.path.join(STATE_DIR, 'shares.json')
SCORES_CACHE_TTL = 86400
SCORES_FILE = os.path.join(STATE_DIR, 'scores.json')
for d in (DLDIR, THUMB_DIR, ENC_DIR, PREVIEW_SRC_DIR, LIV_DIR, TRASH_DIR):
    os.makedirs(d, exist_ok=True)

CAMERA_AUTH_FILE = os.path.join(STATE_DIR, 'camera_auth.json')

def load_camera_auth():
    """Per-device camera handshake data: env override > state config > vendor
    default. The first run persists a per-install copy so each deployment owns
    its own auth data instead of relying solely on a compiled-in constant."""
    payloads = None
    env = (os.environ.get('LUNA_CAMERA_AUTH') or '').strip()
    if env:
        payloads = [part for part in re.split(r'[,;\s]+', env) if part]
    if not payloads:
        try:
            if os.path.exists(CAMERA_AUTH_FILE):
                with open(CAMERA_AUTH_FILE) as auth_file:
                    data = json.load(auth_file)
                payloads = [str(h) for h in data.get('payloads', []) if h]
        except Exception as e:
            log.warning('load_camera_auth:' + str(e)[:50])
            payloads = None
    parsed = []
    for hex_text in payloads or []:
        try:
            blob = bytes.fromhex(re.sub(r'[^0-9a-fA-F]', '', hex_text))
            if blob:
                parsed.append(blob)
        except ValueError:
            continue
    if not parsed:
        parsed = [bytes(p) for p in luna_client.AUTH_PAYLOADS]
        try:
            os.makedirs(STATE_DIR, exist_ok=True)
            if not os.path.exists(CAMERA_AUTH_FILE):
                with open(CAMERA_AUTH_FILE, 'w') as auth_file:
                    json.dump({'payloads': [p.hex() for p in parsed]}, auth_file)
                os.chmod(CAMERA_AUTH_FILE, 0o600)
        except Exception as e:
            log.warning('save_camera_auth:' + str(e)[:50])
    return parsed

CAMERA_CLIENT = LunaClient(HOST, auth_payloads=load_camera_auth())

lk = threading.RLock()
scan_lk = threading.Lock()
refresh_lk = threading.Lock()
auto_sync_lk = threading.Lock()
preview_lk = threading.Lock()
picks_lk = threading.Lock()
alb_lk = threading.Lock()
_scan_cache = {'ts': 0, 'data': None, 'rescan_ts': 0}
SCAN_CACHE_TTL = 8
SCAN_RESCAN_INTERVAL = 12

def bool_value(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'on'):
        return True
    if text in ('0', 'false', 'no', 'off'):
        return False
    return default

def load_settings():
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE) as settings_file:
                data = json.load(settings_file)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning('load_settings:' + str(e)[:50])
    return {}

def save_settings(data):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        current = load_settings()
        current.update(data)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(current, f)
        os.chmod(SETTINGS_FILE, 0o600)
    except Exception as e:
        log.warning('save_settings:' + str(e)[:50])

def rewrite_settings(mutate):
    """Apply mutations to the on-disk settings in one pass (supports key removal)."""
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        current = load_settings()
        mutate(current)
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(current, f)
        os.chmod(SETTINGS_FILE, 0o600)
    except Exception as e:
        log.warning('rewrite_settings:' + str(e)[:50])

SETTINGS = load_settings()

def _triggered_scan():
    now = time.time()
    with scan_lk:
        if _scan_cache['data'] is not None and now - _scan_cache['ts'] < SCAN_CACHE_TTL:
            return _scan_cache['data'], True
        if now - _scan_cache['rescan_ts'] >= SCAN_RESCAN_INTERVAL:
            wifi.rescan(IFACE)
            _scan_cache['rescan_ts'] = now
        r = wifi.scan(IFACE)
        _scan_cache['data'] = r.stdout
        _scan_cache['ts'] = now
        return r.stdout, False

ST = {'connected': False, 'wifi_conn': False, 'files': [], 'queue': [], 'current': None,
      'completed': 0, 'log': [], 'wifi_current': '', 'wifi_target': CAM_SSID, 'wifi_password': None,
      'wifi_saved': False, 'transcodes': {}, 'auto_sync': bool_value(SETTINGS.get('auto_sync'), bool_value(CFG.get('auto_sync'), False)),
      'auto_sync_lrv': bool_value(SETTINGS.get('auto_sync_lrv'), bool_value(CFG.get('auto_sync_lrv'), True)),
      'last_auto_sync': '', 'privacy_version': str(SETTINGS.get('privacy_version') or ''),
      'active_key': None}
auto_downloads = set()
cancel = threading.Event()
last_auto_notice = 0

def addlog(m):
    with lk:
        ST['log'].append(m); ST['log'] = ST['log'][-150:]
    log.info(m)

def privacy_accepted():
    with lk:
        return ST['privacy_version'] == PRIVACY_VERSION

def run(args, t=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=t)

TLS_DIR = os.path.join(STATE_DIR, 'tls')

def tls_mode():
    return (os.environ.get('LUNA_TLS') or str(CFG.get('tls') or 'auto')).strip().lower()

def _write_tls_files(cert_pem, key_pem, cert_path, key_path):
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)
    flag = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(cert_path, flag, 0o644)
    with os.fdopen(fd, 'wb') as f:
        f.write(cert_pem)
    fd = os.open(key_path, flag, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(key_pem)

def generate_self_signed_certificate(cert_path, key_path):
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Luna Sync')])
        now = datetime.datetime.now(datetime.timezone.utc)
        san = x509.SubjectAlternativeName([
            x509.DNSName('localhost'),
            x509.DNSName('LunaSync'),
            x509.IPAddress(ipaddress.ip_address('127.0.0.1')),
            x509.IPAddress(ipaddress.ip_address('::1')),
        ])
        cert = (x509.CertificateBuilder()
                .subject_name(subject).issuer_name(subject)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - datetime.timedelta(days=1))
                .not_valid_after(now + datetime.timedelta(days=3650))
                .add_extension(san, critical=False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=False)
                .sign(key, hashes.SHA256()))
        _write_tls_files(cert.public_bytes(serialization.Encoding.PEM),
                         key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.TraditionalOpenSSL,
                                           serialization.NoEncryption()),
                         cert_path, key_path)
        return True
    except Exception as cert_error:
        log.warning('tls(cryptography):' + str(cert_error)[:80])
    try:
        result = run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256',
                      '-days', '3650', '-nodes',
                      '-keyout', key_path, '-out', cert_path,
                      '-subj', '/CN=Luna Sync',
                      '-addext', 'subjectAltName=DNS:localhost,DNS:LunaSync,IP:127.0.0.1,IP:::1'], 60)
        if result.returncode == 0 and os.path.exists(cert_path) and os.path.exists(key_path):
            os.chmod(key_path, 0o600)
            return True
        log.warning('tls(openssl):' + (result.stderr or '')[:80])
    except Exception as openssl_error:
        log.warning('tls(openssl):' + str(openssl_error)[:80])
    return False

def build_ssl_context():
    mode = tls_mode()
    if mode in ('off', 'false', '0', 'no', 'disabled'):
        return None
    cert = os.environ.get('LUNA_TLS_CERT') or CFG.get('tls_cert')
    key = os.environ.get('LUNA_TLS_KEY') or CFG.get('tls_key')
    custom = bool(cert and key)
    if not custom:
        cert = os.path.join(TLS_DIR, 'cert.pem')
        key = os.path.join(TLS_DIR, 'key.pem')
        if not (os.path.exists(cert) and os.path.exists(key)):
            if not generate_self_signed_certificate(cert, key):
                addlog('TLS 证书生成失败，已回退明文 HTTP；建议安装 python3-cryptography 或配置 LUNA_TLS_CERT/LUNA_TLS_KEY')
                return None
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        return context
    except Exception as e:
        addlog('TLS 证书加载失败(' + str(cert) + '): ' + str(e)[:60] + '，已回退明文 HTTP')
        return None

def refresh_wifi_backend(start_wpa=False):
    global WIFI_BACKEND, IFACE
    with backend_lk:
        old_backend, old_iface = WIFI_BACKEND, IFACE
        WIFI_BACKEND = wifi.configure(configured_wifi_backend(), CFG.get('wpa_ctrl'))
        IFACE = wifi.detect_interface(CFG.get('wifi_iface'))
        changed = (old_backend, old_iface) != (WIFI_BACKEND, IFACE)
    if start_wpa and WIFI_BACKEND == 'wpa_supplicant' and IFACE:
        wifi.ensure_wpa_supplicant(IFACE)
    if changed:
        addlog('WiFi 后端更新: ' + WIFI_BACKEND + '，无线网卡: ' + (IFACE or '未检测到'))
    return WIFI_BACKEND, IFACE

def current_ssid():
    refresh_wifi_backend()
    return wifi.current_ssid(IFACE)

def camera_client_cidr():
    configured = CFG.get('camera_client_cidr') or CFG.get('camera_client_ip')
    if configured:
        return configured if '/' in str(configured) else str(configured) + '/24'
    try:
        host = ipaddress.ip_address(HOST)
        network = ipaddress.ip_network(str(host) + '/24', strict=False)
        last = int(str(host).rsplit('.', 1)[1])
        client_last = 2 if last != 2 else 3
        return str(ipaddress.ip_address(int(network.network_address) + client_last)) + '/24'
    except Exception:
        return ''

def ensure_camera_ipv4():
    if WIFI_BACKEND != 'wpa_supplicant' or not IFACE:
        return
    try:
        run(['ip', 'link', 'set', IFACE, 'up'], 8)
    except Exception:
        pass
    cidr = camera_client_cidr()
    if not cidr:
        return
    ip = cidr.split('/', 1)[0]
    try:
        current = run(['ip', '-4', 'addr', 'show', 'dev', IFACE], 5)
        if ip in current.stdout:
            return
        result = run(['ip', 'addr', 'replace', cidr, 'dev', IFACE], 8)
        if result.returncode == 0:
            addlog('已配置相机网段地址 ' + cidr)
        else:
            addlog('配置相机网段地址失败: ' + (result.stderr or result.stdout).strip()[:80])
    except Exception as e:
        log.warning('camera_ipv4:' + str(e)[:60])

def wifi_on_target():
    if not wifi.requires_target_ssid():
        return True
    with lk:
        target = ST.get('wifi_target') or CAM_SSID
    cur = current_ssid()
    ok = bool(cur and (cur == target if target else looks_like_luna_ssid(cur)))
    if ok:
        ensure_camera_ipv4()
    return ok


def debounced_connection(probe_ok, previous, failures):
    if probe_ok:
        return True, 0
    failures += 1
    return bool(previous and failures < 2), failures


def cam_on():
    try:
        socket.create_connection((HOST, 80), 2).close(); return True
    except OSError:
        return False

WIFI_KEY_FILE = os.path.join(STATE_DIR, 'wifi.key')
SECRET_ENC_PREFIX = 'enc:v1:'

def _secret_key():
    """Per-install 256-bit key kept in a 0600 file in the state directory."""
    try:
        if os.path.exists(WIFI_KEY_FILE):
            with open(WIFI_KEY_FILE) as key_file:
                key = bytes.fromhex(key_file.read().strip())
            if len(key) == 32:
                return key
        key = secrets.token_bytes(32)
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(WIFI_KEY_FILE, 'w') as key_file:
            key_file.write(key.hex())
        os.chmod(WIFI_KEY_FILE, 0o600)
        return key
    except Exception as e:
        log.warning('secret_key:' + str(e)[:50])
        return None

def _secret_keystream(key, nonce, length):
    stream = bytearray()
    counter = 0
    while len(stream) < length:
        block = hmac.new(key, nonce + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        stream.extend(block)
        counter += 1
    return bytes(stream[:length])

def encrypt_secret(plaintext):
    """Encrypt a credential for at-rest storage (HMAC-SHA256 CTR + MAC tag)."""
    data = str(plaintext or '').encode('utf-8')
    key = _secret_key()
    if not data or key is None:
        return ''
    nonce = secrets.token_bytes(12)
    mask = _secret_keystream(key, nonce, len(data))
    cipher = bytes(a ^ b for a, b in zip(data, mask))
    tag = hmac.new(key, nonce + cipher, hashlib.sha256).digest()
    return SECRET_ENC_PREFIX + base64.b64encode(nonce + tag + cipher).decode('ascii')

def decrypt_secret(token):
    """Inverse of encrypt_secret; legacy plaintext values are returned as-is."""
    token = str(token or '')
    if not token.startswith(SECRET_ENC_PREFIX):
        return token
    try:
        raw = base64.b64decode(token[len(SECRET_ENC_PREFIX):])
        nonce, tag, cipher = raw[:12], raw[12:44], raw[44:]
        key = _secret_key()
        if key is None or len(cipher) == 0:
            return ''
        if not hmac.compare_digest(hmac.new(key, nonce + cipher, hashlib.sha256).digest(), tag):
            return ''
        mask = _secret_keystream(key, nonce, len(cipher))
        return bytes(a ^ b for a, b in zip(cipher, mask)).decode('utf-8')
    except Exception:
        return ''

def load_saved_wifi():
    try:
        if os.path.exists(WIFI_FILE):
            with open(WIFI_FILE) as wifi_file:
                data = json.load(wifi_file)
            if data.get('ssid') and data.get('password'):
                data['password'] = decrypt_secret(data.get('password'))
                if not data.get('password'):
                    log.warning('saved wifi password unreadable (key mismatch?)')
                    return None
                return data
            if data.get('ssid'):
                log.warning('saved wifi has no password: ' + data.get('ssid', '')[:60])
    except Exception as e:
        log.warning('load_saved_wifi:' + str(e)[:50])
        pass
    return None

def save_wifi(ssid, pw):
    try:
        if STORE_WIFI_LOCKED:
            addlog('商店版不保存 WiFi（应用内不收集 Wi-Fi 信息）')
            return
        existing = load_saved_wifi() or {}
        if not pw and existing.get('ssid') == ssid and existing.get('password'):
            pw = existing['password']
        if not pw and CAM_SSID and DEF_PW and ssid == CAM_SSID:
            pw = DEF_PW
        if not pw:
            addlog('未保存 WiFi: 密码为空')
            return
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(WIFI_FILE, 'w') as f:
            json.dump({'ssid': ssid, 'password': encrypt_secret(pw)}, f)
        os.chmod(WIFI_FILE, 0o600)
        with lk:
            ST['wifi_target'] = ssid; ST['wifi_password'] = pw; ST['wifi_saved'] = True
        addlog('已记住 WiFi: ' + ssid)
    except Exception as e:
        log.warning('save_wifi:' + str(e)[:50])

wifi_state_lk = threading.Lock()
wifi_state_loaded = False

def load_wifi_state():
    global wifi_state_loaded
    with wifi_state_lk:
        if wifi_state_loaded:
            return
        saved = load_saved_wifi()
        with lk:
            if saved and saved.get('ssid') and saved.get('password'):
                ST['wifi_target'] = saved['ssid']
                ST['wifi_password'] = saved['password']
                ST['wifi_saved'] = True
                message = '加载记住的WiFi: ' + saved['ssid']
            elif CAM_SSID and DEF_PW:
                ST['wifi_target'] = CAM_SSID
                ST['wifi_password'] = DEF_PW
                ST['wifi_saved'] = False
                message = '加载配置中的WiFi: ' + CAM_SSID
            else:
                message = ''
        wifi_state_loaded = True
    if message:
        addlog(message)

def looks_like_luna_ssid(ssid):
    return str(ssid or '').strip().lower().startswith('luna ')

def is_camera_ssid(ssid):
    return bool(ssid and ((CAM_SSID and ssid == CAM_SSID) or (not CAM_SSID and looks_like_luna_ssid(ssid))))

def try_connect(ssid, pw):
    refresh_wifi_backend(start_wpa=True)
    if not wifi.can_control():
        addlog('当前为手动连接模式，不管理 WiFi')
        return cam_on()
    if not IFACE:
        addlog('未检测到无线网卡')
        return False
    addlog('连接 ' + ssid + ' ...')
    found = False
    for _ in range(3):
        stdout, _ = _triggered_scan()
        found = ssid in (line.split(':', 1)[0] for line in stdout.splitlines())
        if found:
            break
        time.sleep(2)
    if not found:
        if WIFI_BACKEND != 'wpa_supplicant':
            addlog('未扫到 ' + ssid); return False
        addlog('本轮未扫到 ' + ssid + '，继续尝试连接')
    result = wifi.connect(IFACE, ssid, pw)
    if result.returncode != 0:
        addlog('连接失败: ' + (result.stderr or result.stdout).strip()[:80])
    ok = False
    detail = ''
    for _ in range(35):
        time.sleep(1)
        if WIFI_BACKEND == 'wpa_supplicant':
            state = run(['wpa_cli', '-i', IFACE, '-p', CFG.get('wpa_ctrl', '/run/wpa_supplicant'), 'status'], 2)
            fields = {}
            for line in state.stdout.splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    fields[k] = v
            if fields.get('wpa_state') == 'COMPLETED' and fields.get('ssid') == ssid:
                ok = True
                break
            if fields.get('wpa_state'):
                detail = '（wpa_state=' + fields['wpa_state'] + '）'
        elif current_ssid() == ssid:
            ok = True
            break
    if ok:
        ensure_camera_ipv4()
        addlog('已连 ' + ssid)
    else:
        addlog('连接 ' + ssid + ' 失败' + detail)
    return ok

def trigger_auto_sync_check(reason):
    if not privacy_accepted():
        return
    with lk:
        enabled = ST['auto_sync']
    if not enabled:
        return
    addlog(reason)
    threading.Thread(target=auto_sync_once, kwargs={'manual': True}, daemon=True).start()

def keeper():
    camera_failures = 0
    while True:
        try:
            if not privacy_accepted():
                time.sleep(2)
                continue
            load_wifi_state()
            refresh_wifi_backend()
            cur = current_ssid()
            with lk:
                ST['wifi_current'] = cur
            on_target = wifi_on_target()
            probe_ok = on_target and cam_on()
            with lk:
                was_connected = ST['connected']
                if on_target:
                    connected, camera_failures = debounced_connection(
                        probe_ok, was_connected, camera_failures,
                    )
                else:
                    connected, camera_failures = False, 0
                ST['connected'] = connected
            if probe_ok and not was_connected:
                trigger_auto_sync_check('检测到 Luna 已连接，开始自动同步检查')
        except Exception as e:
            log.warning('keeper:' + str(e)[:50])
        time.sleep(12)

def camera_keepalive_worker():
    while True:
        try:
            if privacy_accepted() and wifi_on_target() and cam_on():
                CAMERA_CLIENT.keepalive()
        except Exception as e:
            log.warning('camera_keepalive:' + str(e)[:60])
        time.sleep(3)

def local_files():
    out = {}
    if os.path.isdir(DLDIR):
        for root, _, files in os.walk(DLDIR):
            for f in files:
                if f.endswith('.part'):
                    continue
                p = os.path.join(root, f)
                # never follow user-visible symlinks: a planted link would turn
                # the library browser into an arbitrary file reader
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                rel = os.path.relpath(p, DLDIR)
                out[rel] = {'path': p, 'size': os.path.getsize(p)}
    return out

def local_items():
    loc = local_files()
    with lk:
        meta = {f.get('id', f['name']): dict(f) for f in ST['files']}
    items = []
    for key, info in sorted(loc.items()):
        name = os.path.basename(key)
        item = meta.get(key) or meta.get('internal/' + key) or {'id': key, 'name': name, 'kind': file_kind(name), 'date': '', 'time': '', 'size_text': ''}
        item = dict(item)
        item.setdefault('id', key)
        item['bytes'] = info['size']
        item['status'] = '完成(本地)'
        items.append(item)
    return items

def safe_path(base, name):
    # realpath containment: a symlink planted inside the tree that resolves
    # outside the base (arbitrary file read) is rejected the same as '../'
    root = os.path.realpath(os.path.abspath(base))
    path = os.path.realpath(os.path.join(root, name))
    if path == root or not path.startswith(root + os.sep):
        abort(400)
    return path

def local_path(name):
    loc = local_files()
    if name in loc:
        return loc[name]['path']
    storage, base = split_file_id(name)
    if storage == 'internal':
        legacy = safe_path(DLDIR, base)
        if os.path.isfile(legacy):
            return legacy
    path = safe_path(DLDIR, name)
    return path if os.path.isfile(path) else None

def split_file_id(value):
    parts = value.split('/', 1)
    if len(parts) == 2 and parts[0] in ('internal', 'external') and parts[1]:
        return parts[0], parts[1]
    return 'internal', os.path.basename(value)

def file_key(item):
    return item.get('id') or ((item.get('storage') or 'internal') + '/' + item['name'])


def local_file_info(item, local):
    info = local.get(file_key(item))
    if info is None and item.get('storage') == 'internal':
        info = local.get(item['name'])
    return info


def local_file_complete(item, local):
    info = local_file_info(item, local)
    if info is None:
        return False
    if item.get('bytes_exact') and item.get('bytes') is not None:
        return info['size'] == item['bytes']
    return True


def local_dest_for(item):
    return safe_path(DLDIR, file_key(item))

def refresh():
    if not privacy_accepted():
        return False
    with refresh_lk:
        if not (wifi_on_target() and cam_on()):
            with lk:
                ST['connected'] = False
            return False
        try:
            CAMERA_CLIENT.connect()
            files = CAMERA_CLIENT.list_files()
            loc = local_files()
            for f in files:
                key = file_key(f)
                f['id'] = key
                f['status'] = '完成' if local_file_complete(f, loc) else '就绪'
            with lk:
                ST['files'] = files; ST['connected'] = True
            return True
        except Exception as e:
            addlog('列文件失败:' + str(e)[:60])
            with lk:
                ST['connected'] = False
            return False

def enqueue(names, source='manual'):
    loc = local_files()
    added = 0
    skipped = []
    with lk:
        if source == 'auto' and not ST['auto_sync']:
            return 0, [{'name': name, 'reason': 'auto_disabled'} for name in names]
        current = ST['current'].get('id') if ST['current'] else None
        known = {file_key(f): f for f in ST['files']}
        by_name = {f['name']: file_key(f) for f in ST['files']}
        for name in names:
            key = name if name in known else by_name.get(name, name)
            item = known.get(key)
            if item and local_file_complete(item, loc):
                skipped.append({'name': name, 'reason': 'already_local'})
                continue
            if key in ST['queue'] or key == current:
                if source != 'auto' and key in ST['queue']:
                    auto_downloads.discard(key)
                skipped.append({'name': name, 'reason': 'already_queued'})
                continue
            if key not in known:
                skipped.append({'name': name, 'reason': 'not_available'})
                continue
            ST['queue'].append(key)
            if source == 'auto':
                auto_downloads.add(key)
            added += 1
    return added, skipped

def stop_auto_sync_downloads():
    with lk:
        kept = []
        removed = 0
        for key in ST['queue']:
            if key in auto_downloads:
                auto_downloads.discard(key)
                removed += 1
            else:
                kept.append(key)
        ST['queue'] = kept
        active_key = ST['active_key']
        current = ST['current']
        stop_current = active_key in auto_downloads or bool(current and current.get('source') == 'auto')
        if stop_current:
            cancel.set()
    return removed, stop_current

def auto_sync_once(manual=False):
    if not privacy_accepted():
        return 0
    if not auto_sync_lk.acquire(blocking=False):
        if manual:
            addlog('自动同步已在运行')
        return 0
    try:
        with lk:
            if not ST['auto_sync']:
                return 0
            busy = bool(ST['current'] or ST['queue'])
        if busy:
            if manual:
                addlog('自动同步队列执行中，本轮无需重复扫描')
            return 0
        if not prepare_auto_sync_connection():
            if manual:
                addlog('自动同步未开始: 相机未就绪')
            return 0
        if not refresh():
            if manual:
                addlog('自动同步未开始: 扫描相机文件失败')
            return 0
        with lk:
            include_lrv = ST['auto_sync_lrv']
            files = list(ST['files'])
            names = [file_key(f) for f in files if include_lrv or f.get('kind') != 'LRV']
            skipped_lrv = len(files) - len(names)
        if skipped_lrv and manual:
            addlog('自动同步跳过 ' + str(skipped_lrv) + ' 个 LRV 文件')
        added, _ = enqueue(names, source='auto')
        with lk:
            if not ST['auto_sync']:
                return 0
            ST['last_auto_sync'] = time.strftime('%H:%M:%S')
        if added:
            addlog('自动同步加入 ' + str(added) + ' 个新文件')
        elif manual:
            addlog('自动同步检查完成，没有新文件')
        return added
    finally:
        auto_sync_lk.release()

def auto_notice(message):
    global last_auto_notice
    now = time.time()
    if now - last_auto_notice > 300:
        addlog(message)
        last_auto_notice = now

def prepare_auto_sync_connection():
    if not privacy_accepted():
        return False
    refresh_wifi_backend(start_wpa=True)
    if wifi_on_target() and cam_on():
        return True
    with lk:
        target = ST['wifi_target']; pw = ST['wifi_password'] or DEF_PW; saved = ST['wifi_saved']
    if wifi.can_control() and target and pw is not None:
        return try_connect(target, pw) and wifi_on_target() and cam_on()
    if wifi.can_control() and not target:
        auto_notice('自动同步等待记住 Luna WiFi')
    elif not wifi.can_control():
        auto_notice('自动同步等待手动连接 Luna WiFi')
    elif not saved:
        auto_notice('自动同步需要先记住 Luna WiFi 密码')
    return False

def auto_sync_worker():
    while True:
        try:
            if not privacy_accepted():
                time.sleep(2)
                continue
            with lk:
                enabled = ST['auto_sync']
            if enabled:
                auto_sync_once()
        except Exception as e:
            log.warning('auto_sync:' + str(e)[:60])
        time.sleep(AUTO_INTERVAL)


def postpone_unavailable_download(key, source):
    with lk:
        was_cancelled = cancel.is_set()
        ST['active_key'] = None
        if was_cancelled or (source == 'auto' and not ST['auto_sync']):
            auto_downloads.discard(key)
        elif key not in ST['queue']:
            ST['queue'].insert(0, key)
        if was_cancelled:
            cancel.clear()
    return was_cancelled


def dl_worker():
    while True:
        key = None
        source = 'manual'
        with lk:
            if ST['queue']:
                key = ST['queue'].pop(0)
                source = 'auto' if key in auto_downloads else 'manual'
                cancel.clear()
                ST['active_key'] = key
        if not key:
            time.sleep(2); continue
        with lk:
            f = next((x for x in ST['files'] if file_key(x) == key or x['name'] == key), None)
        if not f:
            addlog(key + ' 不在列表')
            with lk:
                ST['active_key'] = None
                auto_downloads.discard(key)
            continue
        name = f['name']
        key = file_key(f)
        if not (wifi_on_target() and cam_on()):
            was_cancelled = postpone_unavailable_download(key, source)
            if was_cancelled:
                addlog(('自动同步已停止 ' if source == 'auto' else '已取消 ') + name)
            else:
                time.sleep(15)
            continue
        try:
            dest = local_dest_for(f)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
        except Exception as e:
            addlog('准备下载失败 ' + name + ':' + str(e)[:60])
            with lk:
                ST['active_key'] = None
                auto_downloads.discard(key)
                cancel.clear()
            continue
        with lk:
            ST['current'] = {'id': key, 'name': name, 'downloaded': 0,
                             'total': f.get('bytes'), 'speed': 0, 'source': source}
        addlog('开始下载 ' + name)
        try:
            CAMERA_CLIENT.connect()
            def prog(n, d, t, s):
                with lk:
                    ST['current'] = {'id': key, 'name': n, 'downloaded': d,
                                     'total': t, 'speed': s, 'source': source}
            expected_size = f.get('bytes') if f.get('bytes_exact') else None
            download_file(f['url'], dest, on_progress=prog, cancel=cancel,
                          expected_size=expected_size)
            with lk:
                ST['completed'] += 1
            addlog('完成 ' + name)
        except Exception as e:
            if str(e) == 'cancelled':
                addlog(('自动同步已停止 ' if source == 'auto' else '已取消 ') + name)
            else:
                addlog('失败 ' + name + ':' + str(e)[:60])
        finally:
            with lk:
                ST['current'] = None
                ST['active_key'] = None
                auto_downloads.discard(key)
                cancel.clear()


def transcode_worker(name):
    out = safe_path(ENC_DIR, name + '.mp4')
    os.makedirs(os.path.dirname(out), exist_ok=True)
    local = local_path(name)
    src_file = local or safe_path(PREVIEW_SRC_DIR, name)
    os.makedirs(os.path.dirname(src_file), exist_ok=True)
    try:
        if not os.path.exists(src_file):
            url = file_url(name)
            if not url:
                with lk: ST['transcodes'][name] = {'status': 'failed', 'msg': '无URL(先扫描文件)'}
                return
            with lk: ST['transcodes'][name] = {'status': 'downloading'}
            CAMERA_CLIENT.connect()
            item = file_info(name)
            expected_size = item.get('bytes') if item and item.get('bytes_exact') else None
            download_file(url, src_file, expected_size=expected_size)
        with lk: ST['transcodes'][name] = {'status': 'encoding'}
        r = run([
            'ffmpeg', '-y', '-i', src_file, '-c:v', 'libx264', '-preset', 'veryfast',
            '-crf', '26', '-c:a', 'aac', '-movflags', '+faststart', out,
        ], 1800)
        if os.path.exists(out) and os.path.getsize(out) > 0:
            with lk: ST['transcodes'][name] = {'status': 'done'}
            addlog('转码完成 ' + name)
        else:
            with lk: ST['transcodes'][name] = {'status': 'failed', 'msg': (r.stderr or '')[-80:]}
    except Exception as e:
        with lk: ST['transcodes'][name] = {'status': 'failed', 'msg': str(e)[:80]}
        log.warning('transcode ' + name + ':' + str(e)[:60])

def file_info(name):
    with lk:
        f = next((x for x in ST['files'] if file_key(x) == name or x['name'] == name), None)
    return dict(f) if f else None


def file_url(name):
    f = file_info(name)
    return f['url'] if f else None


@app.route('/api/transcode/status/<path:name>')
def api_tc_status(name):
    with lk:
        st = dict(ST['transcodes'].get(name, {'status': 'pending'}))
    out = safe_path(ENC_DIR, name + '.mp4')
    if os.path.exists(out):
        st['status'] = 'done'
    return jsonify(st)

@app.route('/api/transcode/<path:name>', methods=['POST'])
def api_tc_start(name):
    out = safe_path(ENC_DIR, name + '.mp4')
    if os.path.exists(out):
        return jsonify({'status': 'done'})
    with lk:
        cur = ST['transcodes'].get(name, {}).get('status')
        if cur not in ('downloading', 'encoding'):
            ST['transcodes'][name] = {'status': 'pending'}
            threading.Thread(target=transcode_worker, args=(name,), daemon=True).start()
    return jsonify({'status': 'started'})

@app.route('/play/<path:name>')
def play(name):
    out = safe_path(ENC_DIR, name + '.mp4')
    if not os.path.exists(out):
        abort(404)
    return send_file(out, mimetype='video/mp4')

@app.route('/')
def idx():
    return render_template('index.html')

@app.route('/privacy')
def privacy_page():
    return render_template('legal.html', document='privacy')

@app.route('/terms')
def terms_page():
    return render_template('legal.html', document='terms')

@app.route('/declined')
def declined_page():
    return render_template('declined.html')

@app.route('/api/privacy', methods=['GET', 'POST'])
def api_privacy():
    if request.method == 'POST':
        data = request.get_json(silent=True) or {}
        if data.get('accepted') is not True:
            return jsonify({'ok': False, 'error': 'explicit_consent_required'}), 400
        with lk:
            ST['privacy_version'] = PRIVACY_VERSION
        save_settings({'privacy_version': PRIVACY_VERSION})
        load_wifi_state()
        addlog('已同意隐私政策与用户协议')
        trigger_auto_sync_check('隐私授权完成，开始自动同步检查')
    return jsonify({'ok': True, 'accepted': privacy_accepted(),
                    'version': PRIVACY_VERSION, 'privacy_url': PRIVACY_POLICY_URL,
                    'terms_url': TERMS_URL, 'declined_url': DECLINED_URL})

@app.route('/api/privacy/withdraw', methods=['POST'])
def api_privacy_withdraw():
    """撤回隐私授权：清除同意记录、访问密码、会话与本应用保存的凭据/缓存，
    应用回到首次使用状态；素材目录中的用户文件不受影响。"""
    new_secret = secrets.token_hex(32)
    with lk:
        ST['privacy_version'] = ''
        ST['wifi_password'] = None
        ST['wifi_saved'] = False
        ST['wifi_target'] = CAM_SSID
        ST['auto_sync'] = False
        SETTINGS['privacy_version'] = ''
        SETTINGS['web_secret'] = new_secret
        SETTINGS.pop('web_password', None)
    def _purge(settings):
        settings.pop('web_password', None)
        settings.pop('auto_sync', None)
        settings.pop('auto_sync_lrv', None)
        settings['privacy_version'] = ''
        settings['web_secret'] = new_secret
    rewrite_settings(_purge)
    for path in (WIFI_FILE, PICKS_FILE, PROJECTS_FILE, SCORES_FILE):
        try:
            if os.path.exists(path):
                os.remove(path)
        except OSError as e:
            log.warning('withdraw remove:' + str(e)[:50])
    for cache_dir in (THUMB_DIR, ENC_DIR, PREVIEW_SRC_DIR, LIV_DIR):
        try:
            shutil.rmtree(cache_dir, ignore_errors=True)
            os.makedirs(cache_dir, exist_ok=True)
        except OSError as e:
            log.warning('withdraw cache:' + str(e)[:50])
    addlog('用户已撤回隐私授权，应用数据已清除（素材目录未动）')
    response = jsonify({'ok': True, 'declined_url': DECLINED_URL})
    response.delete_cookie(AUTH_COOKIE, path='/')
    return response

AUTH_COOKIE = 'luna_session'
AUTH_SESSION_SECONDS = 30 * 24 * 3600
AUTH_PUBLIC_PATHS = {'/login', '/privacy', '/terms', '/declined', '/api/privacy',
                     '/api/auth-state', '/api/auth/login', '/api/auth/setup',
                     '/api/auth/logout'}

def config_auth_password():
    value = (os.environ.get('LUNA_AUTH_TOKEN') or config_value(CFG.get('web_auth_token')) or '').strip()
    return value or None

def hash_password(password, iterations=120000):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, iterations)
    return 'pbkdf2$%d$%s$%s' % (iterations, salt.hex(), digest.hex())

def verify_password(password, record):
    try:
        scheme, iterations, salt_hex, digest_hex = str(record).split('$')
        if scheme != 'pbkdf2':
            return False
        digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'),
                                     bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False

def auth_password_set():
    return bool(config_auth_password() or SETTINGS.get('web_password'))

def check_web_password(password):
    configured = config_auth_password()
    if configured:
        return hmac.compare_digest(str(password), configured)
    record = SETTINGS.get('web_password')
    return bool(record) and verify_password(password, record)

def web_secret():
    secret = SETTINGS.get('web_secret')
    if secret:
        return str(secret)
    secret = os.urandom(32).hex()
    save_settings({'web_secret': secret})
    SETTINGS['web_secret'] = secret
    return secret

def session_signature(expires):
    return hmac.new(web_secret().encode(), ('%d' % expires).encode(), hashlib.sha256).hexdigest()

def session_cookie_value():
    expires = int(time.time()) + AUTH_SESSION_SECONDS
    return '%d.%s' % (expires, session_signature(expires))

def session_valid():
    value = request.cookies.get(AUTH_COOKIE, '')
    if '.' not in value:
        return False
    expires, _, signature = value.partition('.')
    if not expires.isdigit() or int(expires) < time.time():
        return False
    return hmac.compare_digest(session_signature(int(expires)), signature)

def attach_session(response):
    response.set_cookie(AUTH_COOKIE, session_cookie_value(), max_age=AUTH_SESSION_SECONDS,
                        httponly=True, samesite='Lax', secure=TLS_ENABLED, path='/')
    return response

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/api/auth-state')
def api_auth_state():
    return jsonify({'password_set': auth_password_set(), 'authenticated': session_valid(),
                    'managed_by_config': bool(config_auth_password()),
                    'wifi_guidance': WIFI_GUIDANCE})

@app.route('/api/auth/login', methods=['POST'])
def api_auth_login():
    if not auth_password_set():
        return jsonify({'ok': False, 'error': 'password_not_set'}), 400
    data = request.get_json(silent=True) or {}
    if not check_web_password(str(data.get('password') or '')):
        addlog('Web 登录失败：密码错误')
        return jsonify({'ok': False, 'error': 'invalid_password'}), 401
    addlog('Web 登录成功')
    return attach_session(jsonify({'ok': True}))

@app.route('/api/auth/setup', methods=['POST'])
def api_auth_setup():
    if config_auth_password():
        return jsonify({'ok': False, 'error': 'password_managed_by_config'}), 400
    if SETTINGS.get('web_password'):
        return jsonify({'ok': False, 'error': 'password_already_set'}), 400
    data = request.get_json(silent=True) or {}
    password = str(data.get('password') or '')
    if len(password) < 4 or password != str(data.get('confirm') or ''):
        return jsonify({'ok': False, 'error': 'invalid_password'}), 400
    record = hash_password(password)
    save_settings({'web_password': record})
    SETTINGS['web_password'] = record
    addlog('Web 访问密码已设置')
    return attach_session(jsonify({'ok': True}))

@app.route('/api/auth/logout', methods=['POST'])
def api_auth_logout():
    response = jsonify({'ok': True})
    response.delete_cookie(AUTH_COOKIE, path='/')
    return response

@app.before_request
def require_web_auth():
    if request.path in AUTH_PUBLIC_PATHS or request.path.startswith('/share/'):
        return None
    if request.path in AUTH_PUBLIC_PATHS or request.path.startswith('/static/'):
        return None
    if session_valid():
        return None
    if request.path == '/':
        return redirect('/login')
    return jsonify({'ok': False, 'error': 'authentication_required'}), 401

@app.before_request
def require_privacy_consent():
    public_paths = {'/', '/privacy', '/terms', '/declined', '/api/privacy', '/api/state'} | AUTH_PUBLIC_PATHS
    if request.path in public_paths or request.path.startswith('/static/') \
            or request.path.startswith('/share/'):
        return None
    if not privacy_accepted():
        return jsonify({'ok': False, 'error': 'privacy_consent_required'}), 451
    return None

@app.route('/api/state')
def api_state():
    accepted = privacy_accepted()
    if accepted:
        load_wifi_state()
        refresh_wifi_backend()
    with lk:
        return jsonify({'connected': ST['connected'] if accepted else False,
            'wifi_conn': ST['wifi_conn'] if accepted else False,
            'wifi_current': ST['wifi_current'] if accepted else '',
            'wifi_saved': ST['wifi_saved'] if accepted else False,
            'file_count': len(ST['files']) if accepted else 0,
            'queue_len': len(ST['queue']) if accepted else 0,
            'current': ST['current'] if accepted else None,
            'completed': ST['completed'] if accepted else 0,
            'log': ST['log'][-12:] if accepted else [],
            'camera_ssid': CAM_SSID if accepted else '',
            'wifi_iface': IFACE if accepted else None,
            'wifi_backend': WIFI_BACKEND, 'wifi_control': accepted and wifi.can_control(),
            'wifi_guidance': WIFI_GUIDANCE,
            'wifi_target': ST['wifi_target'] if accepted else '',
            'wifi_has_password': accepted and bool(ST['wifi_password'] or DEF_PW),
            'download_dir': DLDIR if accepted else '',
            'auto_sync': ST['auto_sync'], 'auto_interval': AUTO_INTERVAL,
            'auto_sync_lrv': ST['auto_sync_lrv'],
            'last_auto_sync': ST['last_auto_sync'],
            'privacy_accepted': accepted, 'privacy_version': PRIVACY_VERSION})

@app.route('/api/auto-sync', methods=['POST'])
def api_auto_sync():
    data = request.json or {}
    with lk:
        if 'enabled' in data:
            enabled = bool_value(data.get('enabled'))
            ST['auto_sync'] = enabled
        else:
            enabled = ST['auto_sync']
        if 'include_lrv' in data:
            include_lrv = bool_value(data.get('include_lrv'), True)
            ST['auto_sync_lrv'] = include_lrv
        else:
            include_lrv = ST['auto_sync_lrv']
    if 'include_lrv' in data:
        save_settings({'auto_sync_lrv': include_lrv})
    if 'enabled' in data:
        save_settings({'auto_sync': enabled})
    stopped = (0, False)
    if 'enabled' in data and not enabled:
        stopped = stop_auto_sync_downloads()
        detail = []
        if stopped[0]:
            detail.append('移除队列 ' + str(stopped[0]) + ' 个')
        if stopped[1]:
            detail.append('正在停止当前自动下载')
        addlog('自动同步已关闭' + (': ' + '，'.join(detail) if detail else ''))
    elif 'enabled' in data:
        addlog('自动同步已开启')
    if 'include_lrv' in data:
        addlog('自动同步 LRV 已' + ('开启' if include_lrv else '关闭'))
    if 'enabled' in data and enabled:
        trigger_auto_sync_check('自动同步已开启，开始检查')
    return jsonify({'ok': True, 'auto_sync': enabled, 'auto_sync_lrv': include_lrv,
                    'removed': stopped[0], 'cancelled': stopped[1]})

@app.route('/api/wifi/scan')
def wifi_scan():
    if STORE_WIFI_LOCKED:
        return jsonify({'nets': [], 'current': '', 'camera_ssid': CAM_SSID,
                        'wifi_iface': IFACE, 'wifi_backend': WIFI_BACKEND,
                        'error': '商店版不提供应用内 Wi-Fi 管理，请在系统设置中连接相机'}), 403
    refresh_wifi_backend(start_wpa=True)
    if not wifi.can_control():
        return jsonify({'nets': [], 'current': '', 'camera_ssid': CAM_SSID,
                        'wifi_iface': IFACE, 'wifi_backend': WIFI_BACKEND,
                        'error': '当前为手动连接模式'}), 503
    if not IFACE:
        return jsonify({'nets': [], 'current': '', 'camera_ssid': CAM_SSID,
                        'wifi_iface': None, 'wifi_backend': WIFI_BACKEND,
                        'error': '未检测到无线网卡'}), 503
    stdout, _ = _triggered_scan()
    nets = []; seen = set()
    for line in stdout.splitlines():
        parts = line.split(':')
        if len(parts) < 2:
            continue
        ssid = parts[0]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        secure = len(parts) > 2 and parts[2].strip().lower() not in ('', 'no', '--')
        nets.append({'ssid': ssid, 'signal': parts[1] if len(parts) > 1 else '',
                     'secure': 'yes' if secure else 'no',
                     'is_camera': is_camera_ssid(ssid)})
    with lk:
        ST['wifi_current'] = current_ssid()
    return jsonify({'nets': nets, 'current': ST['wifi_current'],
                    'camera_ssid': CAM_SSID, 'wifi_iface': IFACE,
                    'wifi_backend': WIFI_BACKEND})

@app.route('/api/wifi/connect', methods=['POST'])
def wifi_connect():
    if STORE_WIFI_LOCKED:
        return jsonify({'ok': False, 'msg': '商店版不提供应用内 Wi-Fi 管理，请在系统设置中连接相机'}), 403
    refresh_wifi_backend(start_wpa=True)
    if not wifi.can_control():
        return jsonify({'ok': False, 'msg': '当前为手动连接模式'}), 400
    data = request.json or {}
    ssid = data.get('ssid', '').strip(); pw = data.get('password', '')
    remember = data.get('remember', False)
    if not ssid:
        return jsonify({'ok': False, 'msg': '请输入 SSID'}), 400
    with lk:
        ST['wifi_target'] = ssid; ST['wifi_password'] = pw; ST['wifi_conn'] = True
    if remember:
        save_wifi(ssid, pw)
    def bg():
        try:
            if try_connect(ssid, pw) and is_camera_ssid(ssid):
                refresh()
                trigger_auto_sync_check('Luna WiFi 已连接，开始自动同步检查')
        finally:
            with lk:
                ST['wifi_conn'] = False
    threading.Thread(target=bg, daemon=True).start()
    return jsonify({'ok': True})

@app.route('/api/wifi/forget', methods=['POST'])
def wifi_forget():
    if STORE_WIFI_LOCKED:
        return jsonify({'ok': False, 'msg': '商店版不提供应用内 Wi-Fi 管理'}), 403
    try:
        if os.path.exists(WIFI_FILE):
            os.remove(WIFI_FILE)
        with lk:
            ST['wifi_saved'] = False; ST['wifi_target'] = CAM_SSID; ST['wifi_password'] = None
        addlog('已清除记住的WiFi')
    except Exception as e:
        log.warning('forget:' + str(e)[:50])
    return jsonify({'ok': True})

@app.route('/api/files')
def api_files():
    ok = refresh()
    with lk:
        files = list(ST['files']) if ok else []
    return jsonify({'connected': ok, 'items': files})

@app.route('/api/local-files')
def api_local_files():
    return jsonify({'items': local_items()})

def load_picks():
    try:
        with open(PICKS_FILE) as picks_file:
            data = json.load(picks_file)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_picks(data):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(PICKS_FILE, 'w') as picks_file:
            json.dump(data, picks_file, ensure_ascii=False)
        os.chmod(PICKS_FILE, 0o600)
    except Exception as e:
        log.warning('save_picks:' + str(e)[:60])

@app.route('/api/picks', methods=['GET'])
def api_picks_get():
    with picks_lk:
        return jsonify({'picks': load_picks()})

@app.route('/api/picks', methods=['POST'])
def api_picks_set():
    data = request.json or {}
    rel = str(data.get('path') or '')
    mark = str(data.get('mark') or '')
    if not rel:
        return jsonify({'ok': False, 'error': 'path_required'}), 400
    if mark not in ('keep', 'reject', ''):
        return jsonify({'ok': False, 'error': 'invalid_mark'}), 400
    with picks_lk:
        picks = load_picks()
        if mark:
            picks[rel] = {'mark': mark, 'ts': int(time.time())}
        else:
            picks.pop(rel, None)
        save_picks(picks)
    return jsonify({'ok': True, 'mark': mark})

@app.route('/api/picks/clear', methods=['POST'])
def api_picks_clear():
    with picks_lk:
        save_picks({})
    return jsonify({'ok': True})

@app.route('/api/picks/export', methods=['POST'])
def api_picks_export():
    """Copy keep-marked files into a subfolder of the download directory."""
    data = request.json or {}
    folder = str(data.get('folder') or '').strip()
    if not folder or len(folder) > 80 or any(ch in folder for ch in '/\\') or folder in ('.', '..'):
        return jsonify({'ok': False, 'error': 'invalid_folder'}), 400
    enhance = bool_value(data.get('enhance'))
    watermark_text = str(data.get('watermark') or '').strip()
    with picks_lk:
        picks = load_picks()
    keepers = [rel for rel, meta in picks.items()
               if isinstance(meta, dict) and meta.get('mark') == 'keep']
    dest_dir = safe_path(DLDIR, folder)
    os.makedirs(dest_dir, exist_ok=True)
    exported = missing = 0
    for rel in sorted(keepers):
        src = local_path(rel)
        if not src:
            missing += 1
            continue
        dst = safe_path(dest_dir, os.path.basename(src))
        if os.path.exists(dst):
            dst = safe_path(dest_dir, rel.replace('/', '_'))
        try:
            if _process_for_export(src, dst, enhance, watermark_text) == 'processed':
                exported += 1
            else:
                exported += 1
        except OSError as e:
            log.warning('picks export ' + rel + ':' + str(e)[:60])
            missing += 1
    addlog('导出入选 ' + str(exported) + ' 个文件到 ' + folder)
    return jsonify({'ok': True, 'exported': exported, 'missing': missing,
                    'folder': folder})

# ---------------- albums / projects / auto-select ----------------

def _load_json_file(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, (dict, list)) else {}
    except Exception:
        return {}

def _save_json_file(path, data):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(path, 0o600)
    except Exception as e:
        log.warning('save ' + os.path.basename(path) + ':' + str(e)[:60])

def load_projects():
    data = _load_json_file(PROJECTS_FILE)
    return data if isinstance(data, dict) else {}

def save_projects(data):
    _save_json_file(PROJECTS_FILE, data)

AUTO = {'running': False, 'done': 0, 'total': 0, 'results': {}, 'recommended': [], 'blurry': []}
EDIT = {'running': False, 'phase': '', 'done': 0, 'total': 0, 'error': '', 'output': ''}
edit_job_lk = threading.Lock()
auto_lk = threading.Lock()

def _dhash(image, size=8):
    small = image.convert('L').resize((size + 1, size), Image.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            if pixels[row * (size + 1) + col] < pixels[row * (size + 1) + col + 1]:
                bits |= 1 << (row * size + col)
    return bits

def _dhash_distance(a, b):
    return bin(a ^ b).count('1')

def _find_blurry(entries):
    """Flag low-quality shots relative to the batch: an absolute score bar
    plus a per-batch adaptive sharpness bar (0.3x the best photo's edge
    energy), so one-off soft shots get caught without false-flagging whole
    albums shot in flat light."""
    if not entries:
        return []
    max_sharp = max(item['sharp'] for item in entries)
    sharp_bar = max(4.0, max_sharp * 0.3)
    return [item['path'] for item in entries
            if item['score'] < 40 or item['sharp'] < sharp_bar]

def _analyze_image(path):
    """Local technical quality score (0-100) plus metrics. Pure PIL."""
    img = Image.open(path)
    img = img.convert('RGB')
    img.thumbnail((640, 640), Image.BILINEAR)
    gray = img.convert('L')
    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_stat = ImageStat.Stat(edges)
    sharp = edge_stat.stddev[0]
    hist = gray.histogram()
    total = sum(hist) or 1
    dark = sum(hist[:8]) / total
    bright = sum(hist[248:]) / total
    mean_l = ImageStat.Stat(gray).mean[0]
    std_l = ImageStat.Stat(gray).stddev[0]
    hsv = img.convert('HSV')
    sat_mean = ImageStat.Stat(hsv.split()[1]).mean[0]
    expo_penalty = max(0.0, dark - 0.06) * 400 + max(0.0, bright - 0.06) * 400
    sharp_s = max(0.0, min(100.0, sharp * 2.2))
    mean_penalty = max(0.0, 90 - mean_l) * 0.6 + max(0.0, mean_l - 195) * 0.6
    expo_s = max(0.0, min(100.0, 100 - expo_penalty - mean_penalty))
    contrast_s = max(0.0, min(100.0, std_l * 1.8))
    sat_s = max(0.0, min(100.0, 100 - max(0.0, 25 - sat_mean) * 1.5 - max(0.0, sat_mean - 190) * 0.4))
    score = round(0.40 * sharp_s + 0.30 * expo_s + 0.20 * contrast_s + 0.10 * sat_s, 1)
    return {'score': score, 'sharp': round(sharp, 1), 'exposure': round(expo_s, 1),
            'contrast': round(contrast_s, 1), 'saturation': round(sat_mean, 1),
            'dhash': _dhash(img)}

def _cluster_hashes(entries, max_distance=6):
    """Greedy burst clustering: near-duplicate hashes share a cluster."""
    clusters = []
    for item in entries:
        placed = False
        for cluster in clusters:
            if _dhash_distance(item['dhash'], cluster['dhash']) <= max_distance:
                item['cluster'] = cluster['id']
                placed = True
                break
        if not placed:
            clusters.append({'id': len(clusters), 'dhash': item['dhash']})
            item['cluster'] = clusters[-1]['id']
    return clusters

def _autoselect_worker(paths):
    try:
        entries = []
        for idx, rel in enumerate(paths):
            src = local_path(rel)
            if not src or not rel.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.insp')):
                continue
            try:
                metrics = _analyze_image(src)
                metrics['path'] = rel
                entries.append(metrics)
            except Exception as e:
                log.warning('autoselect ' + rel + ':' + str(e)[:60])
            with auto_lk:
                AUTO['done'] = idx + 1
        _cluster_hashes(entries)
        best_of_cluster = {}
        for item in entries:
            cid = item['cluster']
            if cid not in best_of_cluster or item['score'] > best_of_cluster[cid]['score']:
                best_of_cluster[cid] = item
        recommended = sorted({item['path'] for item in best_of_cluster.values()
                              if item['score'] >= 60})
        blurry = _find_blurry(entries)
        results = {}
        for item in entries:
            results[item['path']] = {k: item[k] for k in
                                     ('score', 'sharp', 'exposure', 'contrast',
                                      'saturation', 'cluster')}
        with auto_lk:
            AUTO['results'] = dict(sorted(results.items(),
                                          key=lambda kv: -kv[1]['score']))
            AUTO['recommended'] = recommended
            AUTO['running'] = False
        addlog('自动选片完成：分析 ' + str(len(results)) + ' 张，推荐 ' + str(len(recommended)) + ' 张')
    except Exception as e:
        log.warning('autoselect worker:' + str(e)[:80])
        with auto_lk:
            AUTO['running'] = False

@app.route('/api/autoselect/start', methods=['POST'])
def api_autoselect_start():
    data = request.json or {}
    paths = [str(x) for x in (data.get('paths') or []) if str(x)]
    if not paths:
        return jsonify({'ok': False, 'error': 'paths_required'}), 400
    with auto_lk:
        if AUTO['running']:
            return jsonify({'ok': False, 'error': 'already_running'}), 409
        AUTO.update({'running': True, 'done': 0, 'total': len(paths),
                     'results': {}, 'recommended': [], 'blurry': []})
    threading.Thread(target=_autoselect_worker, args=(paths,), daemon=True).start()
    return jsonify({'ok': True, 'total': len(paths)})

@app.route('/api/autoselect/status')
def api_autoselect_status():
    with auto_lk:
        return jsonify({'running': AUTO['running'], 'done': AUTO['done'],
                        'total': AUTO['total'], 'results': AUTO['results'],
                        'recommended': AUTO['recommended'], 'blurry': AUTO['blurry']})

@app.route('/api/autoselect/apply', methods=['POST'])
def api_autoselect_apply():
    data = request.json or {}
    paths = [str(x) for x in (data.get('paths') or []) if str(x)]
    with picks_lk:
        picks = load_picks()
        now = int(time.time())
        for rel in paths:
            picks[rel] = {'mark': 'keep', 'ts': now}
        save_picks(picks)
    addlog('自动选片推荐已应用为入选 ' + str(len(paths)) + ' 个')
    return jsonify({'ok': True, 'applied': len(paths)})

@app.route('/api/projects', methods=['GET'])
def api_projects_get():
    with alb_lk:
        projects = load_projects()
    return jsonify({'projects': projects})

@app.route('/api/projects', methods=['POST'])
def api_projects_create():
    data = request.json or {}
    name = str(data.get('name') or '').strip()
    files = [str(x) for x in (data.get('files') or []) if str(x)]
    if not name or len(name) > 60:
        return jsonify({'ok': False, 'error': 'invalid_name'}), 400
    with alb_lk:
        projects = load_projects()
        pid = 'p%d' % int(time.time() * 1000)
        projects[pid] = {'name': name, 'files': sorted(set(files)),
                         'created': int(time.time())}
        save_projects(projects)
    addlog('新建项目「' + name + '」（' + str(len(files)) + ' 个文件）')
    return jsonify({'ok': True, 'id': pid, 'name': name})

@app.route('/api/projects/delete', methods=['POST'])
def api_projects_delete():
    pid = str((request.json or {}).get('id') or '')
    with alb_lk:
        projects = load_projects()
        removed = projects.pop(pid, None)
        if removed is None:
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        save_projects(projects)
    return jsonify({'ok': True})

@app.route('/api/projects/rename', methods=['POST'])
def api_projects_rename():
    data = request.json or {}
    pid = str(data.get('id') or '')
    name = str(data.get('name') or '').strip()
    if not pid or not name or len(name) > 60:
        return jsonify({'ok': False, 'error': 'invalid_name'}), 400
    with alb_lk:
        projects = load_projects()
        if pid not in projects:
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        projects[pid]['name'] = name
        save_projects(projects)
    return jsonify({'ok': True})

@app.route('/api/projects/add', methods=['POST'])
def api_projects_add():
    data = request.json or {}
    pid = str(data.get('id') or '')
    files = [str(x) for x in (data.get('files') or []) if str(x)]
    if not pid or not files:
        return jsonify({'ok': False, 'error': 'invalid_args'}), 400
    with alb_lk:
        projects = load_projects()
        if pid not in projects:
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        projects[pid]['files'] = sorted(set(projects[pid]['files']) | set(files))
        save_projects(projects)
    return jsonify({'ok': True, 'count': len(projects[pid]['files'])})

@app.route('/api/projects/remove', methods=['POST'])
def api_projects_remove():
    data = request.json or {}
    pid = str(data.get('id') or '')
    files = [str(x) for x in (data.get('files') or []) if str(x)]
    if not pid or not files:
        return jsonify({'ok': False, 'error': 'invalid_args'}), 400
    with alb_lk:
        projects = load_projects()
        if pid not in projects:
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        projects[pid]['files'] = sorted(set(projects[pid]['files']) - set(files))
        save_projects(projects)
    return jsonify({'ok': True, 'count': len(projects[pid]['files'])})

# ---------------- mini export / collage / share / xmp / stats / gps / daily best ----------------

def _auto_levels(img, clip=0.005):
    gray = img.convert('L')
    hist = gray.histogram()
    total = sum(hist) or 1
    lo, acc = 0, 0
    while lo < 255 and acc < total * clip:
        acc += hist[lo]
        lo += 1
    hi, acc = 255, 0
    while hi > 0 and acc < total * clip:
        acc += hist[hi]
        hi -= 1
    if hi - lo < 10:
        return img
    lut = [max(0, min(255, int((i - lo) * 255 / max(1, hi - lo)))) for i in range(256)]
    return img.point(lut * 3)

def _gray_world(img):
    stat = ImageStat.Stat(img)
    means = stat.mean
    avg = sum(means) / 3
    if avg <= 0:
        return img
    gains = [max(0.8, min(1.25, avg / m)) if m > 0 else 1.0 for m in means]
    channels = []
    for ch, gain in zip(img.split(), gains):
        channels.append(ch.point(lambda i, g=gain: min(255, int(i * g))))
    return Image.merge('RGB', channels)

def _enhance(img):
    """One-tap quality fix: white balance, auto levels, mild sharpening."""
    img = _gray_world(img)
    img = _auto_levels(img)
    return img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))

def _watermark_font(size):
    for cand in (
        '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
        '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Light.ttc',
        'C:/Windows/Fonts/msyh.ttc',
    ):
        if os.path.exists(cand):
            try:
                return ImageFont.truetype(cand, size)
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()

def _watermark(img, text):
    font = _watermark_font(max(18, img.width // 36))
    draw = ImageDraw.Draw(img)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    margin = max(12, img.width // 40)
    pos = (img.width - tw - margin, img.height - th - margin - bbox[1])
    draw.text(pos, text, fill=(255, 255, 255), font=font,
              stroke_width=max(1, img.width // 800), stroke_fill=(0, 0, 0))
    return img

def _process_for_export(src, dst, enhance, watermark_text):
    if enhance or watermark_text:
        img = Image.open(src)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        if enhance:
            img = _enhance(img)
        if watermark_text:
            img = _watermark(img, watermark_text)
        img.save(dst, 'JPEG', quality=92)
        return 'processed'
    shutil.copy2(src, dst)
    return 'copied'

def _keep_paths():
    with picks_lk:
        picks = load_picks()
    return sorted(rel for rel, meta in picks.items()
                  if isinstance(meta, dict) and meta.get('mark') == 'keep')

def _mini_image(src, dst, long_edge, quality, enhance=False, watermark_text=''):
    img = Image.open(src)
    if img.mode != 'RGB':
        img = img.convert('RGB')
    w, h = img.size
    scale = long_edge / max(w, h)
    if scale < 1:
        img = img.resize((round(w * scale), round(h * scale)), Image.LANCZOS)
    if enhance:
        img = _enhance(img)
    if watermark_text:
        img = _watermark(img, watermark_text)
    img.save(dst, 'JPEG', quality=quality)

@app.route('/api/export/mini', methods=['POST'])
def api_export_mini():
    """Export keep-marked photos in a WeChat-friendly size, zipped."""
    data = request.json or {}
    folder = str(data.get('folder') or '精选-朋友圈').strip()
    long_edge = int(data.get('size') or 2048)
    quality = int(data.get('quality') or 85)
    if not folder or len(folder) > 60 or any(ch in folder for ch in '/\\') or folder in ('.', '..'):
        return jsonify({'ok': False, 'error': 'invalid_folder'}), 400
    long_edge = max(480, min(4096, long_edge))
    quality = max(50, min(95, quality))
    enhance = bool_value(data.get('enhance'))
    watermark_text = str(data.get('watermark') or '').strip()
    keepers = _keep_paths()
    dest_dir = safe_path(DLDIR, folder)
    os.makedirs(dest_dir, exist_ok=True)
    exported = missing = 0
    for rel in keepers:
        src = local_path(rel)
        if not src or not rel.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.insp')):
            missing += 1
            continue
        dst = safe_path(dest_dir, os.path.basename(src))
        if os.path.exists(dst):
            dst = safe_path(dest_dir, rel.replace('/', '_'))
        try:
            _mini_image(src, dst, long_edge, quality, enhance, watermark_text)
            exported += 1
        except OSError as e:
            log.warning('mini export ' + rel + ':' + str(e)[:60])
            missing += 1
    zip_path = ''
    if exported and data.get('zip', True):
        zip_base = safe_path(DLDIR, folder)
        zip_path = shutil.make_archive(zip_base, 'zip', dest_dir)
        zip_path = os.path.basename(zip_path)
    addlog('朋友圈导出 ' + str(exported) + ' 张（长边 ' + str(long_edge) + 'px）')
    return jsonify({'ok': True, 'exported': exported, 'missing': missing,
                    'folder': folder, 'zip': zip_path})

@app.route('/api/picks/xmp', methods=['POST'])
def api_picks_xmp():
    """Write photography-standard XMP sidecars for keep/reject marks."""
    data = request.json or {}
    folder = str(data.get('folder') or 'xmp').strip()
    if not folder or len(folder) > 60 or any(ch in folder for ch in '/\\') or folder in ('.', '..'):
        return jsonify({'ok': False, 'error': 'invalid_folder'}), 400
    with picks_lk:
        picks = load_picks()
    dest_dir = safe_path(DLDIR, folder)
    os.makedirs(dest_dir, exist_ok=True)
    written = 0
    for rel, meta in sorted(picks.items()):
        if not isinstance(meta, dict) or meta.get('mark') not in ('keep', 'reject'):
            continue
        src = local_path(rel)
        if not src:
            continue
        rating = 5 if meta['mark'] == 'keep' else 1
        label = 'Keep' if meta['mark'] == 'keep' else 'Reject'
        base = os.path.basename(src)
        xmp = ('<?xml version="1.0" encoding="UTF-8"?>\n'
               '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
               ' <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
               '  <rdf:Description rdf:about=""\n'
               '    xmlns:xmp="http://ns.adobe.com/xap/1.0/"\n'
               '    xmlns:photoshop="http://ns.adobe.com/photoshop/1.0/"\n'
               '    xmp:Rating="%d"\n'
               '    xmp:Label="%s"\n'
               '    photoshop:Headline="Luna Sync pick"/>\n'
               ' </rdf:RDF>\n'
               '</x:xmpmeta>\n') % (rating, label)
        dst = safe_path(dest_dir, base + '.xmp')
        with open(dst, 'w') as xmp_file:
            xmp_file.write(xmp)
        written += 1
    addlog('导出 XMP 标记 ' + str(written) + ' 个')
    return jsonify({'ok': True, 'written': written, 'folder': folder})

@app.route('/api/collage', methods=['POST'])
def api_collage():
    """Compose kept photos into a grid or strip image, fully local."""
    data = request.json or {}
    layout = str(data.get('layout') or '3x3')
    cols = 3 if layout == '3x3' else 2
    per_page = {'2x2': 4, '3x3': 9, 'strip': 6}.get(layout)
    if per_page is None:
        return jsonify({'ok': False, 'error': 'invalid_layout'}), 400
    paths = [str(x) for x in (data.get('paths') or []) if str(x)][:per_page]
    if len(paths) < 2:
        return jsonify({'ok': False, 'error': 'need_two'}), 400
    cell = 1200
    if layout == 'strip':
        cell_h = 900
        canvas = Image.new('RGB', (cell * len(paths), cell_h), (24, 30, 38))
    else:
        rows = -(-len(paths) // cols)
        canvas = Image.new('RGB', (cell * cols, cell * rows), (24, 30, 38))
    used = 0
    for idx, rel in enumerate(paths):
        src = local_path(rel)
        if not src:
            continue
        try:
            img = Image.open(src).convert('RGB')
        except Exception:
            continue
        cw = cell if layout != 'strip' else cell
        ch = cell if layout != 'strip' else cell_h
        ratio = max(cw / img.width, ch / img.height)
        img = img.resize((max(1, round(img.width * ratio)), max(1, round(img.height * ratio))),
                         Image.LANCZOS)
        left = (img.width - cw) // 2
        top = (img.height - ch) // 2
        canvas.paste(img.crop((left, top, left + cw, top + ch)),
                     ((idx % cols) * cell, (idx // cols) * (ch if layout != 'strip' else 0) if layout != 'strip' else 0))
        used += 1
    if used < 2:
        return jsonify({'ok': False, 'error': 'no_images'}), 400
    out_dir = safe_path(DLDIR, '拼图')
    os.makedirs(out_dir, exist_ok=True)
    out_name = '拼图-%s.jpg' % time.strftime('%Y%m%d-%H%M%S')
    out_path = safe_path(out_dir, out_name)
    canvas.save(out_path, 'JPEG', quality=90)
    addlog('生成拼图 ' + out_name + '（' + str(used) + ' 张，' + layout + '）')
    return jsonify({'ok': True, 'file': '拼图/' + out_name, 'used': used,
                    'layout': layout, 'width': canvas.width, 'height': canvas.height})

def _load_shares():
    data = _load_json_file(SHARES_FILE)
    return data if isinstance(data, dict) else {}

def _save_shares(data):
    _save_json_file(SHARES_FILE, data)

def _prune_shares():
    shares = _load_shares()
    now = time.time()
    changed = False
    for token in list(shares):
        expires = shares[token].get('expires', 0)
        if expires and expires < now:
            del shares[token]
            changed = True
    if changed:
        _save_shares(shares)
    return shares

@app.route('/api/share/create', methods=['POST'])
def api_share_create():
    data = request.json or {}
    paths = [str(x) for x in (data.get('paths') or []) if str(x)]
    paths = [x for x in paths if local_path(x)]
    if len(paths) < 1:
        return jsonify({'ok': False, 'error': 'paths_required'}), 400
    passcode = str(data.get('passcode') or '').strip()
    days = int(data.get('days') or 7)
    token = secrets.token_urlsafe(12)
    share = {'paths': paths,
             'created': int(time.time()),
             'expires': (int(time.time()) + days * 86400) if days > 0 else 0,
             'pass': hashlib.sha256(passcode.encode()).hexdigest() if passcode else ''}
    with alb_lk:
        shares = _prune_shares()
        shares[token] = share
        _save_shares(shares)
    addlog('创建精选分享页（' + str(len(paths)) + ' 张，口令 ' + ('开' if passcode else '关') + '）')
    return jsonify({'ok': True, 'token': token, 'count': len(paths)})

@app.route('/api/share/list')
def api_share_list():
    with alb_lk:
        shares = _prune_shares()
    return jsonify({'shares': [{'token': t, 'count': len(v.get('paths', [])),
                                'created': v.get('created', 0),
                                'expires': v.get('expires', 0),
                                'locked': bool(v.get('pass'))}
                               for t, v in shares.items()]})

@app.route('/api/share/revoke', methods=['POST'])
def api_share_revoke():
    token = str((request.json or {}).get('token') or '')
    with alb_lk:
        shares = _load_shares()
        if token not in shares:
            return jsonify({'ok': False, 'error': 'not_found'}), 404
        del shares[token]
        _save_shares(shares)
    return jsonify({'ok': True})

def _share_check(token, code):
    shares = _prune_shares()
    share = shares.get(token)
    if not share:
        return None, False
    need = share.get('pass') or ''
    if need and hashlib.sha256(str(code or '').encode()).hexdigest() != need:
        return share, False
    return share, True

@app.route('/share/<token>')
def share_page(token, code=''):
    share, ok = _share_check(token, request.args.get('code', ''))
    if not share:
        abort(404)
    if not ok:
        return render_template('share.html', token=token, locked=True,
                               count=0, items=[], code='')
    items = [{'idx': i, 'name': os.path.basename(p)}
             for i, p in enumerate(share.get('paths', []))]
    return render_template('share.html', token=token, locked=False,
                           count=len(items), items=items,
                           code=request.args.get('code', ''))

@app.route('/share/<token>/img/<int:idx>')
def share_img(token, idx):
    share, ok = _share_check(token, request.args.get('code', ''))
    if not share or not ok:
        abort(403 if share else 404)
    paths = share.get('paths', [])
    if idx < 0 or idx >= len(paths):
        abort(404)
    p = local_path(paths[idx])
    if not p:
        abort(404)
    return send_file(p, mimetype=mimetypes.guess_type(p)[0] or 'application/octet-stream')

@app.route('/api/storage/stats')
def api_storage_stats():
    months = {}
    total = 0
    for root, dirs, files in os.walk(DLDIR):
        dirs[:] = [d for d in dirs if d != '.trash']
        for f in files:
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
                mtime = os.path.getmtime(fp)
            except OSError:
                continue
            total += size
            month = time.strftime('%Y-%m', time.localtime(mtime))
            entry = months.setdefault(month, {'bytes': 0, 'count': 0})
            entry['bytes'] += size
            entry['count'] += 1
    def dir_size(path):
        size = 0
        for r, _, fs in os.walk(path):
            for f in fs:
                try:
                    size += os.path.getsize(os.path.join(r, f))
                except OSError:
                    pass
        return size
    caches = {name: dir_size(path) for name, path in (
        ('thumb', THUMB_DIR), ('encoded', ENC_DIR),
        ('liv', LIV_DIR), ('preview', PREVIEW_SRC_DIR))}
    caches['trash'] = dir_size(TRASH_DIR)
    months_list = [{'month': k, 'bytes': v['bytes'], 'count': v['count']}
                   for k, v in sorted(months.items(), reverse=True)]
    return jsonify({'total': total, 'months': months_list, 'caches': caches})

def _read_gps(path):
    """Best-effort GPS extraction; returns {} when unavailable."""
    try:
        img = Image.open(path)
        raw = getattr(img, '_getexif', lambda: None)() or {}
        gps = raw.get_ifd(34853) if hasattr(raw, 'get_ifd') else raw.get(34853)
        if not gps:
            return {}
        def to_deg(val, ref):
            if not isinstance(val, tuple) or len(val) != 3:
                return None
            deg = val[0] / max(1, val[1]) + val[1] / 60 + val[2] / 3600
            return -deg if ref in ('S', 'W') else deg
        lat = to_deg(gps.get(2), str(gps.get(1, 'N')))
        lon = to_deg(gps.get(4), str(gps.get(3, 'E')))
        if lat is None or lon is None:
            return {}
        return {'lat': round(lat, 6), 'lon': round(lon, 6)}
    except Exception:
        return {}

@app.route('/api/gps/scan', methods=['POST'])
def api_gps_scan():
    paths = [str(x) for x in (request.json or {}).get('paths') or []]
    out = {}
    for rel in paths:
        src = local_path(rel)
        if not src:
            continue
        gps = _read_gps(src)
        if gps:
            out[rel] = gps
    return jsonify({'gps': out})

@app.route('/api/settings/daily-best', methods=['GET'])
def api_daily_best_get():
    return jsonify(_daily_settings())

@app.route('/api/settings/daily-best', methods=['POST'])
def api_daily_best_set():
    data = request.json or {}
    save_settings({'daily_best': bool_value(data.get('enabled')),
                   'daily_best_time': str(data.get('time') or '03:00')})
    addlog('每日精选 ' + ('开启，时间 ' + str(data.get('time')) if bool_value(data.get('enabled')) else '关闭'))
    return jsonify({'ok': True, **_daily_settings()})

@app.route('/api/camera/status')
def api_camera_status():
    reachable = cam_on()
    return jsonify({'reachable': reachable, 'storage': None,
                    'supported': False})

DAILY_FILE = os.path.join(STATE_DIR, 'dailybest.json')
DAILY_WORKER_STARTED = False

def _daily_settings():
    settings = load_settings()
    return {'enabled': bool_value(settings.get('daily_best'), False),
            'time': str(settings.get('daily_best_time') or '03:00')}

def _daily_best_run():
    date_key = time.strftime('%Y-%m-%d')
    paths = [rel for rel in local_files()
             if rel.lower().endswith(('.jpg', '.jpeg', '.png', '.webp', '.insp'))]
    if not paths:
        return
    global AUTO
    with auto_lk:
        if AUTO['running']:
            return
        AUTO.update({'running': True, 'done': 0, 'total': len(paths),
                     'results': {}, 'recommended': [], 'blurry': []})
    _autoselect_worker(paths)
    with auto_lk:
        recommended = list(AUTO['recommended'])
    if not recommended:
        addlog('每日精选：今日无推荐')
        return
    dest_dir = safe_path(DLDIR, os.path.join('今日精选', date_key))
    os.makedirs(dest_dir, exist_ok=True)
    copied = 0
    for rel in recommended:
        src = local_path(rel)
        if not src:
            continue
        try:
            shutil.copy2(src, safe_path(dest_dir, os.path.basename(src)))
            copied += 1
        except OSError:
            pass
    addlog('每日精选 ' + date_key + '：已复制 ' + str(copied) + ' 张推荐到 今日精选/' + date_key)

def _daily_best_worker():
    while True:
        try:
            conf = _daily_settings()
            if conf['enabled'] and privacy_accepted():
                last = ''
                data = _load_json_file(DAILY_FILE)
                if isinstance(data, dict):
                    last = str(data.get('last', ''))
                today = time.strftime('%Y-%m-%d')
                now = time.strftime('%H:%M')
                if last != today and now >= conf['time']:
                    if wifi_on_target() and cam_on():
                        refresh()
                    _daily_best_run()
                    _save_json_file(DAILY_FILE, {'last': today})
        except Exception as e:
            log.warning('daily best:' + str(e)[:60])
        time.sleep(60)

# ---------------- auto-cut (auto edit montage) ----------------

CUT_TMP_DIR = os.path.join(STATE_DIR, 'autocut_tmp')
MUSIC_DIR_NAME = '音乐'
CUT_FADE = 0.5

def _video_core(name):
    """Strip camera prefix and extension: VID_20260703_112717_233.mp4 -> 20260703_112717_233"""
    base = os.path.basename(name)
    core = base.rsplit('.', 1)[0]
    if core[:4].upper() in ('VID_', 'IMG_', 'LRV_'):
        core = core[4:]
    return core

def _find_lrv(rel):
    core = _video_core(rel)
    if not core:
        return None
    for cand in local_files():
        if cand.lower().endswith('.lrv') and _video_core(cand) == core:
            return cand
    return None

def _motion_profile(src, tmp_dir, tag):
    """Per-second motion scores + scene-cut seconds for one video, via 1fps
    downscaled frames (fast even for 4K sources)."""
    frames_dir = os.path.join(tmp_dir, 'f' + tag)
    os.makedirs(frames_dir, exist_ok=True)
    run(['ffmpeg', '-y', '-i', src, '-vf', 'fps=1,scale=256:-2',
         '-q:v', '6', os.path.join(frames_dir, 'f_%04d.jpg')], 600)
    frames = sorted(f for f in os.listdir(frames_dir) if f.endswith('.jpg'))
    scores, cuts = [], []
    prev = None
    for f in frames:
        img = Image.open(os.path.join(frames_dir, f)).convert('L')
        if prev is not None:
            diff = ImageStat.Stat(ImageChops.difference(img, prev)).mean[0]
            scores.append(diff)
            if diff > 42:
                cuts.append(len(scores))
        prev = img
    # a scene cut's diff is the shot change itself, not motion: neutralize it
    for c in cuts:
        if 0 < c - 1 < len(scores):
            scores[c - 1] = 0.0
    shutil.rmtree(frames_dir, ignore_errors=True)
    return scores, cuts

def _pick_highlights(scores, cuts, window=4, max_segments=2):
    n = len(scores)
    if n == 0:
        return []
    if n <= window + 1:
        return [{'start': 0, 'dur': max(2.0, float(n))}]
    smooth = []
    for i in range(n):
        seg = scores[max(0, i - 1):i + 2]
        smooth.append(sum(seg) / len(seg))
    windows = []
    for i in range(0, n - window + 1):
        seg_score = sum(smooth[i:i + window]) / window
        if any(i + 1 <= c <= i + window - 1 for c in cuts):
            continue
        windows.append((seg_score, i))
    windows.sort(reverse=True)
    best = windows[0][0] if windows else 0
    floor = best * 0.25
    picked = []
    for seg_score, i in windows:
        if seg_score < floor:
            break
        if all(i + window <= st or i >= st + du for st, du in picked):
            picked.append((float(i), float(window)))
            if len(picked) >= max_segments:
                break
    picked.sort()
    return [{'start': st, 'dur': min(du, float(n - st))} for st, du in picked]

def _video_highlight_segments(rel, tmp_dir, tag):
    """Analyze one video (LRV proxy preferred) -> highlight segments."""
    src = local_path(rel)
    if not src:
        return []
    lrv_rel = _find_lrv(rel)
    analysis_src = (local_path(lrv_rel) if lrv_rel else None) or src
    scores, cuts = _motion_profile(analysis_src, tmp_dir, str(abs(hash(rel)) % 100000))
    return [{'src': rel, 'start': seg['start'], 'dur': seg['dur']}
            for seg in _pick_highlights(scores, cuts)]

def _plan_montage(video_segs, photo_rels, target):
    """Interleave video highlights and photos chronologically, trim to target."""
    photos = list(photo_rels)[:6]
    plan = []
    used_photos = 0
    remaining = float(target)
    for seg in sorted(video_segs, key=lambda x: (x['start'], x['src'])):
        dur = min(seg['dur'], 5.0, max(2.0, remaining))
        if dur < 2.0:
            break
        plan.append({'kind': 'video', 'src': seg['src'],
                     'start': seg['start'], 'dur': dur})
        remaining -= dur
        if used_photos < len(photos) and remaining > 3.0:
            plan.append({'kind': 'photo', 'src': photos[used_photos], 'dur': 3.0})
            used_photos += 1
            remaining -= 3.0
        if remaining <= 2.0:
            break
    while used_photos < len(photos) and sum(p['dur'] for p in plan) + 3.0 <= target + 1.0:
        plan.append({'kind': 'photo', 'src': photos[used_photos], 'dur': 3.0})
        used_photos += 1
    return plan

def _render_montage(plan, music_rel, out_path, progress_cb=None):
    """Assemble the montage with one ffmpeg filter graph (xfade + acrossfade)."""
    fade = CUT_FADE if len(plan) > 1 else 0
    cmd = ['ffmpeg', '-y']
    inputs = []
    for idx, p in enumerate(plan):
        if p['kind'] == 'video':
            src = local_path(p['src'])
            cmd += ['-ss', '%.2f' % p['start'], '-t', '%.2f' % p['dur'], '-i', src]
        else:
            cmd += ['-loop', '1', '-t', '%.2f' % p['dur'], '-i', local_path(p['src'])]
        inputs.append(p)
    if music_rel:
        music_src = safe_path(DLDIR, music_rel)
        if os.path.isfile(music_src):
            cmd += ['-i', music_src]
    parts = []
    for i, p in enumerate(plan):
        if p['kind'] == 'video':
            parts.append('[%d:v]scale=1920:1080:force_original_aspect_ratio=increase,'
                         'crop=1920:1080,fps=30,format=yuv420p,setsar=1[v%d];'
                         '[%d:a]aresample=44100,aformat=channel_layouts=stereo[a%d]'
                         % (i, i, i, i))
        else:
            dur = p['dur']
            parts.append("[%d:v]scale=1920:1080:force_original_aspect_ratio=increase,"
                         "crop=1920:1080,fps=30,"
                         "zoompan=z='min(zoom+0.0009,1.12)':d=1:"
                         "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1920x1080,"
                         "format=yuv420p,setsar=1[v%d];"
                         "aevalsrc=0:c=stereo:s=44100:d=%.2f[a%d]"
                         % (i, i, dur, i))
    graph = ';'.join(parts) + ';'
    offsets = []
    acc = 0.0
    for p in plan[:-1]:
        acc += p['dur'] - fade
        offsets.append(acc)
    vprev, aprev = 'v0', 'a0'
    for i in range(1, len(plan)):
        vout = 'vx%d' % i
        aout = 'ax%d' % i
        graph += '[%s][%s]xfade=transition=fade:duration=%.2f:offset=%.2f[%s];' % (
            vprev, 'v%d' % i, fade, offsets[i - 1], vout)
        graph += '[%s][%s]acrossfade=d=%.2f[%s];' % (aprev, 'a%d' % i, fade, aout)
        vprev, aprev = vout, aout
    graph += '[%s]format=yuv420p[vout]' % vprev
    cmd += ['-filter_complex', graph, '-map', '[vout]']
    if music_rel:
        total = sum(p['dur'] for p in plan) - fade * (len(plan) - 1)
        cmd += ['-map', '%d:a' % len(plan), '-t', '%.2f' % total]
    else:
        cmd += ['-map', aprev]
    cmd += ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23',
            '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart',
            '-shortest', out_path]
    result = run(cmd, 3600)
    if result.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError('ffmpeg montage failed: ' + (result.stderr or '')[-300:])

def _autocut_worker(video_rels, photo_rels, target, music_rel):
    tmp_dir = CUT_TMP_DIR
    try:
        os.makedirs(tmp_dir, exist_ok=True)
        with edit_job_lk:
            EDIT.update({'running': True, 'phase': 'analyze', 'done': 0,
                         'total': len(video_rels), 'error': '', 'output': ''})
        segs = []
        for idx, rel in enumerate(video_rels):
            try:
                segs.extend(_video_highlight_segments(rel, tmp_dir, str(idx)))
            except Exception as e:
                log.warning('autocut analyze ' + rel + ':' + str(e)[:80])
            with edit_job_lk:
                EDIT['done'] = idx + 1
        if not segs:
            with edit_job_lk:
                EDIT['running'] = False
                EDIT['error'] = 'no_highlights'
            return
        plan = _plan_montage(segs, photo_rels, target)
        if len(plan) < 1:
            with edit_job_lk:
                EDIT['running'] = False
                EDIT['error'] = 'plan_empty'
            return
        with edit_job_lk:
            EDIT['phase'] = 'build'
        out_dir = safe_path(DLDIR, '自动剪辑')
        os.makedirs(out_dir, exist_ok=True)
        out_path = safe_path(out_dir, '剪辑-%s.mp4' % time.strftime('%Y%m%d-%H%M%S'))
        _render_montage(plan, music_rel, out_path)
        with edit_job_lk:
            EDIT['running'] = False
            EDIT['output'] = '自动剪辑/' + os.path.basename(out_path)
        addlog('自动剪辑完成：' + os.path.basename(out_path) + '（' + str(len(plan)) + ' 段）')
    except Exception as e:
        log.warning('autocut:' + str(e)[:120])
        with edit_job_lk:
            EDIT['running'] = False
            EDIT['error'] = str(e)[:200]
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

@app.route('/api/music/list')
def api_music_list():
    music_dir = safe_path(DLDIR, MUSIC_DIR_NAME)
    items = []
    if os.path.isdir(music_dir):
        for f in sorted(os.listdir(music_dir)):
            if f.lower().endswith(('.mp3', '.m4a', '.aac', '.wav')):
                items.append(f)
    return jsonify({'music': items})

@app.route('/api/autocut/start', methods=['POST'])
def api_autocut_start():
    data = request.json or {}
    videos = [str(x) for x in (data.get('videos') or []) if str(x)]
    photos = [str(x) for x in (data.get('photos') or []) if str(x)]
    target = int(data.get('duration') or 30)
    target = max(15, min(60, target))
    music = str(data.get('music') or '')
    if music:
        music = MUSIC_DIR_NAME + '/' + music
    if not videos:
        return jsonify({'ok': False, 'error': 'no_videos'}), 400
    with edit_job_lk:
        if EDIT['running']:
            return jsonify({'ok': False, 'error': 'already_running'}), 409
    threading.Thread(target=_autocut_worker,
                     args=(videos, photos, target, music), daemon=True).start()
    return jsonify({'ok': True, 'videos': len(videos), 'target': target})

@app.route('/api/autocut/status')
def api_autocut_status():
    with edit_job_lk:
        return jsonify(EDIT)

@app.route('/api/download', methods=['POST'])
def api_dl():
    ns = (request.json or {}).get('files', [])
    added, skipped = enqueue(ns)
    addlog('队列 +' + str(added))
    msg = ''
    if not added:
        reasons = {s['reason'] for s in skipped}
        with lk:
            connected = ST['connected']
        if 'already_local' in reasons and len(reasons) == 1:
            msg = '所选文件已在本地'
        elif not connected:
            msg = '相机未连接，无法下载新素材'
        elif 'not_available' in reasons:
            msg = '所选文件不在当前相机列表'
        elif 'already_queued' in reasons:
            msg = '所选文件已在队列中'
    return jsonify({'queued': added, 'skipped': skipped, 'msg': msg})

@app.route('/api/cancel', methods=['POST'])
def api_can():
    with lk:
        current = ST['current']
        active = bool(ST['active_key'] or current)
        removed = len(ST['queue'])
        ST['queue'] = []
        auto_downloads.clear()
        if active:
            cancel.set()
        auto_sync = ST['auto_sync']
    if active or removed:
        detail = []
        if active:
            detail.append('正在停止当前下载')
        if removed:
            detail.append('移除队列 ' + str(removed) + ' 个')
        addlog('取消下载: ' + '，'.join(detail))
    return jsonify({'ok': True, 'cancelled': active, 'removed': removed,
                    'auto_sync': auto_sync})

def _purge_trash_item(unique):
    base = safe_path(TRASH_DIR, unique)
    if not os.path.isdir(base):
        return False
    shutil.rmtree(base, ignore_errors=True)
    return True

def _trash_purge_expired():
    try:
        cutoff = time.time() - TRASH_KEEP_DAYS * 86400
        for unique in os.listdir(TRASH_DIR):
            base = safe_path(TRASH_DIR, unique)
            stamp = os.path.join(base, '.trashed-at')
            if os.path.exists(stamp) and os.path.getmtime(stamp) < cutoff:
                shutil.rmtree(base, ignore_errors=True)
                addlog('回收站清理 ' + unique[:40])
    except Exception as e:
        log.warning('trash purge:' + str(e)[:60])

def _drop_side_files(name):
    for extra in (
        safe_path(ENC_DIR, name + '.mp4'),
        safe_path(THUMB_DIR, name + '.jpg'),
        safe_path(THUMB_DIR, name + '.preview.jpg'),
        safe_path(PREVIEW_SRC_DIR, name),
    ):
        if os.path.exists(extra):
            os.remove(extra)
        if os.path.exists(extra + '.part'):
            os.remove(extra + '.part')
    liv_cache = safe_path(LIV_DIR, name + '.d')
    if os.path.isdir(liv_cache):
        shutil.rmtree(liv_cache, ignore_errors=True)
    with picks_lk:
        picks = load_picks()
        if picks.pop(name, None) is not None:
            save_picks(picks)

@app.route('/api/file/<path:name>', methods=['DELETE'])
def api_del(name):
    p = local_path(name) or safe_path(DLDIR, name)
    # move the original into the trash folder (restorable for N days)
    if os.path.exists(p):
        unique = '%d_%s' % (int(time.time() * 1000), re.sub(r'[^A-Za-z0-9._-]', '_', name))
        base = safe_path(TRASH_DIR, unique)
        os.makedirs(base, exist_ok=True)
        try:
            shutil.move(p, os.path.join(base, os.path.basename(p)))
            with open(os.path.join(base, '.trashed-at'), 'w') as stamp:
                stamp.write(name)
        except OSError:
            pass
    if os.path.exists(p + '.part'):
        os.remove(p + '.part')
    _drop_side_files(name)
    addlog('已移入回收站 ' + name)
    return jsonify({'ok': True, 'trashed': True})

@app.route('/api/trash', methods=['GET'])
def api_trash_list():
    _trash_purge_expired()
    items = []
    for unique in sorted(os.listdir(TRASH_DIR), reverse=True):
        base = safe_path(TRASH_DIR, unique)
        if not os.path.isdir(base):
            continue
        try:
            with open(os.path.join(base, '.trashed-at')) as stamp:
                orig_name = stamp.read().strip()
        except Exception:
            orig_name = unique
        files = [f for f in os.listdir(base) if not f.startswith('.')]
        size = sum(os.path.getsize(os.path.join(base, f)) for f in files)
        items.append({'unique': unique, 'name': orig_name,
                      'when': int(os.path.getmtime(base)) * 1000, 'size': size})
    return jsonify({'items': items, 'keepDays': TRASH_KEEP_DAYS})

@app.route('/api/trash/restore', methods=['POST'])
def api_trash_restore():
    unique = str((request.json or {}).get('unique') or '')
    base = safe_path(TRASH_DIR, unique)
    if not os.path.isdir(base):
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    try:
        with open(os.path.join(base, '.trashed-at')) as stamp:
            orig = stamp.read().strip()
    except Exception:
        orig = ''
    restored = []
    for f in os.listdir(base):
        if f.startswith('.'):
            continue
        src = os.path.join(base, f)
        # .trashed-at records the original download-relative path, so the
        # file goes back exactly where it was deleted from
        rel = orig or f
        dst = safe_path(DLDIR, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        restored.append(rel)
    shutil.rmtree(base, ignore_errors=True)
    addlog('从回收站恢复 ' + str(len(restored)) + ' 个文件')
    return jsonify({'ok': True, 'restored': restored})

@app.route('/api/trash/purge', methods=['POST'])
def api_trash_purge():
    unique = str((request.json or {}).get('unique') or '')
    if unique == 'all':
        count = 0
        for u in os.listdir(TRASH_DIR):
            if _purge_trash_item(u):
                count += 1
        return jsonify({'ok': True, 'purged': count})
    if not _purge_trash_item(unique):
        return jsonify({'ok': False, 'error': 'not_found'}), 404
    return jsonify({'ok': True, 'purged': 1})

def _read_exif(path):
    """Best-effort EXIF summary via PIL; returns {} when unavailable."""
    try:
        img = Image.open(path)
        raw = getattr(img, '_getexif', lambda: None)()
        if not raw:
            return {}
        tag_map = {271: 'make', 272: 'model', 42036: 'lens',
                   33437: 'fnumber', 34855: 'iso', 37386: 'focal',
                   33434: 'exposure', 36867: 'taken'}
        out = {}
        for tag, key in tag_map.items():
            if tag not in raw:
                continue
            val = raw[tag]
            if key == 'fnumber' and isinstance(val, tuple):
                val = round(val[0] / max(1, val[1]), 1)
            elif key == 'focal' and isinstance(val, tuple):
                val = round(val[0] / max(1, val[1]))
            elif key == 'exposure' and isinstance(val, tuple) and val[1]:
                expo = val[0] / val[1]
                val = ('1/%d' % round(1 / expo)) if expo and expo < 1 else round(expo, 2)
            out[key] = val
        return out
    except Exception:
        return {}

@app.route('/api/exif/<path:name>')
def api_exif(name):
    p = local_path(name)
    if not p:
        item = file_info(name)
        if not item:
            abort(404)
        return jsonify({})
    return jsonify(_read_exif(p))

def _wipe_dir(d):
    n = t = 0
    if not os.path.isdir(d):
        return (0, 0)
    for root, dirs, files in os.walk(d, topdown=False):
        for fn in files:
            fp = os.path.join(root, fn)
            try:
                t += os.path.getsize(fp)
                os.remove(fp)
                n += 1
            except Exception as e:
                log.warning('wipe ' + fn + ':' + str(e)[:60])
        for dn in dirs:
            try:
                os.rmdir(os.path.join(root, dn))
            except OSError:
                pass
    return (n, t)

@app.route('/api/cache/clear', methods=['POST'])
def api_cache_clear():
    data = request.json or {}
    scope = (data.get('scope') or 'all').lower()
    files = space = 0
    if scope in ('all', 'thumb'):
        n, t = _wipe_dir(THUMB_DIR); files += n; space += t
    if scope in ('all', 'encoded'):
        n, t = _wipe_dir(ENC_DIR); files += n; space += t
        with lk:
            ST['transcodes'] = {}
    if scope in ('all', 'preview'):
        n, t = _wipe_dir(PREVIEW_SRC_DIR); files += n; space += t
    if scope in ('all', 'liv'):
        n, t = _wipe_dir(LIV_DIR); files += n; space += t
    msg = '清理缓存 ' + str(files) + ' 个文件 / ' + _human(space)
    addlog(msg)
    return jsonify({'ok': True, 'files': files, 'bytes': space, 'msg': msg})

def _human(b):
    for u in ('B', 'KB', 'MB', 'GB'):
        if b < 1024:
            return ('%.0f' % b if u == 'B' else '%.1f' % b) + ' ' + u
        b /= 1024
    return '%.1f TB' % b


_generating = set()
_gen_lk = threading.Lock()

def extract_thumb(src, output, stream=False):
    """Grab a frame for the thumbnail; retry without the seek on failure."""
    input_opts = ['-threads', '1', '-user_agent', 'LunaDL/0.1'] if stream else ['-threads', '1']
    for lead in (['-ss', '1'], []):
        cmd = ['ffmpeg', '-y'] + lead + input_opts + [
            '-i', src, '-frames:v', '1', '-vf', 'scale=320:-2', '-q:v', '4', output]
        result = run(cmd, 90 if stream else 30)
        if result.returncode == 0 and os.path.exists(output) and os.path.getsize(output) > 0:
            return
        if os.path.exists(output):
            try:
                os.remove(output)
            except OSError:
                pass


def is_live_name(name):
    base = os.path.basename(name).upper()
    return base.startswith('LIV_') or name.lower().endswith('.liv')


def _find_motion_split(path):
    """Return the byte offset where an appended MP4 starts, or -1.

    Insta360 live-photo stills are plain JPEGs with the MP4 clip appended
    right after the JPEG EOI marker; the clip begins with a 4-byte box
    length followed by 'ftyp'.
    """
    chunk = 4 * 1024 * 1024
    prev = b''
    base = 0
    with open(path, 'rb') as f:
        while True:
            data = f.read(chunk)
            if not data:
                return -1
            scan = prev + data
            start_abs = base - len(prev)
            idx = scan.find(b'ftyp')
            while idx != -1:
                box_start = start_abs + idx - 4
                if box_start >= 4:
                    box_len = int.from_bytes(scan[idx - 4:idx], 'big')
                    brand = scan[idx + 4:idx + 8]
                    if 8 <= box_len <= 64 and len(brand) == 4 and brand.isascii() \
                            and all(chr(b).isalnum() for b in brand):
                        return box_start
                idx = scan.find(b'ftyp', idx + 1)
            base += len(data)
            prev = data[-8:]


def _container_kind(path):
    try:
        with open(path, 'rb') as probe:
            head = probe.read(12)
    except OSError:
        return ''
    if head[:4] in (b'PK\x03\x04', b'PK\x05\x06', b'PK\x07\x08'):
        return 'zip'
    if head[:3] == b'\xff\xd8\xff':
        return 'jpeg'
    if head[4:8] == b'ftyp':
        return 'mp4'
    return ''


def liv_parts(name):
    """Extract the still photo and video clip bundled inside a Live Photo file.

    Insta360 .liv containers are ZIP archives holding a JPEG and an MP4; some
    exports are plain JPEG or MP4. Results are cached under LIV_DIR keyed by
    the file id and refreshed when the source file size changes.
    """
    with preview_lk:
        out_dir = safe_path(LIV_DIR, name + '.d')
        photo = os.path.join(out_dir, 'photo.jpg')
        video = os.path.join(out_dir, 'video.mp4')
        meta = os.path.join(out_dir, 'meta.json')
        source = local_path(name)
        if not source:
            item = file_info(name)
            if not item:
                return None
            source = safe_path(PREVIEW_SRC_DIR, name)
            try:
                os.makedirs(os.path.dirname(source), exist_ok=True)
                if not os.path.exists(source):
                    CAMERA_CLIENT.connect()
                    expected = item.get('bytes') if item.get('bytes_exact') else None
                    download_file(item['url'], source, expected_size=expected)
            except Exception as e:
                log.warning('liv(source) ' + name + ':' + str(e)[:60])
                return None
        try:
            size = os.path.getsize(source)
            if os.path.exists(meta) and os.path.exists(photo):
                try:
                    with open(meta) as meta_file:
                        recorded = json.load(meta_file)
                    if recorded.get('size') == size:
                        return {'photo': photo,
                                'video': video if os.path.exists(video) else None}
                except Exception:
                    pass
            kind = _container_kind(source)
            os.makedirs(out_dir, exist_ok=True)
            if kind == 'zip':
                photo_member = video_member = None
                with zipfile.ZipFile(source) as archive:
                    for member in archive.namelist():
                        low = member.lower()
                        if photo_member is None and low.endswith(('.jpg', '.jpeg')):
                            photo_member = member
                        if video_member is None and low.endswith(('.mp4', '.mov')):
                            video_member = member
                    if photo_member:
                        with archive.open(photo_member) as src, open(photo, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
                    if video_member:
                        with archive.open(video_member) as src, open(video, 'wb') as dst:
                            shutil.copyfileobj(src, dst)
            elif kind == 'jpeg':
                split_at = _find_motion_split(source)
                if split_at != -1:
                    with open(source, 'rb') as src, open(photo, 'wb') as dst:
                        remaining = split_at
                        while remaining > 0:
                            block = src.read(min(1024 * 1024, remaining))
                            if not block:
                                break
                            dst.write(block)
                            remaining -= len(block)
                    with open(source, 'rb') as src, open(video, 'wb') as dst:
                        src.seek(split_at)
                        shutil.copyfileobj(src, dst)
                else:
                    shutil.copyfile(source, photo)
            elif kind == 'mp4':
                shutil.copyfile(source, video)
            if not os.path.exists(photo) and os.path.exists(video):
                frame = photo + '.frame.jpg'
                result = run(['ffmpeg', '-v', 'error', '-y', '-i', video,
                              '-frames:v', '1', '-q:v', '3', frame], 60)
                if result.returncode == 0 and os.path.exists(frame) and os.path.getsize(frame) > 0:
                    os.replace(frame, photo)
                else:
                    try:
                        os.remove(frame)
                    except OSError:
                        pass
            if not os.path.exists(photo) and not os.path.exists(video):
                return None
            with open(meta, 'w') as meta_file:
                json.dump({'size': size, 'kind': kind}, meta_file)
            return {'photo': photo if os.path.exists(photo) else None,
                    'video': video if os.path.exists(video) else None}
        except Exception as e:
            log.warning('liv ' + name + ':' + str(e)[:60])
            return None


def dng_preview(name, output, width):
    with preview_lk:
        if os.path.exists(output) and os.path.getsize(output) > 0:
            return output
        source = local_path(name)
        if not source:
            source = safe_path(PREVIEW_SRC_DIR, name)
            os.makedirs(os.path.dirname(source), exist_ok=True)
            if not os.path.exists(source):
                item = file_info(name)
                if not item:
                    return None
                CAMERA_CLIENT.connect()
                expected_size = item.get('bytes') if item.get('bytes_exact') else None
                download_file(item['url'], source, expected_size=expected_size)
        os.makedirs(os.path.dirname(output), exist_ok=True)
        temporary = output + '.part.jpg'
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
            result = run([
                'ffmpeg', '-v', 'error', '-y', '-i', source, '-frames:v', '1',
                '-vf', 'scale=%d:-2:force_original_aspect_ratio=decrease' % width,
                '-q:v', '3', temporary,
            ], 120)
            if result.returncode == 0 and os.path.exists(temporary) and os.path.getsize(temporary) > 0:
                os.replace(temporary, output)
                return output
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    return None


@app.route('/thumb/<path:name>')
def thumb(name):
    tp = safe_path(THUMB_DIR, name + '.jpg')
    if os.path.exists(tp):
        return send_file(tp, mimetype='image/jpeg')
    os.makedirs(os.path.dirname(tp), exist_ok=True)
    low = name.lower()
    if low.endswith('.dng'):
        try:
            preview = dng_preview(name, tp, 220)
            return send_file(preview, mimetype='image/jpeg') if preview else ('', 204)
        except HTTPException:
            raise
        except Exception as e:
            log.warning('thumb(dng) ' + name + ':' + str(e)[:60])
            return ('', 204)
    if low.endswith(('.mp4', '.lrv', '.mov', '.m4v')):
        if os.path.exists(tp) and os.path.getsize(tp) > 0:
            return send_file(tp, mimetype='image/jpeg')
        # one extraction per file; duplicate requests get the placeholder
        # instead of queueing another ffmpeg (NAS CPUs choke otherwise)
        with _gen_lk:
            if name in _generating:
                return ('', 204)
            _generating.add(name)
        try:
            with preview_lk:
                if os.path.exists(tp) and os.path.getsize(tp) > 0:
                    return send_file(tp, mimetype='image/jpeg')
                p = local_path(name)
                url = None if p else file_url(name)
                if not p and not url:
                    return ('', 204)
                try:
                    extract_thumb(p or url, tp, stream=not p)
                    if os.path.exists(tp) and os.path.getsize(tp) > 0:
                        return send_file(tp, mimetype='image/jpeg')
                except Exception as e:
                    log.warning('thumb(video) ' + name + ':' + str(e)[:60])
                if os.path.exists(tp):
                    try:
                        os.remove(tp)
                    except OSError:
                        pass
                return ('', 204)
        finally:
            with _gen_lk:
                _generating.discard(name)
    if is_live_name(name):
        try:
            parts = liv_parts(name)
        except HTTPException:
            raise
        except Exception as e:
            log.warning('thumb(liv) ' + name + ':' + str(e)[:60])
            return ('', 204)
        if not parts or not parts.get('photo'):
            return ('', 204)
        try:
            with open(parts['photo'], 'rb') as local_file:
                data = local_file.read()
            if Image is None:
                return Response(data, mimetype='image/jpeg')
            im = Image.open(io.BytesIO(data)); im.thumbnail((220, 220)); im.convert('RGB').save(tp, 'JPEG', quality=75)
            return send_file(tp, mimetype='image/jpeg')
        except Exception as e:
            log.warning('thumb(liv) ' + name + ':' + str(e)[:60])
            return ('', 204)
    if not low.endswith(('.jpg', '.jpeg', '.insp', '.gif', '.png', '.webp')):
        return ('', 204)
    try:
        p = local_path(name)
        if p:
            with open(p, 'rb') as local_file:
                data = local_file.read()
        else:
            url = file_url(name)
            if not url:
                return ('', 204)
            CAMERA_CLIENT.connect()
            data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'L'}), timeout=20).read()
        if Image is None:
            return Response(data, mimetype='image/jpeg')
        im = Image.open(io.BytesIO(data)); im.thumbnail((220, 220)); im.convert('RGB').save(tp, 'JPEG', quality=75)
        return send_file(tp, mimetype='image/jpeg')
    except HTTPException:
        raise
    except Exception as e:
        log.warning('thumb ' + name + ':' + str(e)[:60])
        return ('', 204)

@app.route('/img/<path:name>')
def img(name):
    if name.lower().endswith('.dng'):
        output = safe_path(THUMB_DIR, name + '.preview.jpg')
        try:
            preview = dng_preview(name, output, 2560)
            return send_file(preview, mimetype='image/jpeg') if preview else ('', 204)
        except Exception as e:
            log.warning('preview(dng) ' + name + ':' + str(e)[:60])
            return ('', 204)
    if is_live_name(name):
        parts = liv_parts(name)
        if parts and parts.get('photo'):
            return send_file(parts['photo'], mimetype='image/jpeg')
        abort(404)
    mime = mimetypes.guess_type(name)[0] or 'image/jpeg'
    p = local_path(name)
    if p:
        return send_file(p, mimetype=mime)
    url = file_url(name)
    if not url:
        abort(404)
    CAMERA_CLIENT.connect()
    data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'L'}), timeout=60).read()
    return Response(data, mimetype=mime)

@app.route('/video/<path:name>')
def video(name):
    if is_live_name(name):
        parts = liv_parts(name)
        if parts and parts.get('video'):
            return send_file(parts['video'], mimetype='video/mp4', conditional=True)
        abort(404)
    local = local_path(name)
    if local:
        return send_file(local, mimetype=mimetypes.guess_type(name)[0] or 'video/mp4', conditional=True)
    url = file_url(name)
    if not url:
        abort(404)
    CAMERA_CLIENT.connect()
    headers = {'User-Agent': 'L'}
    range_h = request.headers.get('Range')
    if range_h:
        headers['Range'] = range_h
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30)
        status = getattr(resp, 'status', None) or resp.getcode() or 200
    except urllib.error.HTTPError as e:
        resp = e; status = e.code
    def gen():
        try:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                resp.close()
            except Exception:
                pass
    out = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store'}
    cr = resp.headers.get('Content-Range')
    cl = resp.headers.get('Content-Length')
    if cr:
        out['Content-Range'] = cr
    if cl:
        out['Content-Length'] = cl
    return Response(gen(), status=status, headers=out, mimetype='video/mp4')

if privacy_accepted():
    load_wifi_state()

workers_lk = threading.Lock()
workers_started = False

def start_workers():
    global workers_started
    with workers_lk:
        if workers_started:
            return
        workers_started = True
    if privacy_accepted():
        load_wifi_state()
        refresh_wifi_backend()
    threading.Thread(target=keeper, daemon=True).start()
    threading.Thread(target=camera_keepalive_worker, daemon=True).start()
    threading.Thread(target=dl_worker, daemon=True).start()
    threading.Thread(target=auto_sync_worker, daemon=True).start()
    threading.Thread(target=_daily_best_worker, daemon=True).start()
    addlog('Luna Sync 启动，WiFi 后端: ' + WIFI_BACKEND + '，无线网卡: ' + (IFACE or '未检测到'))
    addlog('素材保存目录: ' + DLDIR)

def bridge_gateway_ips():
    try:
        out = subprocess.run(
            ['ip', '-o', '-4', 'addr', 'show'],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return []
    ips = []
    for line in out.splitlines():
        fields = line.split()
        if len(fields) < 4:
            continue
        if fields[1] == 'docker0' or fields[1].startswith('br-'):
            ip = fields[3].split('/')[0]
            if ip not in ips:
                ips.append(ip)
    return ips


def start_gateway_forwarder(listen_ip, listen_port, target_port=None):
    import socketserver

    target_port = target_port if target_port is not None else listen_port

    def pipe(source, target):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        finally:
            for sock in (source, target):
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                upstream = socket.create_connection(('127.0.0.1', target_port), timeout=10)
            except OSError:
                try:
                    self.request.close()
                except OSError:
                    pass
                return
            pipes = [
                threading.Thread(target=pipe, args=(self.request, upstream), daemon=True),
                threading.Thread(target=pipe, args=(upstream, self.request), daemon=True),
            ]
            for worker in pipes:
                worker.start()
            for worker in pipes:
                worker.join()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    server = Server((listen_ip, listen_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def run_app(host=None, port=None):
    start_workers()
    port = port or int(os.environ.get('LUNA_WEB_PORT') or CFG.get('web_port', 8765))
    if (not host and not os.environ.get('LUNA_BIND_HOST')
            and os.environ.get('LUNA_GATEWAY_FORWARD', '').strip().lower() in ('1', 'true', 'yes')):
        bound = []
        for gateway_ip in bridge_gateway_ips():
            try:
                start_gateway_forwarder(gateway_ip, port)
                bound.append(gateway_ip)
            except OSError as err:
                addlog('容器网关转发监听失败 ' + gateway_ip + ':' + str(port) + ': ' + str(err))
        if bound:
            host = '127.0.0.1'
            addlog('Web 服务仅监听本机回环，容器网关转发入口: '
                   + ', '.join(ip + ':' + str(port) for ip in bound))
        else:
            host = '0.0.0.0'
            addlog('未找到可用的 docker 桥接网关地址，Web 服务回退监听所有网卡')
    host = host or os.environ.get('LUNA_BIND_HOST') or '0.0.0.0'
    global TLS_ENABLED
    ssl_context = None
    if tls_mode() not in ('off', 'false', '0', 'no', 'disabled'):
        ssl_context = build_ssl_context()
    TLS_ENABLED = ssl_context is not None
    if TLS_ENABLED:
        addlog('HTTPS 已启用，请使用 https:// 地址访问 Web 界面（自签证书首次打开会有告警，属正常现象）')
    else:
        addlog('警告：当前以明文 HTTP 提供服务，登录密码与 Wi-Fi 凭据将未加密传输；'
               '生产环境请保持 TLS 开启（LUNA_TLS=auto）')
    app.run(host=host, port=port, threaded=True, ssl_context=ssl_context)

if __name__ == '__main__':
    run_app()
