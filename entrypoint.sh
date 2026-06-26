#!/bin/bash
set -e

CONFIG="${LUNA_CONFIG:-/app/config.json}"

eval "$(python3 - "$CONFIG" <<'PY'
import json, os, shlex, sys

config_path = sys.argv[1]
cfg = {}
try:
    with open(config_path) as f:
        cfg = json.load(f)
except Exception:
    pass

backend = os.environ.get('LUNA_WIFI_BACKEND') or cfg.get('wifi_backend', 'auto')
iface = os.environ.get('LUNA_WIFI_IFACE') or cfg.get('wifi_iface') or ''
wpa_ctrl = os.environ.get('LUNA_WPA_CTRL') or cfg.get('wpa_ctrl', '/run/wpa_supplicant')

def emit(name, value):
    print('%s=%s' % (name, shlex.quote(str(value))))

emit('CFG_BACKEND', backend)
emit('CFG_IFACE', iface)
emit('CFG_WPA_CTRL', wpa_ctrl)
PY
)"

resolve_backend() {
    local backend="$1"
    case "$backend" in
        nm|network-manager) backend="networkmanager" ;;
        wpa|wpasupplicant) backend="wpa_supplicant" ;;
        manual|off|disabled) backend="none" ;;
    esac
    if [ "$backend" != "auto" ]; then
        printf '%s\n' "$backend"
        return
    fi
    if command -v nmcli >/dev/null 2>&1 && [ -d /run/NetworkManager ] && [ -e /run/dbus/system_bus_socket ]; then
        printf '%s\n' "networkmanager"
        return
    fi
    if command -v iw >/dev/null 2>&1 && command -v wpa_cli >/dev/null 2>&1; then
        for p in /sys/class/net/*/wireless; do
            [ -e "$p" ] && printf '%s\n' "wpa_supplicant" && return
        done
    fi
    printf '%s\n' "none"
}

detect_iface() {
    if [ -n "$CFG_IFACE" ]; then
        printf '%s\n' "$CFG_IFACE"
        return
    fi
    for p in /sys/class/net/*/wireless; do
        [ -e "$p" ] && basename "$(dirname "$p")" && return
    done
}

BACKEND="$(resolve_backend "$CFG_BACKEND")"
export LUNA_WIFI_BACKEND_RESOLVED="$BACKEND"
export LUNA_WPA_CTRL="$CFG_WPA_CTRL"

echo "[entrypoint] wifi backend: $BACKEND"

if [ "$BACKEND" = "wpa_supplicant" ]; then
    IFACE="$(detect_iface)"
    if [ -z "$IFACE" ]; then
        echo "[entrypoint] WARNING: no wireless interface found; wifi features may fail"
    else
        export LUNA_WIFI_IFACE="$IFACE"
        echo "[entrypoint] wireless interface: $IFACE"
        ip link set "$IFACE" up 2>/dev/null || echo "[entrypoint] warn: cannot set $IFACE up"
        pkill wpa_supplicant 2>/dev/null || true
        rm -f "$CFG_WPA_CTRL/$IFACE" 2>/dev/null || true
        mkdir -p "$CFG_WPA_CTRL"
        WPA_CONF=/tmp/wpa_supplicant.conf
        cat > "$WPA_CONF" <<EOF
ctrl_interface=$CFG_WPA_CTRL
ctrl_interface_group=0
update_config=1
ap_scan=1
EOF
        echo "[entrypoint] starting wpa_supplicant on $IFACE ..."
        wpa_supplicant -B -i "$IFACE" -c "$WPA_CONF" -D nl80211,wext 2>&1 | head -10 || true
        for i in $(seq 1 20); do
            if wpa_cli -i "$IFACE" -p "$CFG_WPA_CTRL" status >/dev/null 2>&1; then
                echo "[entrypoint] wpa_supplicant ready"
                break
            fi
            sleep 0.5
        done
        wpa_cli -i "$IFACE" -p "$CFG_WPA_CTRL" status 2>&1 | head -8 || true
    fi
fi

exec python3 -u /app/web_app.py
