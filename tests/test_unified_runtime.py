"""Managed home compatibility and copy-only migration, no production data."""
import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'brain'))
from home_migration import migrate, seed_core_features


class ManagedMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.old = self.root / 'old app'
        self.new = self.root / 'user data'
        (self.old / 'config').mkdir(parents=True)
        (self.old / 'config/config.json').write_text(json.dumps({'api_key': 'fixture-key', 'model': 'fixture'}))

    def test_config_credentials_plugins_and_skills_migrate_without_source_changes(self):
        for rel in ['plugins/fixture.py', 'skill_host/skills/fixture/index.js', 'data/fixture.txt']:
            p = self.old / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text('fixture')
        before = (self.old / 'config/config.json').read_bytes()
        result = migrate(self.old, self.new)
        self.assertEqual(result['copied'], 4)
        self.assertEqual(json.loads((self.new / 'config/config.json').read_text())['api_key'], 'fixture-key')
        self.assertEqual(before, (self.old / 'config/config.json').read_bytes())
        self.assertTrue((self.new / 'skill_host/skills/fixture/index.js').exists())
        self.assertEqual(migrate(self.old, self.new), result)

    def test_existing_target_is_never_overwritten_and_conflict_copy_retained(self):
        (self.new / 'config').mkdir(parents=True)
        target = self.new / 'config/config.json'
        target.write_text('{"api_key":"new-fixture-key"}')
        before = target.read_bytes()
        self.assertEqual(migrate(self.old, self.new)['conflicts'], 1)
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(len(list((self.new / 'migration/conflicts').rglob('config.json'))), 1)

    def test_live_wal_database_uses_consistent_online_backup(self):
        (self.old / 'data').mkdir()
        con = sqlite3.connect(self.old / 'data/jobs.sqlite3')
        self.addCleanup(con.close)
        con.execute('pragma journal_mode=wal')
        con.execute('create table jobs (id text)')
        con.execute("insert into jobs values ('committed-in-wal')")
        con.commit()
        migrate(self.old, self.new)
        with closing(sqlite3.connect(self.new / 'data/jobs.sqlite3')) as db:
            self.assertEqual(db.execute('select id from jobs').fetchone()[0], 'committed-in-wal')
            self.assertEqual(db.execute('pragma integrity_check').fetchone()[0], 'ok')
        self.assertFalse((self.new / 'data/jobs.sqlite3-wal').exists())

    def test_nested_home_rejected(self):
        with self.assertRaises(ValueError):
            migrate(self.old, self.old / 'nested')

    def test_frozen_process_honors_external_home(self):
        code = "import sys,os;sys.frozen=True;sys.path.insert(0,sys.argv[1]);import paths;print(paths.ROOT)"
        result = subprocess.check_output([sys.executable, '-c', code, str(ROOT / 'brain')],
            env=dict(os.environ, VIGIL_HOME=str(self.new))).decode().strip()
        self.assertEqual(Path(result), self.new)

    def test_relative_internal_persona_reference_is_rebased(self):
        persona = self.old / 'config/personas/default.json'
        persona.parent.mkdir()
        persona.write_text('{}')
        cfg = self.old / 'config/config.json'
        cfg.write_text(json.dumps({'persona_file': str(persona)}))
        migrate(self.old, self.new)
        self.assertEqual(Path(json.loads((self.new / 'config/config.json').read_text())['persona_file']), self.new / 'config/personas/default.json')


class BuildPolicy(unittest.TestCase):
    def test_shipped_core_feature_updates_do_not_overwrite_custom_code(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bundle, home = root / 'bundle', root / 'home'
            bundle.mkdir()
            (bundle / 'fixture.py').write_text('old stock')
            seed_core_features(bundle, home)
            (bundle / 'fixture.py').write_text('new stock')
            seed_core_features(bundle, home)
            self.assertEqual((home / 'core_features/fixture.py').read_text(), 'new stock')
            (home / 'core_features/fixture.py').write_text('user custom')
            (bundle / 'fixture.py').write_text('newer stock')
            seed_core_features(bundle, home)
            self.assertEqual((home / 'core_features/fixture.py').read_text(), 'user custom')

    def test_runtime_contract_does_not_expose_config(self):
        import build_identity
        value = build_identity.runtime()
        self.assertTrue({'creator_jobs', 'agent_runs'}.issubset(value['capabilities']))
        self.assertTrue({'api_key', 'brain_token', 'config'}.isdisjoint(value))

    def test_official_build_refuses_dirty_tree(self):
        spec = importlib.util.spec_from_file_location('release_builder', ROOT / 'scripts/build_release.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        from unittest.mock import patch
        with patch.object(module, 'git', return_value=' M tracked-file'):
            with self.assertRaisesRegex(RuntimeError, 'Commit and test'):
                module.build()
