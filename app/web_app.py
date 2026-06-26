import os, sys, json, time, threading, socket, subprocess, logging, io, mimetypes
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
IFACE = wifi.detect_interface(CFG.get('wifi_iface'))
CAM_SSID = CFG.get('camera_ssid', '')
DEF_PW = CFG.get('camera_password')
AUTO_INTERVAL = max(10, int(CFG.get('auto_sync_interval_sec', 30)))
STATE_DIR = CFG.get('state_dir', '/state')
THUMB_DIR = os.path.join(STATE_DIR, 'thumbs')
ENC_DIR = os.path.join(STATE_DIR, 'encoded')
WIFI_FILE = os.path.join(STATE_DIR, 'wifi.json')
for d in (DLDIR, THUMB_DIR, ENC_DIR):
    os.makedirs(d, exist_ok=True)

lk = threading.Lock()
ST = {'connected': False, 'wifi_conn': False, 'files': [], 'queue': [], 'current': None,
      'completed': 0, 'log': [], 'wifi_current': '', 'wifi_target': CAM_SSID, 'wifi_password': None,
      'wifi_saved': False, 'transcodes': {}, 'auto_sync': bool(CFG.get('auto_sync', True)),
      'last_auto_sync': ''}
cancel = threading.Event()

def addlog(m):
    with lk:
        ST['log'].append(m); ST['log'] = ST['log'][-150:]
    log.info(m)

def run(args, t=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=t)

def current_ssid():
    return wifi.current_ssid(IFACE)

def wifi_on_target():
    cur = current_ssid()
    return bool(cur and CAM_SSID and cur == CAM_SSID)

def cam_on():
    try:
        socket.create_connection((HOST, 80), 2).close(); return True
    except OSError:
        return False

def load_saved_wifi():
    try:
        if os.path.exists(WIFI_FILE):
            return json.load(open(WIFI_FILE))
    except Exception:
        pass
    return None

def save_wifi(ssid, pw):
    try:
        with open(WIFI_FILE, 'w') as f:
            json.dump({'ssid': ssid, 'password': pw}, f)
        os.chmod(WIFI_FILE, 0o600)
        with lk:
            ST['wifi_saved'] = True
        addlog('已记住 WiFi: ' + ssid)
    except Exception as e:
        log.warning('save_wifi:' + str(e)[:50])

def try_connect(ssid, pw):
    if not IFACE:
        addlog('未检测到无线网卡')
        return False
    addlog('连接 ' + ssid + ' ...')
    wifi.rescan(IFACE)
    r = wifi.scan(IFACE)
    if ssid not in (line.split(':', 1)[0] for line in r.stdout.splitlines()):
        addlog('未扫到 ' + ssid); return False
    result = wifi.connect(IFACE, ssid, pw)
    if result.returncode != 0:
        addlog('连接失败: ' + (result.stderr or result.stdout).strip()[:80])
    time.sleep(4)
    ok = (current_ssid() == ssid)
    addlog('已连 ' + ssid if ok else '连接 ' + ssid + ' 失败')
    return ok

def keeper():
    while True:
        try:
            with lk:
                target = ST['wifi_target']; pw = ST['wifi_password'] or DEF_PW
            cur = current_ssid()
            with lk:
                ST['wifi_current'] = cur
            if target and cur != target and pw is not None:
                try_connect(target, pw); cur = current_ssid()
                with lk:
                    ST['wifi_current'] = cur
            with lk:
                ST['connected'] = (CAM_SSID and cur == CAM_SSID and cam_on()) or (not CAM_SSID and cam_on())
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
    with lk:
        current = ST['current']['name'] if ST['current'] else None
        known = {f['name'] for f in ST['files']}
        for name in names:
            if name in loc or name in ST['queue'] or name == current or name not in known:
                continue
            ST['queue'].append(name)
            added += 1
    return added

