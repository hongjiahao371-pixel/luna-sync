import glob
import os
import platform
import shutil
import subprocess
import time


BACKEND = 'auto'
RESOLVED_BACKEND = 'none'
WPA_CTRL = '/run/wpa_supplicant'
WPA_START_ATTEMPTS = {}


def run(args, timeout=30):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, '', args[0] + ' not found')
    except subprocess.TimeoutExpired as e:
        return subprocess.CompletedProcess(args, 124, e.stdout or '', e.stderr or 'timeout')


def configure(backend='auto', wpa_ctrl=None):
    global BACKEND, RESOLVED_BACKEND, WPA_CTRL
    BACKEND = (backend or 'auto').strip().lower()
    WPA_CTRL = wpa_ctrl or WPA_CTRL
    RESOLVED_BACKEND = resolve_backend(BACKEND)
    return RESOLVED_BACKEND


def resolve_backend(backend=None):
    backend = (backend or BACKEND or 'auto').strip().lower()
    aliases = {
        'nm': 'networkmanager',
        'network-manager': 'networkmanager',
        'manual': 'none',
        'off': 'none',
        'disabled': 'none',
        'wpa': 'wpa_supplicant',
        'wpasupplicant': 'wpa_supplicant',
        'win': 'windows',
    }
    backend = aliases.get(backend, backend)
    if backend != 'auto':
        return backend if backend in ('networkmanager', 'wpa_supplicant', 'windows', 'none') else 'none'
    if platform.system().lower() == 'windows' and windows_interfaces():
        return 'windows'
    if shutil.which('nmcli') and os.path.exists('/run/NetworkManager') and os.path.exists('/run/dbus/system_bus_socket'):
        return 'networkmanager'
    if shutil.which('iw') and shutil.which('wpa_cli') and wireless_interfaces():
        return 'wpa_supplicant'
    return 'none'


def backend():
    return RESOLVED_BACKEND


def can_control():
    return RESOLVED_BACKEND in ('networkmanager', 'wpa_supplicant', 'windows')


def requires_target_ssid():
    return can_control()


def wireless_interfaces():
    out = []
    seen = set()

    def add(name):
        if name and name not in seen:
            seen.add(name)
            out.append(name)

    for path in glob.glob('/sys/class/net/*/wireless'):
        add(os.path.basename(os.path.dirname(path)))
    for path in glob.glob('/sys/class/net/*/uevent'):
        name = os.path.basename(os.path.dirname(path))
        try:
            data = open(path).read()
        except Exception:
            data = ''
        if 'DEVTYPE=wlan' in data:
            add(name)
    for path in glob.glob('/sys/class/net/*'):
        name = os.path.basename(path)
        if name.startswith(('wlan', 'wlx', 'wlp', 'wl')):
            add(name)
    return out


def detect_interface(preferred=None):
    if RESOLVED_BACKEND == 'windows':
        return windows_detect_interface(preferred)
    if RESOLVED_BACKEND == 'networkmanager':
        return nm_detect_interface(preferred)
    if RESOLVED_BACKEND == 'wpa_supplicant':
        return wpa_detect_interface(preferred)
    return None


def current_ssid(interface):
    if RESOLVED_BACKEND == 'windows':
        return windows_current_ssid(interface)
    if RESOLVED_BACKEND == 'networkmanager':
        return nm_current_ssid(interface)
    if RESOLVED_BACKEND == 'wpa_supplicant':
        return wpa_current_ssid(interface)
    return ''


def scan(interface=None):
    if RESOLVED_BACKEND == 'windows':
        return windows_scan(interface)
    if RESOLVED_BACKEND == 'networkmanager':
        return nm_scan(interface)
    if RESOLVED_BACKEND == 'wpa_supplicant':
        return wpa_scan(interface)
    return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='wifi backend disabled')


def rescan(interface=None):
    if RESOLVED_BACKEND == 'windows':
        return windows_rescan(interface)
    if RESOLVED_BACKEND == 'networkmanager':
        return nm_rescan(interface)
    if RESOLVED_BACKEND == 'wpa_supplicant':
        return wpa_rescan(interface)
    return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='wifi backend disabled')


def connect(interface, ssid, password):
    if RESOLVED_BACKEND == 'windows':
        return windows_connect(interface, ssid, password)
    if RESOLVED_BACKEND == 'networkmanager':
        return nm_connect(interface, ssid, password)
    if RESOLVED_BACKEND == 'wpa_supplicant':
        return wpa_connect(interface, ssid, password)
    return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='wifi backend disabled')


def windows_interfaces():
    try:
        import pywifi
        return pywifi.PyWiFi().interfaces()
    except Exception:
        return []


def windows_interface(preferred=None):
    interfaces = windows_interfaces()
    if preferred:
        for interface in interfaces:
            if interface.name() == preferred:
                return interface
    return interfaces[0] if interfaces else None


def windows_detect_interface(preferred=None):
    interface = windows_interface(preferred)
    return interface.name() if interface else None


