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


def test_brain_release_guard_rejects_legacy_plugin_trees(tmp_path):
    (tmp_path / "_internal" / "core_features").mkdir(parents=True)
    (tmp_path / "_internal" / "core_features" / "calc.py").write_text("core", encoding="utf-8")
    assert assert_no_bundled_plugin_tree(tmp_path) is True

    plugin_dir = tmp_path / "_internal" / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "wallpaper.py").write_text("extension", encoding="utf-8")
    with pytest.raises(RuntimeError, match="bundled plugin tree"):
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


def test_legacy_optional_plugins_migrate_as_user_installs_but_core_duplicates_do_not(tmp_path):
    home = tmp_path / "home"
    legacy = home / "plugins"
    core = home / "core_features"
    legacy.mkdir(parents=True)
    core.mkdir()
    (core / "calc.py").write_text("current-core", encoding="utf-8")
    (legacy / "calc.py").write_text("current-core", encoding="utf-8")
    (legacy / "reminder.py").write_text("legacy-optional-plugin", encoding="utf-8")

    result = migrate_legacy_plugins(home)
    installed = home / "data" / "installed_plugins"
    assert result["skipped_core"] == 1
    assert result["copied"] == 1
    assert not (installed / "calc.py").exists()
    assert (installed / "reminder.py").read_text(encoding="utf-8") == "legacy-optional-plugin"
    assert (legacy / "reminder.py").is_file(), "migration must preserve the source for recovery"

    (legacy / "calc.py").write_text("user-modified-core", encoding="utf-8")
    result = migrate_legacy_plugins(home)
    assert (installed / "calc.py").read_text(encoding="utf-8") == "user-modified-core"


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
    assert "('core_features', 'core_features')" not in legacy_spec
    assert "('core_features/plugin_market.py', 'core_features')" in legacy_spec
    assert "def _seed_core_features()" in launcher
    assert "_seed_plugins()" not in launcher
    assert "migrate_legacy_plugins(HOME_DIR)" in launcher
    assert 'CORE_FEATURES_DIR = os.path.join(ROOT, "core_features")' in paths
    assert "CORE_FEATURES_DIR" in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "PLUGINS_DIR" not in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "from paths import CORE_FEATURES_DIR" in host
    assert "assert_no_bundled_plugin_tree(OUT)" in build


def test_client_package_contract_regression_runs_under_python_ci():
    completed = subprocess.run(
        ["node", "--test", "client/tools/test-plugin-package-boundary.js"],
        cwd=ROOT, capture_output=True, text=True, timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
