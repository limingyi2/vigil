import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
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
    persisted = json.loads(registry.read_text(encoding="utf-8"))["plugins"]["demo"]
    assert "permission denied" in persisted["last_error"]
    assert result["recovery_path"] in persisted["last_error"]
    assert persisted["recovery_path"] == result["recovery_path"]


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


def test_host_stop_missing_plugin_keeps_running_brain_reference():
    brain = object()
    old_registry, old_started, old_brain = plugin_host._registry, plugin_host._started, plugin_host._runtime_brain
    try:
        plugin_host._registry = {}
        plugin_host._started = {"removed"}
        plugin_host._runtime_brain = brain
        assert plugin_host.stop("removed")[0]
        assert plugin_host.runtime_brain() is brain
    finally:
        plugin_host._registry, plugin_host._started, plugin_host._runtime_brain = old_registry, old_started, old_brain


def test_manager_reload_rejects_failed_start_hook():
    class FakePlugin:
        name = "demo"

        @staticmethod
        def hook(name):
            return (lambda brain: None) if name == "start" else None

    brain = object()
    with patch.object(plugin_host, "started_names", return_value=set()), \
         patch.object(plugin_host, "stop", return_value=(True, [])), \
         patch.object(plugin_host, "load", return_value={"demo": FakePlugin()}), \
         patch.object(plugin_host, "runtime_brain", return_value=brain), \
         patch.object(plugin_host, "is_enabled", return_value=True), \
         patch.object(plugin_host, "start_all", return_value={"count": 0, "started": [], "failed": ["demo"]}), \
         patch.object(plugin_host, "is_started", return_value=False):
        try:
            plugin_manager._reload("demo")
        except RuntimeError as exc:
            assert "start(brain)" in str(exc)
        else:
            raise AssertionError("failed start hook must fail reload")


def test_update_start_hook_failure_restores_old_version(tmp_path):
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
         patch.object(plugin_manager, "_reload", side_effect=[RuntimeError("start(brain) failed"), True]):
        ok, result = plugin_manager.update("demo", str(newer), reload=True)
    assert not ok
    assert result["rollback"] == "restored"
    assert (old / "__init__.py").read_text(encoding="utf-8") == "old"
    assert json.loads(registry.read_text(encoding="utf-8"))["plugins"]["demo"]["version"] == "1.0.0"


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


def test_featureless_plugin_disable_is_consistent_in_listing_and_frontend(tmp_path):
    class FakePlugin:
        name = "demo"
        desc = "demo"
        feature = ""
        tools = {}
        routes = []
        frontend_js = "window.demo = true;"
        frontend_css = ""
        err = ""

        def info(self):
            return {"name": self.name, "desc": self.desc, "feature": self.feature,
                    "tools": [], "routes": [], "hooks": [], "file": "demo.py", "err": ""}

        def hook(self, _name):
            return None

    registry = tmp_path / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "enabled": False}}}), encoding="utf-8")
    old_registry = plugin_host._registry
    try:
        plugin_host._registry = {"demo": FakePlugin()}
        with patch.object(plugin_ops, "REGISTRY", str(registry)):
            listed = plugin_host.listing()
            assets = plugin_host.frontend_assets()
        assert listed[0]["enabled"] is False
        assert assets["plugins"][0]["enabled"] is False
    finally:
        plugin_host._registry = old_registry


def test_uninstall_reloads_remaining_plugins_without_missing_target_warning():
    with patch.object(plugin_manager, "_valid_name", return_value=True), \
         patch.object(plugin_manager, "_stop", return_value=(True, "")), \
         patch.object(plugin_ops, "uninstall", return_value=(True, {"name": "demo"})), \
         patch.object(plugin_manager, "_reload", return_value=True):
        ok, result = plugin_manager.uninstall("demo")
    assert ok
    assert "runtime_warning" not in result


def test_targeted_lifecycle_keeps_real_background_plugins_running(tmp_path):
    """reminder/kb/skill have no stop hook; ordinary plugin operations must not block."""
    source_plugins = Path(__file__).resolve().parents[1] / "plugins"
    actual = {}
    for name in ("reminder", "kb", "skill"):
        actual[name] = plugin_host._load_one(str(source_plugins / (name + ".py")))
        assert actual[name] is not None

    class NoopThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    target = tmp_path / "installed"
    target.mkdir()
    registry = target / "_registry.json"
    old_registry = plugin_host._registry
    old_started = plugin_host._started
    old_brain = plugin_host._runtime_brain
    old_dir = plugin_host._DIR
    old_installed = plugin_host._INSTALLED_DIR
    try:
        plugin_host._registry = actual
        plugin_host._started = set()
        plugin_host._runtime_brain = None
        plugin_host._DIR = str(source_plugins)
        plugin_host._INSTALLED_DIR = str(target)
        brain = object()
        with patch.object(plugin_host, "_enabled", return_value=True), \
             patch.object(actual["reminder"].mod, "threading", SimpleNamespace(Thread=NoopThread)), \
             patch.object(actual["skill"].mod, "host_alive", return_value=False):
            assert plugin_host.start_all(brain, names=["reminder", "kb", "skill"]) == 3
            identities = {name: plugin_host.get(name) for name in actual}
            assert plugin_host.runtime_brain() is brain

            first = tmp_path / "one.zip"
            second = tmp_path / "two.zip"
            _archive(first, "1.0.0")
            _archive(second, "2.0.0", b"NAME='demo'\nDESC='updated'\n")
            with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
                 patch.object(plugin_ops, "REGISTRY", str(registry)):
                ok, result = plugin_manager.install_archive(str(first), reload=True)
                assert ok, result
                ok, result = plugin_manager.update("demo", str(second), reload=True)
                assert ok, result
                ok, result = plugin_manager.uninstall("demo")
                assert ok, result

            assert plugin_host.runtime_brain() is brain
            assert plugin_host.started_names() == set(actual)
            assert all(plugin_host.get(name) is identities[name] for name in actual)
    finally:
        plugin_host._registry = old_registry
        plugin_host._started = old_started
        plugin_host._runtime_brain = old_brain
        plugin_host._DIR = old_dir
        plugin_host._INSTALLED_DIR = old_installed
