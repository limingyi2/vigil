"""Fault injection against temporary homes, never production plugins/config."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import paths
import plugin_ops as ops
import plugin_transaction as tx
from plugins import plugin_market as market


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='vigil-p4-3-')
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.plugins = self.home / 'plugins'
        self.plugins.mkdir()
        self.config = self.home / 'config.json'
        self.config.write_text(json.dumps({'features_by_src': {'pc': {'demo': False}},
                                          'user_value': 'preserve'}))
        self.config_before = self.config.read_bytes()
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        self.registry = self.plugins / '_registry.json'
        self.patches = [patch.object(ops, '_PLUG_DIR', str(self.plugins)),
                        patch.object(ops, 'REGISTRY', str(self.registry)),
                        patch.object(paths, 'CONFIG_PATH', str(self.config)),
                        patch.object(paths, 'DATA_DIR', str(self.home / 'data')),
                        patch.object(market, '_repo_path', return_value=str(self.repo))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def archive(self, value='new', manifest=None, filename='demo.py', extra=None):
        archive = self.home / 'package.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr(filename, 'NAME="demo"\nDESC="demo"\nVALUE=%r\n' % value)
            if manifest is not None:
                z.writestr('zxplugin.json', json.dumps(manifest))
            for name, content in (extra or {}).items():
                z.writestr(name, content)
        return str(archive)

    def source(self, value='new', manifest=None):
        source = self.repo / 'plugins' / 'demo'
        source.mkdir(parents=True, exist_ok=True)
        (source / '__init__.py').write_text('NAME="demo"\nDESC="demo"\nVALUE=%r\n' % value)
        if manifest is not None:
            (source / 'zxplugin.json').write_text(json.dumps(manifest))
        return source

    def market_install(self, relative='plugins/demo', name='demo'):
        with patch.object(market, '_read_market', return_value={
            'plugins': [{'name': name, 'path': relative}]}), \
             patch.object(market, '_reload_plugins', side_effect=AssertionError('must not execute')):
            return market.api_install({'name': name}, {})

    def old(self, directory=False):
        target = self.plugins / ('demo' if directory else 'demo.py')
        if directory:
            target.mkdir()
            (target / '__init__.py').write_text('NAME="demo"\nVALUE="old"\n')
        else:
            target.write_text('NAME="demo"\nVALUE="old"\n')
            (self.plugins / '_demo_old.txt').write_text('old-extra')
        self.registry.write_text('{"version":1,"plugins":{"demo":{"version":"old"},"other":{"x":1}}}')
        return target

    def state(self):
        return {str(p.relative_to(self.plugins)): p.read_bytes()
                for p in self.plugins.rglob('*') if p.is_file()}

    def test_zip_first_install_success_deferred_and_no_code_executed(self):
        archive = self.archive(extra={'notes.txt': 'hello'})
        ok, result = ops.install(archive)
        self.assertTrue(ok, result)
        self.assertTrue(result['static_validated'])
        self.assertTrue(result['files_installed'])
        self.assertFalse(result['dynamic_loaded'])
        self.assertTrue(result['restart_needed'])
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertTrue((self.plugins / '_demo_notes.txt').is_file())

    def test_directory_zip_installs_frontend_assets_transactionally(self):
        archive = self.home / 'directory.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('demo/__init__.py', 'NAME="demo"\nDESC="demo"\n')
            z.writestr('demo/frontend.js', 'window.VigilPlugin = {};\n')
            z.writestr('demo/frontend.css', '.demo {}\n')
            z.writestr('demo/zxplugin.json', json.dumps({'name': 'demo', 'version': '1.0.0'}))
        ok, result = ops.install(str(archive))
        self.assertTrue(ok, result)
        self.assertEqual(result['layout'], 'directory')
        self.assertTrue(result['frontend_assets'])
        self.assertTrue(result['restart_needed'])
        self.assertEqual((self.plugins / 'demo' / 'frontend.js').read_text(),
                         'window.VigilPlugin = {};\n')

    def test_directory_zip_registry_failure_restores_exact_old_package(self):
        old = self.old(directory=True)
        before = self.state()
        archive = self.home / 'directory.zip'
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('demo/__init__.py', 'NAME="demo"\nDESC="demo"\nVALUE="new"\n')
            z.writestr('demo/frontend.js', 'window.newVersion = true;\n')
        with patch.object(ops, 'write_registry', return_value=False):
            ok, result = ops.install(str(archive))
        self.assertFalse(ok)
        self.assertEqual(result['rollback'], 'restored')
        self.assertEqual(self.state(), before)

    def test_top_level_side_effect_never_runs(self):
        marker = self.home / 'executed'
        archive = self.archive()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('demo.py', 'NAME="demo"\nDESC="demo"\nopen(%r,"w").write("bad")\ndef start(brain): pass\n' % str(marker))
        self.assertTrue(ops.install(archive)[0])
        self.assertFalse(marker.exists())

    def test_zip_first_install_failure_no_residue(self):
        with patch.object(ops, 'write_registry', return_value=False):
            ok, result = ops.install(self.archive())
        self.assertFalse(ok)
        self.assertEqual(result['rollback'], 'restored')
        self.assertEqual(self.state(), {})

    def test_zip_update_registry_failure_restores_exact_code_extras_registry(self):
        self.old()
        before = self.state()
        with patch.object(ops, 'write_registry', return_value=False):
            ok, result = ops.install(self.archive(extra={'notes.txt': 'new-extra'}))
        self.assertFalse(ok)
        self.assertEqual(result['rollback'], 'restored')
        self.assertEqual(self.state(), before)
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_zip_update_replace_failure_restores_old(self):
        self.old()
        before = self.state()
        original = os.replace
        def fail(source, target):
            source_path = Path(source)
            if source_path.name == 'demo.py' and source_path.parent.name == 'demo':
                raise OSError('injected replacement failure')
            return original(source, target)
        with patch.object(tx.os, 'replace', side_effect=fail):
            ok, result = ops.install(self.archive())
        self.assertFalse(ok)
        self.assertEqual(self.state(), before)

    def test_market_first_install_failure_no_residue(self):
        self.source()
        with patch.object(ops, 'write_registry', return_value=False):
            result = self.market_install()
        self.assertFalse(result['ok'])
        self.assertEqual(self.state(), {})

    def test_market_update_failure_restores_exact_old_directory(self):
        self.old(directory=True)
        self.source()
        before = self.state()
        with patch.object(ops, 'write_registry', return_value=False):
            result = self.market_install()
        self.assertFalse(result['ok'])
        self.assertEqual(self.state(), before)

    def test_market_update_success_preserves_config_and_data(self):
        self.old(directory=True)
        self.source(manifest={'name': 'demo', 'version': '2'})
        data = self.home / 'data' / 'plugins' / 'demo'
        data.mkdir(parents=True)
        (data / 'user.txt').write_text('user data')
        result = self.market_install()
        self.assertTrue(result['ok'], result)
        self.assertEqual((data / 'user.txt').read_text(), 'user data')
        self.assertEqual(self.config.read_bytes(), self.config_before)
        self.assertIn('new', (self.plugins / 'demo' / '__init__.py').read_text())
        self.assertTrue(result['restart_needed'])

    def test_invalid_market_paths(self):
        self.source()
        for relative in ('../outside', '/tmp/outside', 'C:/plugins/demo',
                         'plugins/../demo', 'plugins\\demo', 'plugins//demo', '~/.plugins'):
            with self.subTest(relative=relative):
                result = self.market_install(relative)
                self.assertFalse(result['ok'], result)
                self.assertEqual(self.state(), {})

    def test_invalid_market_names(self):
        self.source()
        for name in ('../escape', '/escape', 'CON', 'nul', 'COM1', 'demo:stream', '_hidden'):
            with self.subTest(name=name):
                self.assertFalse(self.market_install(name=name)['ok'])
                self.assertEqual(self.state(), {})

    def test_source_root_symlink_rejected(self):
        source = self.source()
        link = self.repo / 'linked'
        try:
            link.symlink_to(source, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires a privilege not enabled in this test environment')
            raise
        self.assertFalse(self.market_install('linked')['ok'])

    def test_source_child_symlink_rejected_without_read(self):
        source = self.source()
        try:
            (source / 'zxplugin.json').symlink_to(self.config)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires a privilege not enabled in this test environment')
            raise
        self.assertFalse(self.market_install()['ok'])
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_target_symlink_rejected(self):
        self.source()
        outside = self.home / 'outside'
        outside.mkdir()
        (outside / 'user.txt').write_text('preserve')
        try:
            (self.plugins / 'demo').symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires a privilege not enabled in this test environment')
            raise
        self.assertFalse(self.market_install()['ok'])
        self.assertEqual((outside / 'user.txt').read_text(), 'preserve')

    def test_reserved_zip_filename_rejected(self):
        ok, result = ops.install(self.archive(filename='CON.py'))
        self.assertFalse(ok)
        self.assertIn('Windows', result['err'])
        self.assertEqual(self.state(), {})

    def test_invalid_manifest_and_structure_rejected(self):
        self.source(manifest=[])
        self.assertFalse(self.market_install()['ok'])
        self.assertEqual(self.state(), {})
        (self.repo / 'plugins/demo/zxplugin.json').unlink()
        (self.repo / 'plugins/demo/run.exe').write_bytes(b'MZ')
        self.assertFalse(self.market_install()['ok'])

    def test_channel_config_failure_after_replace_restores_files(self):
        self.old()
        before = self.state()
        read = tx.configuration_snapshot
        calls = 0
        def fail():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('injected channel configuration read failure')
            return read()
        with patch.object(tx, 'configuration_snapshot', side_effect=fail):
            ok, result = ops.install(self.archive())
        self.assertFalse(ok)
        self.assertEqual(result['phase'], 'configuration_guard')
        self.assertEqual(self.state(), before)
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_concurrent_channel_write_is_never_overwritten(self):
        self.old()
        before = self.state()
        original = ops.write_registry
        def change_config(reg):
            self.config.write_text('{"user_update":true}')
            return original(reg)
        with patch.object(ops, 'write_registry', side_effect=change_config):
            ok, result = ops.install(self.archive())
        self.assertFalse(ok)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.config.read_text(), '{"user_update":true}')

    def test_rollback_failure_is_explicit_and_backups_retained(self):
        self.old()
        original = os.replace
        def fail_recovery(source, target):
            if '_backup' in str(source):
                raise PermissionError('injected rollback lock')
            return original(source, target)
        with patch.object(ops, 'write_registry', return_value=False), \
             patch.object(tx.os, 'replace', side_effect=fail_recovery):
            ok, result = ops.install(self.archive())
        self.assertFalse(ok)
        self.assertEqual(result['rollback'], 'failed')
        self.assertTrue(Path(result['recovery_path']).exists())
        self.assertTrue(result['rollback_errors'])
        self.assertIn('回滚失败', result['err'])

    def test_malformed_registry_never_overwritten(self):
        self.registry.write_bytes(b'{bad')
        before = self.state()
        self.assertFalse(ops.install(self.archive())[0])
        self.assertEqual(self.state(), before)

    def test_legacy_missing_manifest_desc_and_dynamic_name_accepted(self):
        archive = self.archive()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('demo.py', 'NAME="de" + "mo"\n')
        self.assertTrue(ops.install(archive)[0])

    def test_uninstall_keeps_data_by_default_and_purge_removes_it(self):
        ops.install(self.archive())
        data = self.home / 'data/plugins/demo'
        data.mkdir(parents=True)
        (data / 'user.txt').write_text('user')
        self.assertTrue(ops.uninstall('demo')[0])
        self.assertTrue(data.exists())
        ops.install(self.archive())
        self.assertTrue(ops.uninstall('demo', purge_data=True)[0])
        self.assertFalse(data.exists())


if __name__ == '__main__':
    unittest.main()
