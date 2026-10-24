import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
sys.path.insert(0, str(ROOT))

from home_migration import migrate_legacy_plugins, seed_core_features
from scripts.plugin_package_contract import assert_no_bundled_plugin_tree


def test_managed_brain_publishes_candidate_port_and_process_identity(tmp_path, monkeypatch):
    import server_launch

    ready = tmp_path / "runtime" / "ready.json"
    ready.parent.mkdir()
    monkeypatch.setenv("VIGIL_READY_FILE", str(ready))
    monkeypatch.setenv("VIGIL_INSTANCE_ID", "fixture-instance")
    monkeypatch.setattr(server_launch.os, "getpid", lambda: 73)
    assert server_launch._write_candidate_ready(54321) is True
    payload = json.loads(ready.read_text(encoding="utf-8"))
    assert payload["pid"] == 73
    assert payload["instance_id"] == "fixture-instance"
    assert payload["port"] == 54321


def test_brain_release_guard_rejects_legacy_plugin_trees(tmp_path):
    (tmp_path / "_internal" / "core_features").mkdir(parents=True)
    (tmp_path / "_internal" / "core_features" / "kb.py").write_text("core", encoding="utf-8")
    assert assert_no_bundled_plugin_tree(tmp_path) is True

    plugin_dir = tmp_path / "_internal" / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "future_plugin.py").write_text("extension", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bundled plugin tree"):
        assert_no_bundled_plugin_tree(tmp_path)


def test_brain_release_guard_rejects_renamed_future_plugin_by_manifest(tmp_path):
    package = tmp_path / "_internal" / "extensions" / "future_plugin"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(json.dumps({
        "schema": "vigil.brain-plugin/v1", "name": "future_plugin",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="bundled plugin manifest"):
        assert_no_bundled_plugin_tree(tmp_path)


def test_core_feature_seeding_never_creates_plugins_directory_and_preserves_user_edits(tmp_path):
    bundle = tmp_path / "bundle" / "core_features"
    bundle.mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    source = bundle / "calc.py"
    source.write_text("core-v1", encoding="utf-8")

    result = seed_core_features(bundle, home)
    target = home / "core_features" / "calc.py"
    assert result["seeded"] == 1
    assert target.read_text(encoding="utf-8") == "core-v1"
    assert not (home / "plugins").exists()

    source.write_text("core-v2", encoding="utf-8")
    seed_core_features(bundle, home)
    assert target.read_text(encoding="utf-8") == "core-v2"

    target.write_text("user-edited", encoding="utf-8")
    source.write_text("core-v3", encoding="utf-8")
    seed_core_features(bundle, home)
    assert target.read_text(encoding="utf-8") == "user-edited"


def test_legacy_optional_plugins_migrate_once_without_resurrection_after_uninstall(tmp_path):
    home = tmp_path / "home"
    legacy = home / "plugins"
    legacy.mkdir(parents=True)
    (legacy / "kb.py").write_text("legacy-kb-feature", encoding="utf-8")
    (legacy / "reminder.py").write_text("legacy-optional-plugin", encoding="utf-8")

    result = migrate_legacy_plugins(home)
    installed = home / "data" / "installed_plugins"
    assert result["copied"] == 2
    assert (installed / "kb.py").read_text(encoding="utf-8") == "legacy-kb-feature"
    assert (installed / "reminder.py").read_text(encoding="utf-8") == "legacy-optional-plugin"
    assert (legacy / "reminder.py").is_file(), "migration must preserve the source for recovery"

    # Simulate explicit Plugin Manager uninstall while the immutable legacy
    # source remains present. Future starts/upgrades must not reinstall it.
    (installed / "kb.py").unlink()
    (installed / "reminder.py").unlink()
    result = migrate_legacy_plugins(home)
    assert result["copied"] == 0
    assert not (installed / "kb.py").exists()
    assert not (installed / "reminder.py").exists()


def test_failed_legacy_plugin_copy_is_reported_and_keeps_source(monkeypatch, tmp_path):
    import home_migration

    home = tmp_path / "home"
    source = home / "plugins" / "legacy.py"
    source.parent.mkdir(parents=True)
    source.write_text("NAME='legacy'", encoding="utf-8")

    def fail_copy(*_args, **_kwargs):
        raise OSError("simulated full disk")

    monkeypatch.setattr(home_migration.shutil, "copy2", fail_copy)
    result = migrate_legacy_plugins(home)
    assert result["copied"] == 0
    assert result["errors"] and "simulated full disk" in result["errors"][0]
    assert source.read_text(encoding="utf-8") == "NAME='legacy'"
    assert not (home / "data/installed_plugins/legacy.py").exists()


def test_previous_p21_core_feature_copies_migrate_as_optional_plugins(tmp_path):
    home = tmp_path / "home"
    old = home / "core_features" / "gate.py"
    old.parent.mkdir(parents=True)
    old.write_text("user-adjusted-gate", encoding="utf-8")
    (old.parent / "kb.py").write_text("legacy-kb-feature", encoding="utf-8")

    result = migrate_legacy_plugins(home)
    target = home / "data/installed_plugins/gate.py"
    kb_target = home / "data/installed_plugins/kb.py"
    assert result["copied"] == 2
    assert target.read_text(encoding="utf-8") == "user-adjusted-gate"
    assert kb_target.read_text(encoding="utf-8") == "legacy-kb-feature"
    assert old.read_text(encoding="utf-8") == "user-adjusted-gate"


def test_previous_installer_core_feature_and_plugin_trees_migrate_from_legacy_roots(tmp_path):
    home = tmp_path / "user-home"
    old_brain = tmp_path / "previous-install" / "resources" / "vigil-brain"
    (old_brain / "_internal/core_features").mkdir(parents=True)
    (old_brain / "_internal/plugins").mkdir(parents=True)
    (old_brain / "_internal/core_features/kb.py").write_text("legacy-kb-adapter", encoding="utf-8")
    (old_brain / "_internal/core_features/gate.py").write_text("legacy-gate", encoding="utf-8")
    (old_brain / "_internal/plugins/reminder.py").write_text("legacy-reminder", encoding="utf-8")
    (old_brain / "_internal/core_features/remote.py").write_text("core-only", encoding="utf-8")

    result = migrate_legacy_plugins(home, [old_brain])
    installed = home / "data/installed_plugins"
    assert result["copied"] == 3
    assert (installed / "kb.py").read_text(encoding="utf-8") == "legacy-kb-adapter"
    assert (installed / "gate.py").read_text(encoding="utf-8") == "legacy-gate"
    assert (installed / "reminder.py").read_text(encoding="utf-8") == "legacy-reminder"
    assert not (installed / "remote.py").exists()

    (installed / "kb.py").unlink()
    result = migrate_legacy_plugins(home, [old_brain])
    assert result["copied"] == 0
    assert not (installed / "kb.py").exists()
    assert (old_brain / "_internal/core_features/kb.py").is_file()


def test_release_spec_and_launcher_use_core_features_not_bundled_plugins():
    spec = (ROOT / "deploy/brain/vigil-brain.spec").read_text(encoding="utf-8")
    legacy_spec = (ROOT / "brain/vigil-brain.spec").read_text(encoding="utf-8")
    launcher = (ROOT / "brain/server_launch.py").read_text(encoding="utf-8")
    paths = (ROOT / "brain/paths.py").read_text(encoding="utf-8")
    host = (ROOT / "brain/plugin_host.py").read_text(encoding="utf-8")
    build = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")

    assert '["git", "ls-files", "-z", "--", "brain/core_features"]' in spec
    assert '["git", "ls-files", "-z", "--", "brain/plugins"]' not in spec
    assert "split(chr(0))" in spec
    assert "CORE_FEATURE_ALLOWLIST = {" in spec
    assert "if rel_file not in CORE_FEATURE_ALLOWLIST" in spec
    assert "if seen_core_features != CORE_FEATURE_ALLOWLIST" in spec
    assert "datas=core_feature_data + skill_host_data" in spec
    for optional in ("gate.py", "kb.py", "react.py", "websearch.py"):
        assert optional not in spec
        assert not (ROOT / "brain/core_features" / optional).exists()
    assert not (ROOT / "brain/core_features" / "calc.py").exists()
    assert "('core_features', 'core_features')" not in legacy_spec
    assert "('core_features/plugin_market.py', 'core_features')" in legacy_spec
    assert "def _seed_core_features()" in launcher
    assert "_seed_plugins()" not in launcher
    assert "migrate_legacy_plugins(HOME_DIR, legacy_roots)" in launcher
    assert 'CORE_FEATURES_DIR = os.path.join(ROOT, "core_features")' in paths
    assert "CORE_FEATURES_DIR" in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "PLUGINS_DIR" not in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "from paths import CORE_FEATURES_DIR" in host
    assert "_CORE_PLUGIN_FILES = frozenset({\"plugin_market.py\", \"remote.py\", \"skill.py\"})" in host
    assert "(\"kb.py\", \"plugin_market.py\", \"remote.py\", \"skill.py\")" not in spec
    assert "core_features/kb.py" not in legacy_spec
    assert "assert_no_bundled_plugin_tree(OUT)" in build


def test_client_package_contract_regression_runs_under_python_ci():
    completed = subprocess.run(
        ["node", "--test", "client/tools/test-plugin-package-boundary.js"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
