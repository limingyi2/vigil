import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))
sys.path.insert(0, str(ROOT))

from home_migration import migrate_legacy_plugins, seed_core_features
from scripts.plugin_package_contract import assert_no_bundled_plugin_tree


def test_managed_brain_ready_probe_uses_configured_auth_token(tmp_path, monkeypatch):
    import server_launch

    token = "isolated-ready-token"
    config = tmp_path / "config" / "config.json"
    config.parent.mkdir()
    config.write_text(json.dumps({"brain_token": token}), encoding="utf-8")
    monkeypatch.setattr(server_launch, "HOME_DIR", str(tmp_path))

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer " + token:
                self.send_response(401)
            else:
                self.send_response(200)
            self.end_headers()

        def log_message(self, *_args):
            pass

    listener = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=listener.serve_forever, daemon=True).start()
    try:
        assert server_launch._wait_health(listener.server_port, timeout=2) is True
    finally:
        listener.shutdown()
        listener.server_close()


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


def test_legacy_optional_plugins_migrate_as_user_installs_but_core_duplicates_do_not(tmp_path):
    home = tmp_path / "home"
    legacy = home / "plugins"
    core = home / "core_features"
    legacy.mkdir(parents=True)
    core.mkdir()
    (core / "kb.py").write_text("current-core", encoding="utf-8")
    (legacy / "kb.py").write_text("current-core", encoding="utf-8")
    (legacy / "reminder.py").write_text("legacy-optional-plugin", encoding="utf-8")

    result = migrate_legacy_plugins(home)
    installed = home / "data" / "installed_plugins"
    assert result["skipped_core"] == 1
    assert result["copied"] == 1
    assert not (installed / "kb.py").exists()
    assert (installed / "reminder.py").read_text(encoding="utf-8") == "legacy-optional-plugin"
    assert (legacy / "reminder.py").is_file(), "migration must preserve the source for recovery"

    (legacy / "kb.py").write_text("user-modified-core", encoding="utf-8")
    result = migrate_legacy_plugins(home)
    assert (installed / "kb.py").read_text(encoding="utf-8") == "user-modified-core"


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

    result = migrate_legacy_plugins(home)
    target = home / "data/installed_plugins/gate.py"
    assert result["copied"] == 1
    assert target.read_text(encoding="utf-8") == "user-adjusted-gate"
    assert old.read_text(encoding="utf-8") == "user-adjusted-gate"


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
    for optional in ("gate.py", "react.py", "websearch.py"):
        assert optional not in spec
        assert not (ROOT / "brain/core_features" / optional).exists()
    assert not (ROOT / "brain/core_features" / "calc.py").exists()
    assert "('core_features', 'core_features')" not in legacy_spec
    assert "('core_features/plugin_market.py', 'core_features')" in legacy_spec
    assert "def _seed_core_features()" in launcher
    assert "_seed_plugins()" not in launcher
    assert "migrate_legacy_plugins(HOME_DIR)" in launcher
    assert 'CORE_FEATURES_DIR = os.path.join(ROOT, "core_features")' in paths
    assert "CORE_FEATURES_DIR" in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "PLUGINS_DIR" not in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "from paths import CORE_FEATURES_DIR" in host
    assert "_CORE_PLUGIN_FILES = frozenset({\"kb.py\", \"plugin_market.py\", \"remote.py\", \"skill.py\"})" in host
    assert "assert_no_bundled_plugin_tree(OUT)" in build


def test_client_package_contract_regression_runs_under_python_ci():
    completed = subprocess.run(
        ["node", "--test", "client/tools/test-plugin-package-boundary.js"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
