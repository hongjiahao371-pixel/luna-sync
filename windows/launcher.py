import argparse
import json
import os
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


APP_DIR_NAME = 'LunaSync'
DEFAULT_PORT = 8765


def videos_dir():
    try:
        import winreg
        key_path = r'Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _ = winreg.QueryValueEx(key, 'My Video')
        return Path(os.path.expandvars(value))
    except Exception:
        return Path.home() / 'Videos'


def default_paths():
    local_app_data = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
    data_dir = local_app_data / APP_DIR_NAME
    download_dir = videos_dir() / 'Luna Sync'
    return data_dir, download_dir


def ensure_config(config_path, download_dir, state_dir, port=DEFAULT_PORT):
    config_path = Path(config_path)
    if config_path.exists():
        return json.loads(config_path.read_text(encoding='utf-8'))
    config_path.parent.mkdir(parents=True, exist_ok=True)
    Path(download_dir).mkdir(parents=True, exist_ok=True)
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    config = {
        'camera_host': '192.168.42.1',
        'camera_ssid': '',
        'camera_password': '',
        'wifi_backend': 'windows',
        'wifi_iface': None,
        'auto_sync': True,
        'auto_sync_lrv': True,
        'auto_sync_interval_sec': 30,
        'download_dir': str(download_dir),
        'state_dir': str(state_dir),
        'web_port': port,
    }
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
    return config


def runtime_root():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def configure_runtime(config_path, download_dir, state_dir, port):
    root = runtime_root()
    app_dir = root if getattr(sys, 'frozen', False) else root / 'app'
    sys.path.insert(0, str(app_dir))
    os.environ['LUNA_CONFIG'] = str(config_path)
    os.environ['DOWNLOAD_DIR'] = str(download_dir)
    os.environ['STATE_DIR'] = str(state_dir)
    os.environ['LUNA_WIFI_BACKEND'] = 'windows'
    os.environ['LUNA_BIND_HOST'] = '127.0.0.1'
    os.environ['LUNA_WEB_PORT'] = str(port)
    os.environ['PATH'] = str(root) + os.pathsep + os.environ.get('PATH', '')


def server_is_running(url):
    try:
        with urllib.request.urlopen(url + '/api/state', timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def open_when_ready(url):
    for _ in range(60):
        if server_is_running(url):
            webbrowser.open(url)
            return
        time.sleep(0.5)


def parse_args():
    parser = argparse.ArgumentParser(description='Luna Sync for Windows')
    parser.add_argument('--download-dir', help='Directory used for downloaded camera media')
    parser.add_argument('--data-dir', help='Directory used for configuration and cache files')
    parser.add_argument('--port', type=int, help='Local WebUI port')
    parser.add_argument('--no-browser', action='store_true', help='Do not open the WebUI automatically')
    return parser.parse_args()


def main():
    args = parse_args()
    default_data_dir, default_download_dir = default_paths()
    data_dir = Path(args.data_dir).expanduser() if args.data_dir else default_data_dir
    download_dir = Path(args.download_dir).expanduser() if args.download_dir else default_download_dir
    state_dir = data_dir / 'state'
    config_path = data_dir / 'config.json'
    config = ensure_config(config_path, download_dir, state_dir, args.port or DEFAULT_PORT)
    port = args.port or int(config.get('web_port', DEFAULT_PORT))
    if args.download_dir:
        download_dir.mkdir(parents=True, exist_ok=True)
    else:
        download_dir = Path(config.get('download_dir') or download_dir)
    state_dir = Path(config.get('state_dir') or state_dir)
    configure_runtime(config_path, download_dir, state_dir, port)
    url = 'http://127.0.0.1:%d' % port
    if server_is_running(url):
        if not args.no_browser:
            webbrowser.open(url)
        return 0
    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url,), daemon=True).start()
    print('Luna Sync is running at ' + url)
    print('Downloaded media: ' + str(download_dir))
    print('Close this window to stop Luna Sync.')
    import web_app
    web_app.run_app(host='127.0.0.1', port=port)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
