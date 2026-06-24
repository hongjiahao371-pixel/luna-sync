import subprocess


def run(args, timeout=30):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def detect_interface(preferred=None):
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


def current_ssid(interface):
    if not interface:
        return ''
    result = run(
        ['nmcli', '-t', '-f', 'GENERAL.CONNECTION', 'device', 'show', interface],
        8,
    )
    line = result.stdout.strip()
    return line.split(':', 1)[1].strip() if ':' in line else line


def scan(interface=None):
    args = ['nmcli', '-t', '-f', 'SSID,SIGNAL,SECURITY', 'device', 'wifi', 'list']
    if interface:
        args.extend(['ifname', interface])
    return run(args, 20)


def rescan(interface=None):
    args = ['nmcli', 'device', 'wifi', 'rescan']
    if interface:
        args.extend(['ifname', interface])
    return run(args, 25)


def connect(interface, ssid, password):
    args = ['nmcli', 'device', 'wifi', 'connect', ssid]
    if password:
        args.extend(['password', password])
    if interface:
        args.extend(['ifname', interface])
    return run(args, 30)
