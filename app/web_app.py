import os, sys, json, time, threading, socket, subprocess, logging, io, mimetypes, ipaddress
import urllib.request, urllib.error
from flask import Flask, jsonify, request, render_template, send_file, Response, abort
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luna_client import LunaClient, file_kind
from downloader import download_file
import wifi
try:
    from PIL import Image
except Exception:
    Image = None

CFG = json.load(open(os.environ.get('LUNA_CONFIG', '/app/config.json')))
logging.basicConfig(level='INFO', format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
log = logging.getLogger('luna')
app = Flask(__name__)
HOST = CFG['camera_host']
DLDIR = CFG['download_dir']
def configured_wifi_backend():
    return os.environ.get('LUNA_WIFI_BACKEND') or CFG.get('wifi_backend', 'auto')

WIFI_BACKEND = wifi.configure(configured_wifi_backend(), CFG.get('wpa_ctrl'))
IFACE = wifi.detect_interface(CFG.get('wifi_iface'))
backend_lk = threading.Lock()

def config_value(value):
    text = str(value or '').strip()
    return '' if text.upper().startswith('YOUR_') else text

CAM_SSID = config_value(CFG.get('camera_ssid'))
DEF_PW = config_value(CFG.get('camera_password')) or None
AUTO_INTERVAL = max(10, int(CFG.get('auto_sync_interval_sec', 30)))
STATE_DIR = CFG.get('state_dir', '/state')
THUMB_DIR = os.path.join(STATE_DIR, 'thumbs')
ENC_DIR = os.path.join(STATE_DIR, 'encoded')
WIFI_FILE = os.path.join(STATE_DIR, 'wifi.json')
for d in (DLDIR, THUMB_DIR, ENC_DIR):
    os.makedirs(d, exist_ok=True)

lk = threading.Lock()
scan_lk = threading.Lock()
auto_sync_lk = threading.Lock()
_scan_cache = {'ts': 0, 'data': None, 'rescan_ts': 0}
SCAN_CACHE_TTL = 8
SCAN_RESCAN_INTERVAL = 12

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
      'wifi_saved': False, 'transcodes': {}, 'auto_sync': bool(CFG.get('auto_sync', True)),
      'last_auto_sync': ''}
cancel = threading.Event()
last_auto_notice = 0

def addlog(m):
    with lk:
        ST['log'].append(m); ST['log'] = ST['log'][-150:]
    log.info(m)

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
    if not CAM_SSID:
        ensure_camera_ipv4()
        return cam_on()
    if not wifi.requires_target_ssid():
        return cam_on()
    cur = current_ssid()
    ok = bool(cur and CAM_SSID and cur == CAM_SSID)
    if ok:
        ensure_camera_ipv4()
    return ok

def cam_on():
    try:
        socket.create_connection((HOST, 80), 2).close(); return True
    except OSError:
        return False

def load_saved_wifi():
    try:
        if os.path.exists(WIFI_FILE):
            data = json.load(open(WIFI_FILE))
            if data.get('ssid') and data.get('password'):
                return data
            if data.get('ssid'):
                log.warning('saved wifi has no password: ' + data.get('ssid', '')[:60])
    except Exception as e:
        log.warning('load_saved_wifi:' + str(e)[:50])
        pass
    if CAM_SSID and DEF_PW:
        return {'ssid': CAM_SSID, 'password': DEF_PW, 'source': 'config'}
    return None

def save_wifi(ssid, pw):
    try:
        existing = load_saved_wifi() or {}
        if not pw and existing.get('ssid') == ssid and existing.get('password'):
            pw = existing['password']
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
    with lk:
        enabled = ST['auto_sync']
    if not enabled:
        return
    addlog(reason)
    threading.Thread(target=auto_sync_once, kwargs={'manual': True}, daemon=True).start()

def keeper():
    while True:
        try:
            refresh_wifi_backend()
            with lk:
                target = ST['wifi_target']; pw = ST['wifi_password'] or DEF_PW
            cur = current_ssid()
            with lk:
                ST['wifi_current'] = cur
            if wifi.can_control() and target and cur != target and pw is not None:
                try_connect(target, pw); cur = current_ssid()
                with lk:
                    ST['wifi_current'] = cur
            connected = wifi_on_target() and cam_on()
            with lk:
                was_connected = ST['connected']
                ST['connected'] = connected
            if connected and not was_connected:
                trigger_auto_sync_check('检测到 Luna 已连接，开始自动同步检查')
        except Exception as e:
            log.warning('keeper:' + str(e)[:50])
        time.sleep(12)

def local_files():
    out = {}
    if os.path.isdir(DLDIR):
        for f in os.listdir(DLDIR):
            p = os.path.join(DLDIR, f)
            if os.path.isfile(p) and not f.endswith('.part'):
                out[f] = os.path.getsize(p)
    return out

def local_items():
    loc = local_files()
    with lk:
        meta = {f['name']: dict(f) for f in ST['files']}
    items = []
    for name, size in sorted(loc.items()):
        item = meta.get(name, {'name': name, 'kind': file_kind(name), 'date': '', 'time': '', 'size_text': ''})
        item['bytes'] = size
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
    path = safe_path(DLDIR, name)
    return path if os.path.isfile(path) else None

