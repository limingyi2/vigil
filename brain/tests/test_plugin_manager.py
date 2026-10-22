import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import plugin_manager
import plugin_ops
import plugin_host


def _archive(path, version="1.0.0", body=b"NAME='demo'\nDESC='demo'\n"):
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("demo/__init__.py", body)
        zf.writestr("demo/zxplugin.json", json.dumps({
            "name": "demo", "version": version,
            "dependencies": {}, "permissions": [],
        }))


def test_manager_install_duplicate_update_uninstall_preserves_data(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    _archive(first, "1.0.0")
    _archive(second, "2.0.0", b"NAME='demo'\nDESC='updated'\n")
    with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
         patch.object(plugin_ops, "REGISTRY", str(target / "_registry.json")), \
         patch.object(plugin_manager, "_reload", return_value=True):
        ok, result = plugin_manager.install_archive(str(first), reload=False)
        assert ok, result
        duplicate, _ = plugin_manager.install_archive(str(first), reload=False)
        assert not duplicate
        ok, result = plugin_manager.update("demo", str(second), reload=False)
        assert ok, result
        assert json.loads((target / "demo" / "zxplugin.json").read_text())["version"] == "2.0.0"
        ok, result = plugin_manager.uninstall("demo", purge_data=False)
        assert ok, result
        assert not (target / "demo").exists()


def test_manager_update_failure_reports_rollback_and_keeps_old(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    old = target / "demo"
    old.mkdir()
    (old / "__init__.py").write_text("old", encoding="utf-8")
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "enabled": True}}}), encoding="utf-8")
    broken = tmp_path / "broken.zip"
    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("demo/__init__.py", "def broken(:\n")
    with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
         patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_manager, "_reload", return_value=True):
        ok, result = plugin_manager.update("demo", str(broken), reload=False)
        assert not ok
        assert (old / "__init__.py").read_text(encoding="utf-8") == "old"
        assert result["state"] == "error"


def test_manager_reload_failure_restores_files_registry_and_old_runtime(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    old = target / "demo"
    old.mkdir()
    (old / "__init__.py").write_text("old", encoding="utf-8")
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "enabled": True}}}), encoding="utf-8")
    newer = tmp_path / "new.zip"
    _archive(newer, "2.0.0", b"NAME='demo'\nDESC='new'\n")
    with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
         patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_manager, "_reload", side_effect=[False, True]) as reload:
        ok, result = plugin_manager.update("demo", str(newer), reload=True)
    assert not ok
    assert result["rollback"] == "restored"
    assert "均已恢复" in result["rollback_diagnostic"]
    assert (old / "__init__.py").read_text(encoding="utf-8") == "old"
    assert json.loads(registry.read_text(encoding="utf-8"))["plugins"]["demo"]["version"] == "1.0.0"
    assert reload.call_count == 2


def test_manager_reports_and_keeps_backup_when_restore_fails(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    old = target / "demo"
    old.mkdir()
    (old / "__init__.py").write_text("old", encoding="utf-8")
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {"version": "1.0.0"}}}), encoding="utf-8")
    newer = tmp_path / "new.zip"
    _archive(newer, "2.0.0")
    with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
         patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_manager, "_reload", return_value=False), \
         patch.object(plugin_manager, "_restore_backup", return_value=(False, "permission denied")):
        ok, result = plugin_manager.update("demo", str(newer), reload=True)
    assert not ok
    assert result["rollback"] == "failed"
    assert "permission denied" in result["rollback_diagnostic"]
    assert result["recovery_path"]


def test_enable_requires_running_brain_for_start_hook(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "enabled": False,
        "manifest": {"feature": "demo", "hooks": ["start"]}}}}), encoding="utf-8")
    with patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_manager, "_reload", return_value=False):
        ok, result = plugin_manager.enable("demo")
    assert not ok
    assert result["state"] == "restart_required"
    assert json.loads(registry.read_text(encoding="utf-8"))["plugins"]["demo"]["state"] == "restart_required"


def test_host_start_all_executes_start_hook_with_runtime_brain():
    calls = []

    class FakePlugin:
        name = "demo"

        @staticmethod
        def hook(name):
            return (lambda brain: calls.append(brain)) if name == "start" else None

    old_registry, old_started, old_brain = plugin_host._registry, plugin_host._started, plugin_host._runtime_brain
    try:
        plugin_host._registry = {"demo": FakePlugin()}
        plugin_host._started = set()
        plugin_host._runtime_brain = None
        with patch.object(plugin_host, "_enabled", return_value=True):
            brain = object()
            assert plugin_host.start_all(brain) == 1
            assert calls == [brain]
            assert plugin_host.runtime_brain() is brain
    finally:
        plugin_host._registry, plugin_host._started, plugin_host._runtime_brain = old_registry, old_started, old_brain


def test_manager_status_exposes_market_update_state(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "permissions": ["x"], "enabled": True}}}), encoding="utf-8")
    with patch.object(plugin_ops, "REGISTRY", str(registry)):
        value = plugin_manager.status("demo", "1.1.0")
    assert value["update_state"] == "update_available"
    assert value["permissions"] == ["x"]