def auto_sync_once():
    if not refresh():
        return 0
    with lk:
        names = [f['name'] for f in ST['files']]
    added = enqueue(names)
    with lk:
        ST['last_auto_sync'] = time.strftime('%H:%M:%S')
    if added:
        addlog('自动同步加入 ' + str(added) + ' 个新文件')
    return added

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
    with lk:
        return jsonify({'connected': ST['connected'], 'wifi_conn': ST['wifi_conn'],
            'wifi_current': ST['wifi_current'], 'wifi_saved': ST['wifi_saved'],
            'file_count': len(ST['files']), 'queue_len': len(ST['queue']),
            'current': ST['current'], 'completed': ST['completed'],
            'log': ST['log'][-12:], 'camera_ssid': CAM_SSID, 'wifi_iface': IFACE,
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
        threading.Thread(target=auto_sync_once, daemon=True).start()
    return jsonify({'ok': True, 'auto_sync': enabled})

@app.route('/api/wifi/scan')
def wifi_scan():
    if not IFACE:
        return jsonify({'nets': [], 'current': '', 'camera_ssid': CAM_SSID,
                        'wifi_iface': None, 'error': '未检测到无线网卡'}), 503
    wifi.rescan(IFACE)
    r = wifi.scan(IFACE)
    nets = []; seen = set()
    for line in r.stdout.splitlines():
        parts = line.split(':')
        if len(parts) < 2:
            continue
        ssid = parts[0]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        nets.append({'ssid': ssid, 'signal': parts[1] if len(parts) > 1 else '',
                     'secure': 'yes' if (len(parts) > 2 and parts[2]) else 'no',
                     'is_camera': ssid == CAM_SSID})
    with lk:
        ST['wifi_current'] = current_ssid()
    return jsonify({'nets': nets, 'current': ST['wifi_current'],
                    'camera_ssid': CAM_SSID, 'wifi_iface': IFACE})

@app.route('/api/wifi/connect', methods=['POST'])
def wifi_connect():
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
            if try_connect(ssid, pw) and ssid == CAM_SSID:
                refresh()
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
            ST['wifi_saved'] = False
        addlog('已清除记住的WiFi')
    except Exception as e:
        log.warning('forget:' + str(e)[:50])
    return jsonify({'ok': True})

@app.route('/api/files')
def api_files():
    ok = refresh(); loc = local_files()
    with lk:
        files = list(ST['files'])
    cn = set(f['name'] for f in files)
    for n, sz in loc.items():
        if n not in cn:
            files.append({'name': n, 'kind': file_kind(n), 'date': '', 'time': '', 'size_text': '', 'bytes': sz, 'status': '完成(本地)'})
    return jsonify({'connected': ok, 'items': files})

@app.route('/api/download', methods=['POST'])
def api_dl():
    ns = (request.json or {}).get('files', [])
    added = enqueue(ns)
    addlog('队列 +' + str(added))
    return jsonify({'queued': added})

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

@app.route('/thumb/<path:name>')
def thumb(name):
    tp = safe_path(THUMB_DIR, name + '.jpg')
    if os.path.exists(tp):
        return send_file(tp, mimetype='image/jpeg')
    low = name.lower()
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

# 启动时加载记住的WiFi
saved = load_saved_wifi()
if saved and saved.get('ssid') and saved.get('password'):
    ST['wifi_target'] = saved['ssid']
    ST['wifi_password'] = saved['password']
    ST['wifi_saved'] = True
    addlog('加载记住的WiFi: ' + saved['ssid'])

if __name__ == '__main__':
    threading.Thread(target=keeper, daemon=True).start()
    threading.Thread(target=dl_worker, daemon=True).start()
    threading.Thread(target=auto_sync_worker, daemon=True).start()
    addlog('Luna Sync 启动，无线网卡: ' + (IFACE or '未检测到'))
    app.run(host='0.0.0.0', port=CFG.get('web_port', 8765), threaded=True)
