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

from home_migration import migrate_legacy_plugins, seed_core_features, seed_plugins
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


def test_brain_release_guard_allows_bundled_plugins(tmp_path):
    """内置插件（kb/gate/branch-atlas…）随包发布是预期状态，不该被拦。"""
    internal = tmp_path / "_internal"
    (internal / "core_features").mkdir(parents=True)
    (internal / "core_features" / "skill.py").write_text("core", encoding="utf-8")
    plugins = internal / "plugins"
    plugins.mkdir()
    (plugins / "kb.py").write_text("bundled", encoding="utf-8")
    package = plugins / "branch-atlas"
    package.mkdir()
    (package / "__init__.py").write_text("bundled", encoding="utf-8")
    assert assert_no_bundled_plugin_tree(tmp_path) is True


def test_brain_release_guard_rejects_wallpaper_payload(tmp_path):
    """wallpaper 是市场外部插件，任何形态都不许随包。"""
    package = tmp_path / "_internal" / "plugins" / "wallpaper"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("external", encoding="utf-8")
    with pytest.raises(RuntimeError, match="external plugin"):
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


def test_seed_plugins_updates_shipped_code_and_keeps_user_edits(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    source = bundle / "kb.py"
    source.write_text("kb-v1", encoding="utf-8")

    assert seed_plugins(bundle, home)["seeded"] == 1
    target = home / "plugins" / "kb.py"
    assert target.read_text(encoding="utf-8") == "kb-v1"

    # 上一版随包内容应被新版覆盖，用户改过的必须保留。
    source.write_text("kb-v2", encoding="utf-8")
    seed_plugins(bundle, home)
    assert target.read_text(encoding="utf-8") == "kb-v2"

    target.write_text("user-edited", encoding="utf-8")
    source.write_text("kb-v3", encoding="utf-8")
    seed_plugins(bundle, home)
    assert target.read_text(encoding="utf-8") == "user-edited"


def test_seed_plugins_keeps_directory_package_resources(tmp_path):
    bundle = tmp_path / "bundle" / "branch-atlas"
    bundle.mkdir(parents=True)
    (bundle / "__init__.py").write_text("NAME='branch-atlas'", encoding="utf-8")
    (bundle / "frontend.js").write_text("// ui", encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()

    result = seed_plugins(tmp_path / "bundle", home)
    assert result["seeded"] == 2
    assert (home / "plugins" / "branch-atlas" / "frontend.js").is_file()


def _write_marker(home, mapping):
    marker = home / "data/run/bundled-plugins.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps(mapping), encoding="utf-8")


def test_retire_superseded_installs_only_removes_unmodified_shipped_copies(tmp_path):
    """P21.0 把随包插件复制进了 installed_plugins，升级后会反向盖掉新版内置。"""
    from home_migration import retire_superseded_installs

    home = tmp_path / "home"
    installed = home / "data" / "installed_plugins"
    (installed / "branch-atlas").mkdir(parents=True)
    kb_copy = installed / "kb.py"
    kb_copy.write_text("kb-v1", encoding="utf-8")
    (installed / "branch-atlas" / "__init__.py").write_text("NAME='branch-atlas'", encoding="utf-8")
    _write_marker(home, {
        "kb.py": hashlib.sha256(b"kb-v1").hexdigest(),
        "branch-atlas/__init__.py": hashlib.sha256(b"NAME='branch-atlas'").hexdigest(),
        "gate.py": hashlib.sha256(b"gate-v1").hexdigest(),
    })

    result = retire_superseded_installs(home)

    assert result["retired"] == 2
    assert result["errors"] == []
    # 内置目录清空，副本留在 _retired 而不是被删除。
    assert not kb_copy.exists()
    assert not (installed / "branch-atlas").exists()
    assert (installed / "_retired" / "kb.py").read_text(encoding="utf-8") == "kb-v1"
    assert (installed / "_retired" / "branch-atlas" / "__init__.py").is_file()
    # 幂等：再跑一次无事发生，也不会重复搬进 _retired。
    again = retire_superseded_installs(home)
    assert again == {"retired": 0, "errors": []}


def test_retire_superseded_installs_keeps_user_modified_copy(tmp_path):
    """用户改过的副本按既有优先级继续覆盖内置，不得搬走。"""
    from home_migration import retire_superseded_installs

    home = tmp_path / "home"
    installed = home / "data" / "installed_plugins"
    (installed / "branch-atlas").mkdir(parents=True)
    (installed / "kb.py").write_text("user-edited", encoding="utf-8")
    (installed / "branch-atlas" / "__init__.py").write_text("NAME='branch-atlas'", encoding="utf-8")
    (installed / "branch-atlas" / "extra.py").write_text("# user", encoding="utf-8")
    (installed / "wallpaper").mkdir(parents=True)
    (installed / "wallpaper" / "__init__.py").write_text("NAME='wallpaper'", encoding="utf-8")
    _write_marker(home, {
        "kb.py": hashlib.sha256(b"kb-v1").hexdigest(),
        "branch-atlas/__init__.py": hashlib.sha256(b"NAME='branch-atlas'").hexdigest(),
    })

    result = retire_superseded_installs(home)

    assert result["retired"] == 0
    assert (installed / "kb.py").read_text(encoding="utf-8") == "user-edited"
    assert (installed / "branch-atlas" / "extra.py").is_file()
    # 外部插件不在台账里，绝不能被当成随包副本。
    assert (installed / "wallpaper" / "__init__.py").is_file()
    assert not (installed / "_retired").exists()


def test_release_spec_ships_bundled_plugins_and_isolates_wallpaper():
    spec = (ROOT / "deploy/brain/vigil-brain.spec").read_text(encoding="utf-8")
    launcher = (ROOT / "brain/server_launch.py").read_text(encoding="utf-8")
    paths = (ROOT / "brain/paths.py").read_text(encoding="utf-8")
    host = (ROOT / "brain/plugin_host.py").read_text(encoding="utf-8")
    build = (ROOT / "scripts/build_release.py").read_text(encoding="utf-8")

    # 内置插件随包：从 git 取受控文件，显式排除 wallpaper。
    assert '["git", "ls-files", "-z", "--", "brain/plugins"]' in spec
    assert 'if rel_file == "wallpaper" or rel_file.startswith("wallpaper/")' in spec
    assert "datas=core_feature_data + plugins_data + skill_host_data" in spec
    assert '"kb.py", "gate.py", "react.py", "websearch.py"' in spec
    # 与 core_features 重名的镜像不进包。
    assert 'BUNDLED_PLUGIN_EXCLUDE = {"plugin_market.py", "remote.py", "skill.py"}' in spec
    # 核心适配器仍是允许清单制。
    assert "CORE_FEATURE_ALLOWLIST = {" in spec
    assert "if seen_core_features != CORE_FEATURE_ALLOWLIST" in spec
    for optional in ("gate.py", "kb.py", "react.py", "websearch.py"):
        assert not (ROOT / "brain/core_features" / optional).exists()

    # 启动时先迁移旧 home 再铺内置，顺序反了会用旧文件盖掉新版。
    assert "def _seed_core_features()" in launcher
    assert "seed_plugins(bundled, HOME_DIR, legacy_roots)" in launcher
    assert launcher.index("migrate_legacy_plugins(HOME_DIR, legacy_roots)") < \
        launcher.index("seed_plugins(bundled, HOME_DIR, legacy_roots)")

    assert "CORE_FEATURES_DIR = os.path.join(ROOT, \"core_features\")" in paths
    dirs = paths.split("_DIRS = (", 1)[1].split(")", 1)[0]
    assert "CORE_FEATURES_DIR" in dirs and "PLUGINS_DIR" in dirs
    assert "from paths import PLUGINS_DIR, CORE_FEATURES_DIR" in host
    assert "_SHADOWED_BUILTINS = frozenset(" in host
    assert "assert_no_bundled_plugin_tree(OUT)" in build


def test_client_package_contract_regression_runs_under_python_ci():
    completed = subprocess.run(
        ["node", "--test", "client/tools/test-plugin-package-boundary.js"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
