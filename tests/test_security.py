import importlib
import importlib.util
import json
import os
import ssl
import sys
import tempfile
import unittest


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

UPK_COMPOSE = os.path.join('upk', 'luna-sync', 'rootfs_common', 'docker-compose.yaml')
SELFHOST_COMPOSES = ['docker-compose.yml', 'docker-compose.hub.yml']


class DeploymentHardeningTests(unittest.TestCase):
    def test_upk_compose_is_hardened_for_store(self):
        # UGOS store package: bridge network, no privileges, guidance mode.
        with open(os.path.join(REPO, UPK_COMPOSE)) as compose_file:
            text = compose_file.read()
        self.assertNotIn('network_mode', text, 'upk must not configure network_mode')
        self.assertNotIn('privileged', text, 'upk must not run privileged')
        self.assertNotIn('pid:', text, 'upk must not share host PID namespace')
        self.assertNotRegex(text, r'capabilities|CAP_', 'upk must not add capabilities')
        self.assertIn('expose:', text)
        self.assertNotRegex(text, r'8766:8766', 'upk web port must stay inside the compose network')
        self.assertIn('LUNA_WIFI_GUIDANCE=ugos', text,
                      'upk must point users at UGOS Wi-Fi settings instead of in-app controls')

    def test_selfhost_compose_keeps_wifi_takeover(self):
        # Self-hosted deployments intentionally keep host networking and
        # privileged mode so the app can drive the wireless NIC (wpa/NM).
        for rel in SELFHOST_COMPOSES:
            with self.subTest(file=rel):
                with open(os.path.join(REPO, rel)) as compose_file:
                    text = compose_file.read()
                self.assertIn('network_mode: host', text)
                self.assertIn('privileged: true', text)
                self.assertNotIn('LUNA_WIFI_GUIDANCE', text,
                                 'self-hosted form must keep the in-app Wi-Fi controls')

    def test_dockerfile_ships_tls_prerequisites(self):
        with open(os.path.join(REPO, 'Dockerfile')) as f:
            text = f.read()
        self.assertIn('python3-cryptography', text)
        self.assertIn('openssl', text)


class SecurityFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if importlib.util.find_spec('flask') is None:
            raise unittest.SkipTest('Flask is not installed in this Python environment')
        cls.tempdir = tempfile.TemporaryDirectory()
        root = cls.tempdir.name
        config = {
            'camera_host': '192.0.2.1',
            'camera_ssid': '',
            'camera_password': '',
            'wifi_backend': 'none',
            'wifi_iface': None,
            'download_dir': os.path.join(root, 'downloads'),
            'state_dir': os.path.join(root, 'state'),
            'web_port': 18768,
        }
        config_path = os.path.join(root, 'config.json')
        with open(config_path, 'w') as f:
            json.dump(config, f)
        os.environ['LUNA_CONFIG'] = config_path
        os.environ['DOWNLOAD_DIR'] = config['download_dir']
        os.environ['STATE_DIR'] = config['state_dir']
        os.environ['LUNA_WIFI_BACKEND'] = 'none'
        app_dir = os.path.abspath(os.path.join(REPO, 'app'))
        if app_dir not in sys.path:
            sys.path.insert(0, app_dir)
        cls.web_app = importlib.import_module('web_app')
        if not cls.web_app.SETTINGS.get('web_password'):
            cls.web_app.SETTINGS['web_password'] = cls.web_app.hash_password('test-pass')
        cls.client = cls.web_app.app.test_client()
        login = cls.client.post('/api/auth/login', json={'password': 'test-pass'})
        assert login.status_code == 200, 'security test login failed'

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def setUp(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = ''
        self.web_app.TLS_ENABLED = False

    def tearDown(self):
        self.web_app.TLS_ENABLED = False

    def test_decline_page_is_public_without_consent_or_login(self):
        guest = self.web_app.app.test_client()
        page = guest.get('/declined')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn('重新阅读协议', body)
        self.assertIn('Review again', body)

    def test_privacy_api_offers_a_decline_url(self):
        data = self.client.get('/api/privacy').get_json()
        self.assertEqual(data['declined_url'], '/declined')

    def test_consent_dialog_shows_equal_decline_option(self):
        home = self.client.get('/')
        self.assertEqual(home.status_code, 200)
        body = home.get_data(as_text=True)
        self.assertIn('consentDecline', body)
        self.assertIn('/declined', body)
        self.assertIn('不同意并退出', body)
        self.assertIn('Decline and exit', body)

    def test_main_ui_has_always_visible_legal_entry(self):
        home = self.client.get('/')
        body = home.get_data(as_text=True)
        self.assertIn('bLegal', body)
        self.assertIn('legalFrame', body)
        self.assertIn("openLegal('privacy')", body)
        self.assertIn("openLegal('terms')", body)

    def test_login_page_links_legal_documents(self):
        login = self.client.get('/login')
        body = login.get_data(as_text=True)
        self.assertIn('/privacy', body)
        self.assertIn('/terms', body)

    def test_self_signed_certificate_is_generated_and_loadable(self):
        cert_path = os.path.join(self.web_app.TLS_DIR, 'test-cert.pem')
        key_path = os.path.join(self.web_app.TLS_DIR, 'test-key.pem')
        for path in (cert_path, key_path):
            if os.path.exists(path):
                os.remove(path)
        try:
            self.assertTrue(self.web_app.generate_self_signed_certificate(cert_path, key_path))
            self.assertLess(os.stat(key_path).st_mode & 0o777, 0o700, 'private key must not be group/world readable')
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert_path, key_path)
            pem = open(cert_path).read()
            self.assertIn('BEGIN CERTIFICATE', pem)
        finally:
            for path in (cert_path, key_path):
                if os.path.exists(path):
                    os.remove(path)

    def test_tls_off_disables_tls(self):
        os.environ['LUNA_TLS'] = 'off'
        try:
            self.assertIsNone(self.web_app.build_ssl_context())
        finally:
            os.environ.pop('LUNA_TLS', None)

    def test_tls_enabled_sets_secure_cookie_and_hsts(self):
        self.web_app.TLS_ENABLED = True
        try:
            login = self.client.post('/api/auth/login', json={'password': 'test-pass'})
            set_cookie = login.headers.get('Set-Cookie', '')
            self.assertIn('Secure', set_cookie)
            self.assertIn('HttpOnly', set_cookie)
            page = self.client.get('/privacy', base_url='https://localhost')
            self.assertEqual(page.headers.get('Strict-Transport-Security'), 'max-age=31536000')
        finally:
            self.web_app.TLS_ENABLED = False
            guest = self.web_app.app.test_client()
            guest.post('/api/auth/login', json={'password': 'test-pass'})

    def test_http_mode_keeps_cookie_without_secure_flag(self):
        login = self.client.post('/api/auth/login', json={'password': 'test-pass'})
        set_cookie = login.headers.get('Set-Cookie', '')
        self.assertNotIn('Secure', set_cookie)

    def test_state_reports_wifi_guidance(self):
        data = self.client.get('/api/state').get_json()
        self.assertIn('wifi_guidance', data)
        self.assertEqual(data['wifi_guidance'], '',
                         'docker deployments must keep the in-app Wi-Fi controls')

    def test_decline_survives_router_for_index_route(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = ''
        response = self.client.get('/api/state')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['privacy_accepted'])


if __name__ == '__main__':
    unittest.main()
