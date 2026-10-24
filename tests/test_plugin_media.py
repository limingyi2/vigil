import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

BRAIN = Path(__file__).resolve().parents[1] / 'brain'
sys.path.insert(0, str(BRAIN))
from plugin_files import send_file
import plugin_host
import server_launch


class Handler:
    def __init__(self, range_value=''):
        self.headers = {'Range': range_value}
        self.response_headers = {}
        self.wfile = io.BytesIO()
    def send_response(self, status): self.status = status
    def send_header(self, name, value): self.response_headers[name] = value
    def end_headers(self): pass
    def _cors(self): pass


class TestPluginResource(unittest.TestCase):
    def test_full_and_seek_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'video.mp4'
            file.write_bytes(b'0123456789')
            for requested, status, expected in [('', 200, b'0123456789'),
                    ('bytes=2-5', 206, b'2345'), ('bytes=7-', 206, b'789'),
                    ('bytes=-3', 206, b'789'), ('bytes=20-', 416, b''),
                    ('bytes=5-2', 416, b''), ('bytes=-0', 416, b'')]:
                with self.subTest(requested=requested):
                    handler = Handler(requested)
                    send_file(handler, file, 'video/mp4')
                    self.assertEqual(handler.status, status)
                    self.assertEqual(handler.wfile.getvalue(), expected)
                    self.assertEqual(int(handler.response_headers['Content-Length']), len(expected))

    def test_wallpaper_nested_assets_and_traversal(self):
        spec = importlib.util.spec_from_file_location('wallpaper_test', BRAIN.parent / 'market/plugins/wallpaper/__init__.py')
        wallpaper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(wallpaper)
        with tempfile.TemporaryDirectory() as tmp, patch.object(wallpaper, '_find_workshop', return_value=tmp):
            asset = Path(tmp) / '123/assets/main.css'
            asset.parent.mkdir(parents=True)
            asset.write_text('body {}')
            result = wallpaper.api_resource({}, {'id': '123', 'file': 'assets/main.css'})
            self.assertEqual(Path(result['__file__']), asset.resolve())
            for query in [{'id': '../', 'file': 'x'}, {'id': '123', 'file': '../../x'},
                          {'id': '123', 'file': str(Path(tmp).parent / 'outside')}]:
                self.assertEqual(wallpaper.api_resource({}, query)['_code'], 404)
            self.assertEqual(wallpaper.api_resource({}, {'id': '../', 'file': 'preview.jpg'})['_code'], 404)

    def test_frontend_version_is_stable_and_changes_with_content_or_enable(self):
        plugin = type('Plugin', (), {'name': 'x', 'feature': '', 'frontend_js': 'abc', 'frontend_css': ''})()
        with patch.object(plugin_host, 'all_plugins', return_value={'x': plugin}):
            first = plugin_host.frontend_assets()['version']
            self.assertEqual(first, plugin_host.frontend_assets()['version'])
            plugin.frontend_js = 'def'
            self.assertNotEqual(first, plugin_host.frontend_assets()['version'])
            plugin.feature = 'x'
            with patch('features.all_features', return_value={'x': False}):
                self.assertEqual(plugin_host.frontend_assets()['plugins'], [])
                self.assertNotEqual(first, plugin_host.frontend_assets()['version'])

    def test_seed_core_features_and_migrate_legacy_plugins_without_bundling_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, home = Path(tmp) / 'code', Path(tmp) / 'home'
            (code / 'core_features').mkdir(parents=True)
            (code / 'core_features/calc.py').write_text('core')
            # Simulate an old build carrying plugin payloads: the new seeder must ignore it.
            (code / 'plugins/wallpaper').mkdir(parents=True)
            (code / 'plugins/wallpaper/frontend.js').write_text('must not be copied')
            (home / 'plugins').mkdir(parents=True)
            (home / 'plugins/existing.py').write_text('custom')
            with patch.multiple(server_launch, FROZEN=True, CODE_DIR=str(code), HOME_DIR=str(home)):
                server_launch._seed_core_features()
                self.assertEqual((home / 'core_features/calc.py').read_text(), 'core')
                self.assertFalse((home / 'plugins/wallpaper').exists())
                self.assertEqual((home / 'plugins/existing.py').read_text(), 'custom')
                self.assertEqual((home / 'data/installed_plugins/existing.py').read_text(), 'custom')


if __name__ == '__main__':
    unittest.main()
