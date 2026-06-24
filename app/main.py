import os, sys, time, json, socket, subprocess, logging
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from luna_client import LunaClient
import wifi
CFG=json.load(open(os.environ.get('LUNA_CONFIG','/app/config.json')))
logging.basicConfig(level=CFG.get('log_level','INFO'), format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
log=logging.getLogger('luna-sync')
def cam_reachable(host):
    try: socket.create_connection((host,80), timeout=2).close(); return True
    except OSError: return False
def ensure_wifi():
    ssid=CFG['camera_ssid']; iface=wifi.detect_interface(CFG.get('wifi_iface')); pw=CFG['camera_password']
    if not iface:
        log.warning('no Wi-Fi interface detected'); return False
    if ssid == wifi.current_ssid(iface): return True
    wifi.rescan(iface)
    if ssid not in (line.split(':',1)[0] for line in wifi.scan(iface).stdout.splitlines()):
        log.info(f"hotspot '{ssid}' not in scan; skip"); return False
    log.info(f"connecting to camera hotspot '{ssid}' ...")
    r=wifi.connect(iface, ssid, pw)
    log.info('connect: '+(r.stdout+r.stderr).strip())
    time.sleep(4)
    return ssid == wifi.current_ssid(iface)
def load_state():
    p=CFG['state_file']
    if os.path.exists(p):
        try: return set(json.load(open(p)))
        except Exception: return set()
    return set()
def save_state(s):
    os.makedirs(os.path.dirname(CFG['state_file']), exist_ok=True)
    json.dump(sorted(s), open(CFG['state_file'],'w'))
def sync_once():
    host=CFG['camera_host']
    if not ensure_wifi(): return
    if not cam_reachable(host): log.warning('connected but camera HTTP unreachable'); return
    cli=LunaClient(host=host, port=CFG['control_port'], base=CFG['camera_path'])
    try:
        files=cli.list_files(); log.info(f'camera has {len(files)} files')
        state=load_state(); os.makedirs(CFG['download_dir'], exist_ok=True)
        new=0; partial=0
        for f in files:
            name=f['name']
            if name in state: continue
            dest=os.path.join(CFG['download_dir'], name)
            log.info(f"downloading {name} ({f['size_text']}) ...")
            try:
                actual=cli.download(f['url'], dest); exp=f.get('bytes')
                if exp is None or actual>=exp:
                    state.add(name); save_state(state); new+=1; log.info(f"  done {name} ({actual}B)")
                else: partial+=1; log.warning(f"  incomplete {name}: {actual}/{exp}")
            except Exception as e: log.error(f"download {name} failed: {e}")
        log.info(f'cycle done: {new} new, {partial} partial, {len(state)} total')
    finally: cli.close()
def main():
    log.info('luna-sync started (self-managed wifi via nmcli/dbus)')
    while True:
        try: sync_once()
        except Exception as e: log.exception(f'cycle error: {e}')
        time.sleep(CFG['poll_interval_sec'])
if __name__=='__main__': main()