def refresh():
    if not (wifi_on_target() and cam_on()):
        with lk:
            ST['connected'] = False
        return False
    try:
        cli = LunaClient(HOST)
        try:
            cli.connect(); files = cli.list_files()
        finally:
            cli.close()
        loc = local_files()
        for f in files:
            f['status'] = '完成' if f['name'] in loc else '就绪'
        with lk:
            ST['files'] = files; ST['connected'] = True
        return True
    except Exception as e:
        addlog('列文件失败:' + str(e)[:60])
        with lk:
            ST['connected'] = False
        return False

def enqueue(names):
    loc = local_files()
    added = 0
    skipped = []
    with lk:
        current = ST['current']['name'] if ST['current'] else None
        known = {f['name'] for f in ST['files']}
        for name in names:
            if name in loc:
                skipped.append({'name': name, 'reason': 'already_local'})
                continue
            if name in ST['queue'] or name == current:
                skipped.append({'name': name, 'reason': 'already_queued'})
                continue
            if name not in known:
                skipped.append({'name': name, 'reason': 'not_available'})
                continue
            ST['queue'].append(name)
            added += 1
    return added, skipped

def auto_sync_once(manual=False):
    if not auto_sync_lk.acquire(blocking=False):
        if manual:
            addlog('自动同步已在运行')
        return 0
    try:
        if not prepare_auto_sync_connection():
            if manual:
                addlog('自动同步未开始: 相机未就绪')
            return 0
        if not refresh():
            if manual:
                addlog('自动同步未开始: 扫描相机文件失败')
            return 0
        with lk:
            names = [f['name'] for f in ST['files']]
        added, _ = enqueue(names)
        with lk:
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
            with lk:
                enabled = ST['auto_sync']
            if enabled:
                auto_sync_once()
        except Exception as e:
            log.warning('auto_sync:' + str(e)[:60])
        time.sleep(AUTO_INTERVAL)

def dl_worker():
    while True:
        name = None
        with lk:
            if ST['queue']:
                name = ST['queue'].pop(0)
        if not name:
            time.sleep(2); continue
        with lk:
            f = next((x for x in ST['files'] if x['name'] == name), None)
        if not f:
            addlog(name + ' 不在列表'); continue
        if not (wifi_on_target() and cam_on()):
            with lk:
                ST['queue'].insert(0, name)
            time.sleep(15); continue
        dest = os.path.join(DLDIR, name)
        with lk:
            ST['current'] = {'name': name, 'downloaded': 0, 'total': f.get('bytes'), 'speed': 0}
        addlog('开始下载 ' + name)
        try:
            cli = LunaClient(HOST); cli.connect()
            def prog(n, d, t, s):
                with lk:
                    ST['current'] = {'name': n, 'downloaded': d, 'total': t, 'speed': s}
            download_file(f['url'], dest, on_progress=prog, cancel=cancel)
            with lk:
                ST['completed'] += 1; ST['current'] = None
            addlog('完成 ' + name); cli.close()
        except Exception as e:
            addlog('失败 ' + name + ':' + str(e)[:60])
            with lk:
                ST['current'] = None
        cancel.clear()


def transcode_worker(name):
    out = safe_path(ENC_DIR, name + '.mp4')
    src_file = safe_path(DLDIR, name)
    try:
        if not os.path.exists(src_file):
            url = file_url(name)
            if not url:
                with lk: ST['transcodes'][name] = {'status': 'failed', 'msg': '无URL(先扫描文件)'}
                return
            with lk: ST['transcodes'][name] = {'status': 'downloading'}
            cli = LunaClient(HOST); cli.connect()
            try:
                download_file(url, src_file)
            finally:
                cli.close()
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

def file_url(name):
    with lk:
        f = next((x for x in ST['files'] if x['name'] == name), None)
    return f['url'] if f else None


@app.route('/api/transcode/status/<path:name>')
def api_tc_status(name):
    with lk:
        st = dict(ST['transcodes'].get(name, {'status': 'pending'}))
    out = os.path.join(ENC_DIR, name + '.mp4')
    if os.path.exists(out):
        st['status'] = 'done'
    return jsonify(st)

@app.route('/api/transcode/<path:name>', methods=['POST'])
def api_tc_start(name):
    out = os.path.join(ENC_DIR, name + '.mp4')
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

@app.route('/api/state')
def api_state():
    refresh_wifi_backend()
    with lk:
        return jsonify({'connected': ST['connected'], 'wifi_conn': ST['wifi_conn'],
            'wifi_current': ST['wifi_current'], 'wifi_saved': ST['wifi_saved'],
            'file_count': len(ST['files']), 'queue_len': len(ST['queue']),
            'current': ST['current'], 'completed': ST['completed'],
            'log': ST['log'][-12:], 'camera_ssid': CAM_SSID, 'wifi_iface': IFACE,
            'wifi_backend': WIFI_BACKEND, 'wifi_control': wifi.can_control(),
            'wifi_target': ST['wifi_target'], 'wifi_has_password': bool(ST['wifi_password'] or DEF_PW),
            'auto_sync': ST['auto_sync'], 'auto_interval': AUTO_INTERVAL,
            'last_auto_sync': ST['last_auto_sync']})

