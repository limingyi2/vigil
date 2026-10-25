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


def test_server_launch_sets_utf8_replacement_before_startup_diagnostics():
    script = r'''
import runpy, sys
class Stream:
    def __init__(self): self.options = []
    def reconfigure(self, **options): self.options.append(options)
    def write(self, value): return len(value)
    def flush(self): pass
out, err = Stream(), Stream()
sys.stdout, sys.stderr = out, err
runpy.run_path(sys.argv[1])
expected = [{"encoding": "utf-8", "errors": "replace"}]
assert out.options == expected, out.options
assert err.options == expected, err.options
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(ROOT / "brain/server_launch.py")],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr


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


def test_brain_release_guard_rejects_any_plugin_tree_including_future_plugin(tmp_path):
    plugin = tmp_path / "_internal" / "plugins" / "future_plugin"
    plugin.mkdir(parents=True)
    (plugin / "__init__.py").write_text("future", encoding="utf-8")
    with pytest.raises(RuntimeError, match="plugin source tree"):
        assert_no_bundled_plugin_tree(tmp_path)


def test_brain_release_guard_rejects_wallpaper_payload(tmp_path):
    """wallpaper 是市场外部插件，任何形态都不许随包。"""
    package = tmp_path / "_internal" / "plugins" / "wallpaper"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("external", encoding="utf-8")
    with pytest.raises(RuntimeError, match="plugin source tree"):
        assert_no_bundled_plugin_tree(tmp_path)


def test_brain_release_guard_rejects_installed_plugin_tree(tmp_path):
    installed = tmp_path / "_internal" / "data" / "installed_plugins"
    installed.mkdir(parents=True)
    (installed / "kb.py").write_text("runtime data", encoding="utf-8")
    with pytest.raises(RuntimeError, match="installed plugin"):
        assert_no_bundled_plugin_tree(tmp_path)


def test_brain_release_guard_rejects_renamed_future_plugin_by_manifest(tmp_path):
    package = tmp_path / "_internal" / "extensions" / "future_plugin"
    package.mkdir(parents=True)
    (package / "manifest.json").write_text(json.dumps({
        "schema": "vigil.brain-plugin/v1", "name": "future_plugin",
    }), encoding="utf-8")
    with pytest.raises(RuntimeError, match="extension plugin manifest"):
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
    old = home / "core_features" / "websearch.py"
    old.parent.mkdir(parents=True)
    old.write_text("user-adjusted-websearch", encoding="utf-8")
    (old.parent / "kb.py").write_text("legacy-kb-feature", encoding="utf-8")

    result = migrate_legacy_plugins(home)
    target = home / "data/installed_plugins/websearch.py"
    kb_target = home / "data/installed_plugins/kb.py"
    assert result["copied"] == 2
    assert target.read_text(encoding="utf-8") == "user-adjusted-websearch"
    assert kb_target.read_text(encoding="utf-8") == "legacy-kb-feature"
    assert old.read_text(encoding="utf-8") == "user-adjusted-websearch"


def test_previous_installer_core_feature_and_plugin_trees_migrate_from_legacy_roots(tmp_path):
    home = tmp_path / "user-home"
    old_brain = tmp_path / "previous-install" / "resources" / "vigil-brain"
    (old_brain / "_internal/core_features").mkdir(parents=True)
    (old_brain / "_internal/plugins").mkdir(parents=True)
    (old_brain / "_internal/core_features/kb.py").write_text("legacy-kb-adapter", encoding="utf-8")
    (old_brain / "_internal/core_features/websearch.py").write_text("legacy-websearch", encoding="utf-8")
    (old_brain / "_internal/plugins/reminder.py").write_text("legacy-reminder", encoding="utf-8")
    (old_brain / "_internal/core_features/remote.py").write_text("core-only", encoding="utf-8")

    result = migrate_legacy_plugins(home, [old_brain])
    installed = home / "data/installed_plugins"
    assert result["copied"] == 3
    assert (installed / "kb.py").read_text(encoding="utf-8") == "legacy-kb-adapter"
    assert (installed / "websearch.py").read_text(encoding="utf-8") == "legacy-websearch"
    assert (installed / "reminder.py").read_text(encoding="utf-8") == "legacy-reminder"
    assert not (installed / "remote.py").exists()

    (installed / "kb.py").unlink()
    result = migrate_legacy_plugins(home, [old_brain])
    assert result["copied"] == 0
    assert not (installed / "kb.py").exists()
    assert (old_brain / "_internal/core_features/kb.py").is_file()


def test_p21_2_retired_plugins_are_not_resurrected_from_legacy_or_retired_trees(tmp_path):
    home = tmp_path / "home"
    legacy = home / "plugins"
    retired = home / "data/installed_plugins/_retired"
    legacy.mkdir(parents=True)
    retired.mkdir(parents=True)
    for name in ("calc", "gate", "meme", "sticker_pack"):
        (legacy / (name + ".py")).write_text("NAME='" + name + "'\n", encoding="utf-8")
        (retired / (name + ".py")).write_text("NAME='" + name + "'\n", encoding="utf-8")
    retired_package = retired / "gate.1234abcd"
    retired_package.mkdir()
    (retired_package / "__init__.py").write_text("NAME='gate'\n", encoding="utf-8")

    result = migrate_legacy_plugins(home)
    assert result["copied"] == 0
    for name in ("calc", "gate", "meme", "sticker_pack"):
        assert not (home / "data/installed_plugins" / (name + ".py")).exists()
        assert (legacy / (name + ".py")).is_file()
        assert (retired / (name + ".py")).is_file()


def test_old_plugin_copy_identical_to_bundled_version_is_migrated(tmp_path):
    home = tmp_path / "home"
    legacy = home / "plugins"
    legacy.mkdir(parents=True)
    plugin = legacy / "branch-atlas"
    plugin.mkdir()
    (plugin / "__init__.py").write_text("NAME='branch-atlas'", encoding="utf-8")
    (plugin / "frontend.js").write_text("// shipped bytes", encoding="utf-8")
    _write_marker(home, {
        "branch-atlas/__init__.py": hashlib.sha256(b"NAME='branch-atlas'").hexdigest(),
        "branch-atlas/frontend.js": hashlib.sha256(b"// shipped bytes").hexdigest(),
    })
    result = migrate_legacy_plugins(home)
    installed = home / "data/installed_plugins/branch-atlas"
    assert result["copied"] == 1
    assert (installed / "frontend.js").read_text(encoding="utf-8") == "// shipped bytes"
    assert (plugin / "__init__.py").exists(), "source stays available for recovery"
    (installed / "__init__.py").unlink()
    (installed / "frontend.js").unlink()
    assert migrate_legacy_plugins(home)["copied"] == 0


def test_plugin_host_ignores_legacy_source_and_only_loads_core_allowlist_and_installs(tmp_path, monkeypatch):
    import plugin_host

    legacy = tmp_path / "legacy-plugins"
    core = tmp_path / "core_features"
    installed = tmp_path / "data/installed_plugins"
    for root in (legacy, core, installed):
        root.mkdir(parents=True)
    (legacy / "future_plugin.py").write_text("NAME='legacy_future'\n", encoding="utf-8")
    (core / "skill.py").write_text("NAME='skill'\n", encoding="utf-8")
    (core / "future_plugin.py").write_text("NAME='core_future'\n", encoding="utf-8")
    (installed / "wallpaper.py").write_text("NAME='wallpaper'\n", encoding="utf-8")

    old_registry, old_loaded = dict(plugin_host._registry), plugin_host._loaded
    monkeypatch.setattr(plugin_host, "_DIR", str(legacy))
    monkeypatch.setattr(plugin_host, "_CORE_PLUGIN_ROOT", str(core))
    monkeypatch.setattr(plugin_host, "_INSTALLED_DIR", str(installed))
    try:
        loaded = plugin_host.load(force=True)
        assert set(loaded) == {"skill", "wallpaper"}
        assert "legacy_future" not in loaded
        assert "core_future" not in loaded
        assert plugin_host._plugin_entry_path("legacy_future") == ""
        assert plugin_host._plugin_entry_path("skill") == str(core / "skill.py")
        assert plugin_host._plugin_entry_path("wallpaper") == str(installed / "wallpaper.py")
    finally:
        plugin_host._registry.clear()
        plugin_host._registry.update(old_registry)
        plugin_host._loaded = old_loaded


def _write_marker(home, mapping):
    marker = home / "data/run/bundled-plugins.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(mapping), encoding="utf-8")


def test_retired_old_bundle_copy_is_restored_without_mutating_recovery_source(tmp_path):
    home = tmp_path / "home"
    retired = home / "data/installed_plugins/_retired/branch-atlas.1234abcd"
    retired.mkdir(parents=True)
    (retired / "__init__.py").write_text("NAME='branch-atlas'", encoding="utf-8")
    result = migrate_legacy_plugins(home)
    assert result["copied"] == 1
    assert (home / "data/installed_plugins/branch-atlas/__init__.py").exists()
    assert (retired / "__init__.py").exists()
    (home / "data/installed_plugins/branch-atlas/__init__.py").unlink()
    assert migrate_legacy_plugins(home)["copied"] == 0


def test_release_specs_and_runtime_have_no_plugin_source_fallback():
    spec = (ROOT / "deploy/brain/vigil-brain.spec").read_text(encoding="utf-8")
    legacy_spec = (ROOT / "brain/vigil-brain.spec").read_text(encoding="utf-8")
    launcher = (ROOT / "brain/server_launch.py").read_text(encoding="utf-8")
    paths = (ROOT / "brain/paths.py").read_text(encoding="utf-8")
    host = (ROOT / "brain/plugin_host.py").read_text(encoding="utf-8")
    manager = (ROOT / "brain/plugin_manager.py").read_text(encoding="utf-8")
    build = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")

    assert 'CORE_FEATURE_ALLOWLIST = {' in spec
    assert 'SKILL_HOST_ALLOWLIST = {"server.js", "start_host.bat"}' in spec
    assert 'rel_file in {"skills/.gitkeep", ".host_started"}' in spec
    assert "brain/plugins" in spec and "绝不是打包输入" in spec
    assert "plugins_data" not in spec and "seed_plugins" not in launcher
    assert "retire_superseded_installs" not in launcher
    assert "plugins_data" not in legacy_spec and "os.walk" not in legacy_spec
    assert "PLUGINS_DIR" not in paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "for directory in (_INSTALLED_DIR, _CORE_PLUGIN_ROOT)" in host
    assert "(_CORE_PLUGIN_ROOT, _DIR)" not in host.split("def load(", 1)[1].split("def _sync_features", 1)[0]
    assert "_CORE_PLUGIN_ROOT" in manager
    assert "assert_no_bundled_plugin_tree(OUT)" in build


def test_clean_path_bootstrap_does_not_create_a_legacy_plugin_source(tmp_path, monkeypatch):
    import paths

    home = tmp_path / "clean-home"
    monkeypatch.setattr(paths, "ROOT", str(home))
    for name in ("CONFIG_DIR", "DATA_DIR", "LOGS_DIR", "ARCHIVE_DIR", "PERSONA_DIR",
                 "MEMORY_DIR", "LIFE_DIR", "UPLOAD_DIR", "RUN_DIR", "CORE_FEATURES_DIR",
                 "PLUGINS_DIR", "PLUGIN_DATA_DIR", "KB_DIR", "CONTEXT_COMPACTION_DIR", "DB_DIR"):
        monkeypatch.setattr(paths, name, str(home / name.lower()))
    monkeypatch.setattr(paths, "PLUGIN_INSTALL_DIR", str(home / "data/installed_plugins"))
    monkeypatch.setattr(paths, "_DIRS", tuple(getattr(paths, name) for name in (
        "CONFIG_DIR", "DATA_DIR", "LOGS_DIR", "ARCHIVE_DIR", "PERSONA_DIR",
        "MEMORY_DIR", "LIFE_DIR", "UPLOAD_DIR", "RUN_DIR", "CORE_FEATURES_DIR",
        "PLUGIN_DATA_DIR", "KB_DIR", "CONTEXT_COMPACTION_DIR", "DB_DIR")))
    paths.ensure()
    assert not Path(paths.PLUGINS_DIR).exists()
    assert Path(paths.CORE_FEATURES_DIR).is_dir()
    assert not Path(paths.PLUGIN_INSTALL_DIR).exists()


def test_future_plugin_in_actual_artifact_contract_is_rejected(tmp_path):
    for relative in ("_internal/plugins/future_plugin/__init__.py",
                     "data/installed_plugins/renamed_plugin/main.py"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("future", encoding="utf-8")
    with pytest.raises(RuntimeError):
        assert_no_bundled_plugin_tree(tmp_path)


def test_client_package_contract_regression_runs_under_python_ci():
    completed = subprocess.run(
        ["node", "--test", "client/tools/test-plugin-package-boundary.js"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