def windows_current_ssid(interface=None):
    result = run(['netsh', 'wlan', 'show', 'interfaces'], 10)
    if result.returncode != 0:
        return ''
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(':')
        if separator and key.strip().lower() == 'ssid':
            return value.strip()
    return ''


def windows_rescan(interface=None):
    device = windows_interface(interface)
    if not device:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    try:
        device.scan()
        return subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
    except Exception as e:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr=str(e))


def windows_scan(interface=None):
    device = windows_interface(interface)
    if not device:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    try:
        from pywifi import const
        device.scan()
        time.sleep(2)
        best = {}
        for network in device.scan_results():
            ssid = str(getattr(network, 'ssid', '') or '').strip()
            if not ssid:
                continue
            signal = str(getattr(network, 'signal', '') or '')
            akm = getattr(network, 'akm', []) or []
            secure = 'yes' if any(value != const.AKM_TYPE_NONE for value in akm) else 'no'
            try:
                score = int(signal)
            except ValueError:
                score = -200
            if ssid not in best or score > best[ssid][0]:
                best[ssid] = (score, '%s:%s:%s' % (ssid, signal, secure))
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout='\n'.join(item[1] for item in best.values()), stderr=''
        )
    except Exception as e:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr=str(e))


def windows_connect(interface, ssid, password):
    device = windows_interface(interface)
    if not device:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    try:
        import pywifi
        from pywifi import const

        for existing in device.network_profiles():
            if existing.ssid == ssid:
                device.remove_network_profile(existing)
        device.disconnect()
        time.sleep(0.5)
        profile = pywifi.Profile()
        profile.ssid = ssid
        profile.auth = const.AUTH_ALG_OPEN
        if password:
            profile.akm = [const.AKM_TYPE_WPA2PSK]
            profile.cipher = const.CIPHER_TYPE_CCMP
            profile.key = password
        else:
            profile.akm = [const.AKM_TYPE_NONE]
            profile.cipher = const.CIPHER_TYPE_NONE
        profile = device.add_network_profile(profile)
        device.connect(profile)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
    except Exception as e:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr=str(e))


def nm_detect_interface(preferred=None):
    result = run(['nmcli', '-t', '-f', 'DEVICE,TYPE,STATE', 'device', 'status'], 10)
    devices = []
    for line in result.stdout.splitlines():
        parts = line.rsplit(':', 2)
        if len(parts) == 3 and parts[1] == 'wifi':
            devices.append((parts[0], parts[2]))
    if preferred and any(device == preferred for device, _ in devices):
        return preferred
    connected = next((device for device, state in devices if state.startswith('connected')), None)
    return connected or (devices[0][0] if devices else None)


def nm_current_ssid(interface):
    if not interface:
        return ''
    result = run(
        ['nmcli', '-t', '-f', 'GENERAL.CONNECTION', 'device', 'show', interface],
        8,
    )
    line = result.stdout.strip()
    return line.split(':', 1)[1].strip() if ':' in line else line


def nm_scan(interface=None):
    args = ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list']
    if interface:
        args.extend(['ifname', interface])
    return run(args, 20)


def nm_rescan(interface=None):
    args = ['nmcli', 'device', 'wifi', 'rescan']
    if interface:
        args.extend(['ifname', interface])
    return run(args, 25)


def nm_connect(interface, ssid, password):
    args = ['nmcli', 'device', 'wifi', 'connect', ssid]
    if password:
        args.extend(['password', password])
    if interface:
        args.extend(['ifname', interface])
    return run(args, 30)


def wpa_detect_interface(preferred=None):
    devices = wireless_interfaces()
    if preferred and os.path.exists('/sys/class/net/' + preferred):
        return preferred
    return devices[0] if devices else None


def ensure_wpa_supplicant(interface=None):
    if not shutil.which('wpa_supplicant') or not shutil.which('wpa_cli'):
        return False
    if not interface:
        interface = wpa_detect_interface()
    if not interface:
        return False
    wpa_link_up(interface)
    base = ['wpa_cli', '-i', interface, '-p', WPA_CTRL]
    if run(base + ['status'], 1).returncode == 0:
        return True
    now = time.time()
    if now - WPA_START_ATTEMPTS.get(interface, 0) < 15:
        return False
    WPA_START_ATTEMPTS[interface] = now
    os.makedirs(WPA_CTRL, exist_ok=True)
    try:
        os.remove(os.path.join(WPA_CTRL, interface))
    except FileNotFoundError:
        pass
    except Exception:
        pass
    conf = '/tmp/wpa_supplicant-%s.conf' % interface
    with open(conf, 'w') as f:
        f.write('ctrl_interface=%s\nctrl_interface_group=0\nupdate_config=1\nap_scan=1\n' % WPA_CTRL)
    run(['wpa_supplicant', '-B', '-i', interface, '-c', conf, '-D', 'nl80211,wext'], 5)
    for _ in range(12):
        if run(base + ['status'], 1).returncode == 0:
            return True
        time.sleep(0.25)
    return False


def wpa_link_up(interface):
    if not interface:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    result = run(['ip', 'link', 'set', interface, 'up'], 8)
    if result.returncode == 0:
        run(['iw', 'dev', interface, 'set', 'power_save', 'off'], 5)
    return result


