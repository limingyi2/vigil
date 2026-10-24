"""Fault injection against temporary homes, never production plugins/config."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import features
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
        ok, result = ops.install_market_directory(str(self.repo), relative, name)
        return dict(result, ok=ok)

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

    def test_market_install_ignores_generated_python_cache(self):
        source = self.source()
        cache = source / '__pycache__'
        cache.mkdir()
        (cache / '__init__.cpython-312.pyc').write_bytes(b'generated fixture cache')
        result = self.market_install()
        self.assertTrue(result['ok'], result)
        self.assertFalse((self.plugins / 'demo' / '__pycache__').exists())

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
        link = source / 'zxplugin.json'
        try:
            link.symlink_to(self.config)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires a privilege not enabled in this test environment')
            raise
        if not os.path.islink(str(link)):
            self.skipTest('这台机器把 symlink 建成了普通文件，测不到"拒绝符号链接"这条')
        self.assertFalse(self.market_install()['ok'])
        self.assertEqual(self.config.read_bytes(), self.config_before)

    def test_target_symlink_rejected(self):
        self.source()
        outside = self.home / 'outside'
        outside.mkdir()
        (outside / 'user.txt').write_text('preserve')
        link = self.plugins / 'demo'
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows symlink creation requires a privilege not enabled in this test environment')
            raise
        if not os.path.islink(str(link)):
            self.skipTest('这台机器把 symlink 建成了普通目录，测不到"拒绝符号链接"这条')
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

    def test_failed_data_purge_never_claims_data_was_removed(self):
        ops.install(self.archive())
        data = self.home / 'data/plugins/demo'
        data.mkdir(parents=True)
        (data / 'user.txt').write_text('user')
        original = ops.shutil.rmtree
        def fail_data(path, *args, **kwargs):
            if Path(path).resolve() == data.resolve():
                raise PermissionError('injected locked plugin data')
            return original(path, *args, **kwargs)
        with patch.object(ops.shutil, 'rmtree', side_effect=fail_data), \
             patch.object(ops.restart_state, 'note_change'):
            ok, result = ops.uninstall('demo', purge_data=True)
        self.assertTrue(ok)
        self.assertTrue(data.exists())
        self.assertIn('数据清理未完成', result['msg'])
        self.assertNotIn('数据已清理', result['msg'])

    def test_partial_data_purge_failure_does_not_claim_data_remains_intact(self):
        ops.install(self.archive())
        data = self.home / 'data/plugins/demo'
        data.mkdir(parents=True)
        user_file = data / 'user.txt'
        user_file.write_text('user')
        original = ops.shutil.rmtree
        def partially_fail(path, *args, **kwargs):
            if Path(path).resolve() == data.resolve():
                user_file.unlink()
                raise PermissionError('injected partial purge')
            return original(path, *args, **kwargs)
        with patch.object(ops.shutil, 'rmtree', side_effect=partially_fail), \
             patch.object(ops.restart_state, 'note_change'):
            ok, result = ops.uninstall('demo', purge_data=True)
        self.assertTrue(ok)
        self.assertTrue(data.exists())
        self.assertFalse(user_file.exists())
        self.assertIn('请检查残留数据', result['msg'])
        self.assertNotIn('数据仍保留', result['msg'])

    def test_data_purge_keeps_channel_cleanup_result_in_message(self):
        archive = self.archive()
        with zipfile.ZipFile(archive, 'w') as z:
            z.writestr('demo.py', 'FEATURE = "DEMO_FEATURE"\n')
        ops.install(archive)
        data = self.home / 'data/plugins/demo'
        data.mkdir(parents=True)
        with patch.object(features, 'drop', return_value=2), \
             patch.object(ops.restart_state, 'note_change'):
            ok, result = ops.uninstall('demo', purge_data=True)
        self.assertTrue(ok)
        self.assertIn('渠道开关也清了（2 处）', result['msg'])
        self.assertIn('插件数据已清理', result['msg'])

    def test_uninstall_does_not_claim_directory_package_removed_when_cleanup_fails(self):
        package = self.plugins / 'demo'
        package.mkdir()
        (package / '__init__.py').write_text('NAME = "demo"\n')
        original = ops.shutil.rmtree
        def fail_package(path, *args, **kwargs):
            if Path(path).resolve() == package.resolve():
                if kwargs.get('ignore_errors'):
                    return None
                raise PermissionError('injected locked package')
            return original(path, *args, **kwargs)
        with patch.object(ops.shutil, 'rmtree', side_effect=fail_package), \
             patch.object(ops.restart_state, 'note_change'):
            ok, result = ops.uninstall('demo')
        self.assertFalse(ok)
        self.assertTrue(package.exists())
        self.assertIn('插件目录清理失败', result['err'])

    def test_uninstall_registry_write_failure_restores_plugin_and_registry(self):
        package = self.plugins / 'demo'
        package.mkdir()
        entry = package / '__init__.py'
        entry.write_text('NAME="demo"\n', encoding='utf-8')
        original = {'version': 1, 'plugins': {'demo': {'version': '1.0.0'}}}
        self.registry.write_text(json.dumps(original), encoding='utf-8')
        with patch.object(ops, 'write_registry', return_value=False), \
             patch.object(ops.restart_state, 'note_change'):
            ok, result = ops.uninstall('demo')
        self.assertFalse(ok)
        self.assertIn('注册表写入失败', result['err'])
        self.assertEqual(entry.read_text(encoding='utf-8'), 'NAME="demo"\n')
        self.assertEqual(json.loads(self.registry.read_text(encoding='utf-8')), original)
        self.assertFalse(any(p.name.startswith('.uninstall-demo-') for p in self.plugins.iterdir()))


if __name__ == '__main__':
    unittest.main()