@app.route('/api/auto-sync', methods=['POST'])
def api_auto_sync():
    data = request.json or {}
    enabled = bool(data.get('enabled'))
    with lk:
        ST['auto_sync'] = enabled
    addlog('自动同步已' + ('开启' if enabled else '关闭'))
    if enabled:
        trigger_auto_sync_check('自动同步已开启，开始检查')
    return jsonify({'ok': True, 'auto_sync': enabled})

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
        nets.append({'ssid': ssid, 'signal': parts[1] if len(parts) > 1 else '',
                     'secure': 'yes' if (len(parts) > 2 and parts[2]) else 'no',
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
    cancel.set(); addlog('请求取消')
    return jsonify({'ok': True})

@app.route('/api/file/<path:name>', methods=['DELETE'])
def api_del(name):
    p = safe_path(DLDIR, name)
    if os.path.exists(p):
        os.remove(p)
    if os.path.exists(p + '.part'):
        os.remove(p + '.part')
    for extra in (safe_path(ENC_DIR, name + '.mp4'), safe_path(THUMB_DIR, name + '.jpg')):
        if os.path.exists(extra):
            os.remove(extra)
    addlog('删除 ' + name)
    return jsonify({'ok': True})

def _wipe_dir(d):
    n = t = 0
    if not os.path.isdir(d):
        return (0, 0)
    for fn in os.listdir(d):
        fp = os.path.join(d, fn)
        try:
            if os.path.isfile(fp) or os.path.islink(fp):
                t += os.path.getsize(fp)
                os.remove(fp)
                n += 1
        except Exception as e:
            log.warning('wipe ' + fn + ':' + str(e)[:60])
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
    msg = '清理缓存 ' + str(files) + ' 个文件 / ' + _human(space)
    addlog(msg)
    return jsonify({'ok': True, 'files': files, 'bytes': space, 'msg': msg})

def _human(b):
    for u in ('B', 'KB', 'MB', 'GB'):
        if b < 1024:
            return ('%.0f' % b if u == 'B' else '%.1f' % b) + ' ' + u
        b /= 1024
    return '%.1f TB' % b

@app.route('/thumb/<path:name>')
def thumb(name):
    tp = safe_path(THUMB_DIR, name + '.jpg')
    if os.path.exists(tp):
        return send_file(tp, mimetype='image/jpeg')
    low = name.lower()
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
            data = open(p, 'rb').read()
        else:
            url = file_url(name)
            if not url:
                return ('', 204)
            cli = LunaClient(HOST); cli.connect()
            try:
                data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'L'}), timeout=20).read()
            finally:
                cli.close()
        if Image is None:
            return Response(data, mimetype='image/jpeg')
        im = Image.open(io.BytesIO(data)); im.thumbnail((220, 220)); im.convert('RGB').save(tp, 'JPEG', quality=75)
        return send_file(tp, mimetype='image/jpeg')
    except Exception as e:
        log.warning('thumb ' + name + ':' + str(e)[:60])
        return ('', 204)

@app.route('/img/<path:name>')
def img(name):
    mime = mimetypes.guess_type(name)[0] or 'image/jpeg'
    p = local_path(name)
    if p:
        return send_file(p, mimetype=mime)
    url = file_url(name)
    if not url:
        abort(404)
    cli = LunaClient(HOST); cli.connect()
    try:
        data = urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'L'}), timeout=60).read()
    finally:
        cli.close()
    return Response(data, mimetype=mime)

@app.route('/video/<path:name>')
def video(name):
    local = local_path(name)
    if local:
        return send_file(local, mimetype=mimetypes.guess_type(name)[0] or 'video/mp4', conditional=True)
    url = file_url(name)
    if not url:
        abort(404)
    cli = LunaClient(HOST); cli.connect()
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
            cli.close()
    out = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store'}
    cr = resp.headers.get('Content-Range')
    cl = resp.headers.get('Content-Length')
    if cr:
        out['Content-Range'] = cr
    if cl:
        out['Content-Length'] = cl
    return Response(gen(), status=status, headers=out, mimetype='video/mp4')

# Load remembered Wi-Fi before workers start.
saved = load_saved_wifi()
if saved and saved.get('ssid') and saved.get('password'):
    ST['wifi_target'] = saved['ssid']
    ST['wifi_password'] = saved['password']
    ST['wifi_saved'] = True
    addlog(('加载配置中的WiFi: ' if saved.get('source') == 'config' else '加载记住的WiFi: ') + saved['ssid'])

if __name__ == '__main__':
    threading.Thread(target=keeper, daemon=True).start()
    threading.Thread(target=dl_worker, daemon=True).start()
    threading.Thread(target=auto_sync_worker, daemon=True).start()
    addlog('Luna Sync 启动，WiFi 后端: ' + WIFI_BACKEND + '，无线网卡: ' + (IFACE or '未检测到'))
    app.run(host='0.0.0.0', port=CFG.get('web_port', 8765), threaded=True)
