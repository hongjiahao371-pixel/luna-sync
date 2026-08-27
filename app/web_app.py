import os, sys, json, time, threading, socket, subprocess, logging, io, mimetypes, ipaddress
import hashlib, hmac
import urllib.request, urllib.error
from flask import Flask, jsonify, request, render_template, send_file, Response, abort, redirect
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luna_client import LunaClient, file_kind
from downloader import download_file
import wifi
try:
    from PIL import Image
except Exception:
    Image = None

with open(os.environ.get('LUNA_CONFIG', '/app/config.json')) as config_file:
    CFG = json.load(config_file)
logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
log = logging.getLogger('luna')
app = Flask(__name__)

PRIVACY_VERSION = '2026-07-22'
PRIVACY_POLICY_URL = '/privacy'
TERMS_URL = '/terms'

@app.after_request
def disable_home_cache(response):
    if request.path == '/':
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response

HOST = CFG['camera_host']
CAMERA_CLIENT = LunaClient(HOST)
DLDIR = os.environ.get('DOWNLOAD_DIR') or CFG['download_dir']
def configured_wifi_backend():
    return os.environ.get('LUNA_WIFI_BACKEND') or CFG.get('wifi_backend', 'auto')

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
WIFI_FILE = os.path.join(STATE_DIR, 'wifi.json')
SETTINGS_FILE = os.path.join(STATE_DIR, 'settings.json')
for d in (DLDIR, THUMB_DIR, ENC_DIR, PREVIEW_SRC_DIR):
    os.makedirs(d, exist_ok=True)

lk = threading.RLock()
scan_lk = threading.Lock()
refresh_lk = threading.Lock()
auto_sync_lk = threading.Lock()
preview_lk = threading.Lock()
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
      'wifi_saved': False, 'transcodes': {}, 'auto_sync': bool_value(SETTINGS.get('auto_sync'), bool_value(CFG.get('auto_sync'), True)),
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

def load_saved_wifi():
    try:
        if os.path.exists(WIFI_FILE):
            with open(WIFI_FILE) as wifi_file:
                data = json.load(wifi_file)
            if data.get('ssid') and data.get('password'):
                return data
            if data.get('ssid'):
                log.warning('saved wifi has no password: ' + data.get('ssid', '')[:60])
    except Exception as e:
        log.warning('load_saved_wifi:' + str(e)[:50])
        pass
    return None

def save_wifi(ssid, pw):
    try:
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
            json.dump({'ssid': ssid, 'password': pw}, f)
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
    root = os.path.abspath(base)
    path = os.path.abspath(os.path.join(root, name))
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
                    'terms_url': TERMS_URL})

AUTH_COOKIE = 'luna_session'
AUTH_SESSION_SECONDS = 30 * 24 * 3600
AUTH_PUBLIC_PATHS = {'/login', '/privacy', '/terms', '/api/auth-state',
                     '/api/auth/login', '/api/auth/setup', '/api/auth/logout'}

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
                        httponly=True, samesite='Lax', path='/')
    return response

@app.route('/login')
def login_page():
    return render_template('login.html')

@app.route('/api/auth-state')
def api_auth_state():
    return jsonify({'password_set': auth_password_set(), 'authenticated': session_valid(),
                    'managed_by_config': bool(config_auth_password())})

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
    if request.path in AUTH_PUBLIC_PATHS or request.path.startswith('/static/'):
        return None
    if session_valid():
        return None
    if request.path == '/':
        return redirect('/login')
    return jsonify({'ok': False, 'error': 'authentication_required'}), 401

@app.before_request
def require_privacy_consent():
    public_paths = {'/', '/privacy', '/terms', '/api/privacy', '/api/state'} | AUTH_PUBLIC_PATHS
    if request.path in public_paths or request.path.startswith('/static/'):
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

@app.route('/api/file/<path:name>', methods=['DELETE'])
def api_del(name):
    p = local_path(name) or safe_path(DLDIR, name)
    if os.path.exists(p):
        os.remove(p)
    if os.path.exists(p + '.part'):
        os.remove(p + '.part')
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
    addlog('删除 ' + name)
    return jsonify({'ok': True})

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
    msg = '清理缓存 ' + str(files) + ' 个文件 / ' + _human(space)
    addlog(msg)
    return jsonify({'ok': True, 'files': files, 'bytes': space, 'msg': msg})

def _human(b):
    for u in ('B', 'KB', 'MB', 'GB'):
        if b < 1024:
            return ('%.0f' % b if u == 'B' else '%.1f' % b) + ' ' + u
        b /= 1024
    return '%.1f TB' % b


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
        except Exception as e:
            log.warning('thumb(dng) ' + name + ':' + str(e)[:60])
            return ('', 204)
    if low.endswith(('.mp4', '.lrv', '.mov', '.m4v')):
        p = local_path(name)
        if not p:
            return ('', 204)
        try:
            run(['ffmpeg', '-y', '-ss', '1', '-i', p, '-frames:v', '1',
                 '-vf', 'scale=320:-2', '-q:v', '4', tp], 30)
            if os.path.exists(tp) and os.path.getsize(tp) > 0:
                return send_file(tp, mimetype='image/jpeg')
            return ('', 204)
        except Exception as e:
            log.warning('thumb(video) ' + name + ':' + str(e)[:60])
            return ('', 204)
    if not low.endswith(('.jpg', '.jpeg', '.insp', '.liv', '.gif', '.png', '.webp')):
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
    app.run(host=host, port=port, threaded=True)

if __name__ == '__main__':
    run_app()
