import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'app'))

import wifi
from windows import launcher


class FakeProfile:
    def __init__(self, ssid=''):
        self.ssid = ssid
        self.auth = None
        self.akm = []
        self.cipher = None
        self.key = None


class FakeInterface:
    def __init__(self):
        self.profiles = [FakeProfile('Home WiFi'), FakeProfile('Luna Ultra TEST')]
        self.removed = []
        self.connected = None
        self.scanned = False

    def name(self):
        return 'Wi-Fi'

    def scan(self):
        self.scanned = True

    def scan_results(self):
        open_network = FakeProfile('Guest')
        open_network.signal = 31
        open_network.akm = [0]
        luna = FakeProfile('Luna Ultra TEST')
        luna.signal = 82
        luna.akm = [4]
        return [open_network, luna]

    def network_profiles(self):
        return list(self.profiles)

    def remove_network_profile(self, profile):
        self.removed.append(profile.ssid)

    def disconnect(self):
        pass

    def add_network_profile(self, profile):
        return profile

    def connect(self, profile):
        self.connected = profile


def fake_pywifi_module():
    module = types.ModuleType('pywifi')
    module.Profile = FakeProfile
    module.const = types.SimpleNamespace(
        AUTH_ALG_OPEN=0,
        AKM_TYPE_NONE=0,
        AKM_TYPE_WPA2PSK=4,
        CIPHER_TYPE_NONE=0,
        CIPHER_TYPE_CCMP=3,
    )
    return module


class WindowsWifiTests(unittest.TestCase):
    def test_networkmanager_current_ssid_uses_active_wifi_not_profile_name(self):
        active = subprocess.CompletedProcess([], 0, '*:Luna Ultra TEST\n', '')
        with patch.object(wifi, 'run', return_value=active):
            self.assertEqual(wifi.nm_current_ssid('wlan0'), 'Luna Ultra TEST')

    def test_current_ssid_ignores_bssid(self):
        output = '\n    BSSID             : 00:11:22:33:44:55\n    SSID              : Luna Ultra TEST\n'
        result = subprocess.CompletedProcess([], 0, output, '')
        with patch.object(wifi, 'run', return_value=result):
            self.assertEqual(wifi.windows_current_ssid('Wi-Fi'), 'Luna Ultra TEST')

    def test_scan_normalizes_windows_results(self):
        device = FakeInterface()
        module = fake_pywifi_module()
        with patch.dict(sys.modules, {'pywifi': module}), patch.object(wifi, 'windows_interface', return_value=device), patch.object(wifi.time, 'sleep'):
            result = wifi.windows_scan('Wi-Fi')
        self.assertEqual(result.returncode, 0)
        self.assertIn('Guest:31:no', result.stdout)
        self.assertIn('Luna Ultra TEST:82:yes', result.stdout)

    def test_connect_replaces_only_matching_profile(self):
        device = FakeInterface()
        module = fake_pywifi_module()
        with patch.dict(sys.modules, {'pywifi': module}), patch.object(wifi, 'windows_interface', return_value=device), patch.object(wifi.time, 'sleep'):
            result = wifi.windows_connect('Wi-Fi', 'Luna Ultra TEST', 'password')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(device.removed, ['Luna Ultra TEST'])
        self.assertEqual(device.connected.ssid, 'Luna Ultra TEST')
        self.assertEqual(device.connected.key, 'password')


class WindowsLauncherTests(unittest.TestCase):
    def test_first_run_config_uses_user_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / 'data' / 'config.json'
            downloads = root / 'Videos' / 'Luna Sync'
            state = root / 'data' / 'state'
            config = launcher.ensure_config(config_path, downloads, state)
            saved = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertEqual(config, saved)
            self.assertEqual(saved['wifi_backend'], 'windows')
            self.assertEqual(Path(saved['download_dir']), downloads)
            self.assertTrue(downloads.is_dir())
            self.assertTrue(state.is_dir())


if __name__ == '__main__':
    unittest.main()