def wpa_current_ssid(interface):
    if not interface:
        return ''
    wpa_link_up(interface)
    result = run(['wpa_cli', '-i', interface, '-p', WPA_CTRL, 'status'], 2)
    for line in result.stdout.splitlines():
        if line.startswith('ssid='):
            return line.split('=', 1)[1].strip()
    return ''


def wpa_parse_scan_results(result):
    networks = []
    for line in result.stdout.splitlines()[1:]:
        parts = line.split('\t')
        if len(parts) < 5:
            continue
        ssid = parts[4].strip()
        if not ssid:
            continue
        signal = parts[2].strip()
        flags = parts[3].upper()
        secure = 'yes' if any(token in flags for token in ('WPA', 'WEP', 'SAE')) else 'no'
        networks.append((ssid, signal, secure))
    return networks


def wpa_scan(interface=None):
    if not interface:
        interface = wpa_detect_interface()
    if not interface:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    ensure_wpa_supplicant(interface)
    link = wpa_link_up(interface)
    if link.returncode != 0:
        return link
    base = ['wpa_cli', '-i', interface, '-p', WPA_CTRL]
    run(base + ['scan'], 8)
    import time
    time.sleep(2)
    cli_result = run(base + ['scan_results'], 8)
    cli_networks = wpa_parse_scan_results(cli_result)
    if cli_networks:
        best = {}
        for ssid, signal, secure in cli_networks:
            try:
                score = int(signal) if signal else -200
            except ValueError:
                score = -200
            if ssid not in best or score > best[ssid][0]:
                best[ssid] = (score, '%s:%s:%s' % (ssid, signal, secure))
        return subprocess.CompletedProcess(
            args=base + ['scan_results'],
            returncode=cli_result.returncode,
            stdout='\n'.join(item[1] for item in best.values()),
            stderr=cli_result.stderr,
        )
    result = run(['iw', 'dev', interface, 'scan', 'ap-force'], 25)
    tries = 0
    while result.returncode != 0 and ('progress' in (result.stderr or '').lower() or 'busy' in (result.stderr or '').lower()) and tries < 4:
        import time
        time.sleep(2)
        result = run(['iw', 'dev', interface, 'scan', 'ap-force'], 25)
        tries += 1
    networks = []
    ssid = ''
    signal = ''
    secure = ''
    for line in result.stdout.splitlines():
        text = line.strip()
        if text.startswith('BSS '):
            if ssid:
                networks.append((ssid, signal, secure))
            ssid = ''
            signal = ''
            secure = ''
        elif text.startswith('SSID: '):
            ssid = text[6:].strip()
        elif text.startswith('signal:'):
            try:
                signal = str(int(float(text.split(':', 1)[1].split()[0])))
            except Exception:
                signal = ''
        elif text.startswith('capability:') and 'privacy' in text.lower():
            secure = 'yes'
        elif text.startswith('capability: ESS') and not secure:
            secure = 'no'
    if ssid:
        networks.append((ssid, signal, secure))
    best = {}
    for ssid, signal, secure in networks:
        try:
            score = int(signal) if signal else -200
        except ValueError:
            score = -200
        if ssid not in best or score > best[ssid][0]:
            best[ssid] = (score, '%s:%s:%s' % (ssid, signal, secure))
    out = '\n'.join(item[1] for item in best.values())
    return subprocess.CompletedProcess(args=['iw', 'scan'], returncode=result.returncode, stdout=out, stderr=result.stderr)


def wpa_rescan(interface=None):
    if not interface:
        interface = wpa_detect_interface()
    if not interface:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    ensure_wpa_supplicant(interface)
    link = wpa_link_up(interface)
    if link.returncode != 0:
        return link
    run(['wpa_cli', '-i', interface, '-p', WPA_CTRL, 'scan'], 8)
    return subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')


def wpa_value(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def wpa_connect(interface, ssid, password):
    if not interface:
        interface = wpa_detect_interface()
    if not interface:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout='', stderr='no iface')
    ensure_wpa_supplicant(interface)
    link = wpa_link_up(interface)
    if link.returncode != 0:
        return link
    base = ['wpa_cli', '-i', interface, '-p', WPA_CTRL]
    commands = [
        base + ['remove_network', 'all'],
        base + ['add_network'],
        base + ['set_network', '0', 'ssid', wpa_value(ssid)],
    ]
    if password:
        commands.append(base + ['set_network', '0', 'psk', wpa_value(password)])
    else:
        commands.append(base + ['set_network', '0', 'key_mgmt', 'NONE'])
    commands.extend([
        base + ['enable_network', '0'],
        base + ['select_network', '0'],
    ])
    last = subprocess.CompletedProcess(args=[], returncode=0, stdout='', stderr='')
    for command in commands:
        last = run(command, 6)
        if last.returncode != 0:
            return last
    return last


configure(os.environ.get('LUNA_WIFI_BACKEND') or os.environ.get('LUNA_WIFI_BACKEND_RESOLVED') or 'auto')
