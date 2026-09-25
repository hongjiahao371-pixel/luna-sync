import importlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest


REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))


class ComplianceRound4Tests(unittest.TestCase):
    """绿联 3.0 审核后续迭代 9 条的整改回归。

    1 thumb 软链任意读 / 2 投诉举报渠道 / 3 运营者信息 / 4 双清单 /
    5 用户权利及行权途径 / 6 跨境与儿童说明 / 7 LunaClient 一设备一认证 /
    8 wifi 密码加密存储 / 9 商店版移除 wifi 表单收集
    """

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
            'web_port': 18769,
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
        assert login.status_code == 200, 'round4 test login failed'

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def accept_privacy(self):
        with self.web_app.lk:
            self.web_app.ST['privacy_version'] = self.web_app.PRIVACY_VERSION
        self.web_app.save_settings({'privacy_version': self.web_app.PRIVACY_VERSION})

    # ---- 1. thumb/img 不得经符号链接读取目录外文件 ----

    def test_symlink_escape_is_rejected(self):
        app = self.web_app
        secret = os.path.join(self.tempdir.name, 'outside-secret.txt')
        with open(secret, 'w') as f:
            f.write('TOP-SECRET-CONTENT')
        os.makedirs(os.path.join(app.DLDIR, 'internal'), exist_ok=True)
        link = os.path.join(app.DLDIR, 'internal', 'evil.jpg')
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(secret, link)
        try:
            self.accept_privacy()
            # the scanner must not list symlinked entries at all
            self.assertNotIn(os.path.join('internal', 'evil.jpg'), app.local_files())
            # direct access must be rejected, never resolved outside DLDIR
            img = self.client.get('/img/internal/evil.jpg')
            self.assertEqual(img.status_code, 400)
            self.assertNotIn(b'TOP-SECRET-CONTENT', img.get_data())
            thumb = self.client.get('/thumb/internal/evil.jpg')
            self.assertEqual(thumb.status_code, 400)
            self.assertNotIn(b'TOP-SECRET-CONTENT', thumb.get_data())
            # traversal via a name is still rejected as before
            self.assertEqual(self.client.get('/img/internal/../../../outside-secret.txt').status_code, 400)
        finally:
            if os.path.lexists(link):
                os.remove(link)

    def test_symlinked_directory_is_not_followed_by_scanner(self):
        app = self.web_app
        outside = os.path.join(self.tempdir.name, 'outside-dir')
        os.makedirs(outside, exist_ok=True)
        with open(os.path.join(outside, 'leak.jpg'), 'w') as f:
            f.write('LEAK')
        link = os.path.join(app.DLDIR, 'linked-dir')
        if os.path.lexists(link):
            os.remove(link)
        os.symlink(outside, link)
        try:
            self.assertNotIn('linked-dir/leak.jpg', app.local_files())
        finally:
            if os.path.lexists(link):
                os.remove(link)

    # ---- 8. wifi 密码加密存储 ----

    def test_saved_wifi_is_encrypted_at_rest(self):
        app = self.web_app
        self.accept_privacy()
        if os.path.exists(app.WIFI_FILE):
            os.remove(app.WIFI_FILE)
        try:
            app.save_wifi('Luna Ultra TEST', 'PLAIN-WIFI-PASS')
            self.assertTrue(os.path.exists(app.WIFI_FILE))
            raw = open(app.WIFI_FILE).read()
            self.assertNotIn('PLAIN-WIFI-PASS', raw, 'wifi.json must not store the plaintext password')
            self.assertIn('enc:v1:', raw)
            self.assertEqual(app.load_saved_wifi()['password'], 'PLAIN-WIFI-PASS')
        finally:
            if os.path.exists(app.WIFI_FILE):
                os.remove(app.WIFI_FILE)

    def test_secret_roundtrip_and_tamper(self):
        app = self.web_app
        token = app.encrypt_secret('another-pass')
        self.assertTrue(token.startswith('enc:v1:'))
        self.assertEqual(app.decrypt_secret(token), 'another-pass')
        self.assertNotIn('another-pass', token)
        self.assertEqual(app.decrypt_secret(token[:-6] + 'AAAAAA'), '',
                         'tampered ciphertext must not decrypt')
        self.assertEqual(app.decrypt_secret(''), '')
        self.assertEqual(app.decrypt_secret('legacy-plaintext'), 'legacy-plaintext',
                         'old plaintext values must still load (migration path)')

    def test_legacy_plaintext_wifi_file_still_loads(self):
        app = self.web_app
        self.accept_privacy()
        os.makedirs(app.STATE_DIR, exist_ok=True)
        with open(app.WIFI_FILE, 'w') as f:
            json.dump({'ssid': 'OldNet', 'password': 'OLD-PLAIN'}, f)
        try:
            loaded = app.load_saved_wifi()
            self.assertEqual(loaded, {'ssid': 'OldNet', 'password': 'OLD-PLAIN'})
        finally:
            if os.path.exists(app.WIFI_FILE):
                os.remove(app.WIFI_FILE)

    # ---- 7. LunaClient 一设备一认证 ----

    def test_camera_auth_is_persisted_per_device(self):
        app = self.web_app
        # earlier test classes may have cleaned the shared temp dir; regenerate
        # deterministically and verify the per-install config round-trips
        if os.path.exists(app.CAMERA_AUTH_FILE):
            os.remove(app.CAMERA_AUTH_FILE)
        payloads = app.load_camera_auth()
        self.assertTrue(os.path.exists(app.CAMERA_AUTH_FILE),
                        'first run must write a per-device camera auth config')
        with open(app.CAMERA_AUTH_FILE) as f:
            data = json.load(f)
        self.assertEqual([bytes.fromhex(h) for h in data['payloads']], payloads)
        self.assertEqual(payloads, app.CAMERA_CLIENT.auth_payloads)
        mode = os.stat(app.CAMERA_AUTH_FILE).st_mode & 0o777
        self.assertLessEqual(mode, 0o600, 'camera auth config must not be world readable')

    def test_camera_auth_env_override(self):
        app = self.web_app
        blob = 'aabbccdd'
        os.environ['LUNA_CAMERA_AUTH'] = blob
        try:
            self.assertEqual(app.load_camera_auth(), [bytes.fromhex(blob)])
        finally:
            os.environ.pop('LUNA_CAMERA_AUTH', None)
        self.assertEqual(app.load_camera_auth(), app.CAMERA_CLIENT.auth_payloads)

    def test_auth_session_accepts_injected_payloads(self):
        import luna_client
        session = luna_client.LunaAuthSession(payloads=[b'\x01\x02'])
        self.assertEqual(session.payloads, [b'\x01\x02'])
        default = luna_client.LunaAuthSession()
        self.assertEqual(default.payloads, [bytes(p) for p in luna_client.AUTH_PAYLOADS])

    # ---- 9. 商店版移除 wifi 表单收集（后端门禁） ----

    def test_store_mode_disables_wifi_collection(self):
        app = self.web_app
        self.accept_privacy()
        original = app.STORE_WIFI_LOCKED
        if os.path.exists(app.WIFI_FILE):
            os.remove(app.WIFI_FILE)
        try:
            app.STORE_WIFI_LOCKED = True
            self.assertEqual(self.client.get('/api/wifi/scan').status_code, 403)
            self.assertEqual(self.client.post('/api/wifi/connect', json={'ssid': 'x'}).status_code, 403)
            self.assertEqual(self.client.post('/api/wifi/forget').status_code, 403)
            app.save_wifi('SomeNet', 'pass')
            self.assertFalse(os.path.exists(app.WIFI_FILE),
                             'store mode must never persist wifi credentials')
        finally:
            app.STORE_WIFI_LOCKED = original
        # self-hosted behaviour unchanged
        self.assertEqual(self.client.get('/api/wifi/scan').status_code, 503)  # no iface in tests
        self.assertEqual(self.client.post('/api/wifi/forget').status_code, 200)

    # ---- 5. 撤回同意（行权途径真实有效） ----

    def test_privacy_withdraw_resets_to_first_run(self):
        app = self.web_app
        self.accept_privacy()
        os.makedirs(os.path.join(app.DLDIR, 'internal'), exist_ok=True)
        media = os.path.join(app.DLDIR, 'internal', 'keep-me.jpg')
        with open(media, 'w') as f:
            f.write('user media')
        app.save_wifi('Luna Ultra TEST', 'WIFI-FOR-WITHDRAW')
        try:
            response = self.client.post('/api/privacy/withdraw')
            self.assertEqual(response.status_code, 200)
            with app.lk:
                self.assertEqual(app.ST['privacy_version'], '')
                self.assertIsNone(app.ST['wifi_password'])
            self.assertFalse(app.privacy_accepted())
            self.assertFalse(os.path.exists(app.WIFI_FILE), 'withdraw must clear saved wifi')
            self.assertFalse(os.path.exists(app.PICKS_FILE), 'withdraw must clear app-processed records')
            self.assertTrue(os.path.exists(media), 'user media in DLDIR must survive withdrawal')
            # sessions and the web password are gone: next request re-authenticates
            self.assertEqual(self.client.get('/api/files').status_code, 401)
            guest = app.app.test_client()
            self.assertFalse(guest.get('/api/auth-state').get_json()['password_set'])
        finally:
            if os.path.exists(media):
                os.remove(media)
            record = app.hash_password('test-pass')
            app.SETTINGS['web_password'] = record
            app.save_settings({'web_password': record})
            self.accept_privacy()
            self.client.post('/api/auth/login', json={'password': 'test-pass'})

    # ---- 2/3/4/6. 隐私政策文本（运营者/渠道/双清单/权利/跨境/儿童） ----

    def test_privacy_md_documents_required_sections(self):
        with open(os.path.join(REPO, 'PRIVACY.md')) as f:
            text = f.read()
        for marker in ('运营者信息', '个人信息保护负责人', '已收集个人信息清单',
                       '与第三方共享个人信息清单', '用户权利及行权途径', '撤销同意',
                       '跨境传输', '儿童信息', '投诉与举报渠道', '15 个工作日'):
            self.assertIn(marker, text, 'PRIVACY.md missing section: ' + marker)
        self.assertIn('1981940361@qq.com', text, 'complaint email channel must be present')

    def test_legal_template_renders_new_sections_and_withdraw(self):
        page = self.client.get('/privacy')
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        for marker in ('运营者信息', '已收集个人信息清单', '与第三方共享个人信息清单',
                       '用户权利及行权途径', '跨境传输说明', '儿童信息说明',
                       '投诉与举报渠道', '/api/privacy/withdraw', '撤回授权'):
            self.assertIn(marker, body, 'privacy page missing: ' + marker)
        self.assertIn('2026年9月25日', body)
        terms = self.client.get('/terms')
        self.assertEqual(terms.status_code, 200)
        self.assertIn('用户协议', terms.get_data(as_text=True))

    def test_privacy_version_bumped(self):
        self.assertEqual(self.web_app.PRIVACY_VERSION, '2026-09-25')
        self.assertTrue(re.match(r'^\d{4}-\d{2}-\d{2}$', self.web_app.PRIVACY_VERSION))

    def test_consent_dialog_has_store_wording(self):
        with open(os.path.join(REPO, 'app', 'templates', 'index.html')) as f:
            text = f.read()
        self.assertIn('consentWifiStore', text)
        self.assertIn('商店版不在应用内收集 Wi-Fi 信息', text)
        self.assertIn("tr(guided?'consentWifiStore':'consentWifi')", text)


if __name__ == '__main__':
    unittest.main()
