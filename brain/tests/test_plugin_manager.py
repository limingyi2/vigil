import json
import zipfile
import time
import types
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
            assert plugin_host.start_all(brain) == 0
            assert calls == [brain]
            assert plugin_host.runtime_brain() is brain
    finally:
        plugin_host._registry, plugin_host._started, plugin_host._runtime_brain = old_registry, old_started, old_brain


def test_stop_running_start_only_plugin_refuses_operation():
    class FakePlugin:
        @staticmethod
        def hook(name):
            return (lambda brain: None) if name == "start" else None

    with patch.object(plugin_host, "get", return_value=FakePlugin()), \
         patch.object(plugin_host, "is_started", return_value=True), \
         patch.object(plugin_host, "stop") as stop:
        ok, detail = plugin_manager._stop("demo")
    assert not ok
    assert "本次操作未执行" in detail
    assert "重启 Brain" in detail
    stop.assert_not_called()


def test_stop_loaded_but_not_started_plugin_allows_operation_after_restart():
    class FakePlugin:
        @staticmethod
        def hook(name):
            return (lambda brain: None) if name == "start" else None

    with patch.object(plugin_host, "get", return_value=FakePlugin()), \
         patch.object(plugin_host, "is_started", return_value=False), \
         patch.object(plugin_host, "stop") as stop:
        ok, detail = plugin_manager._stop("demo")
    assert ok
    assert detail == ""
    stop.assert_not_called()


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


def test_update_without_stop_hook_requires_restart_instead_of_forcing_replace(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    old = target / "demo"
    old.mkdir()
    (old / "__init__.py").write_text("old", encoding="utf-8")
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "enabled": True}}}), encoding="utf-8")
    newer = tmp_path / "new.zip"
    _archive(newer, "2.0.0")
    with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
         patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_manager, "_stop", return_value=(False, "插件提供 start(brain) 但没有 stop/shutdown/close；需要重启 Brain")):
        ok, result = plugin_manager.update("demo", str(newer), reload=True)
    assert not ok
    assert result["state"] == "restart_required"
    assert result["restart_needed"] is True
    assert (old / "__init__.py").read_text(encoding="utf-8") == "old"


def test_update_loaded_but_disabled_plugin_after_restart_is_allowed(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    old = target / "demo"
    old.mkdir()
    (old / "__init__.py").write_text("old", encoding="utf-8")
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "enabled": False}}}), encoding="utf-8")
    newer = tmp_path / "new.zip"
    _archive(newer, "2.0.0")

    class FakePlugin:
        @staticmethod
        def hook(name):
            return (lambda brain: None) if name == "start" else None

    with patch.object(plugin_ops, "_PLUG_DIR", str(target)), \
         patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_host, "get", return_value=FakePlugin()), \
         patch.object(plugin_host, "is_started", return_value=False), \
         patch.object(plugin_manager, "_reload", return_value=True):
        ok, result = plugin_manager.update("demo", str(newer), reload=True)
    assert ok, result
    assert json.loads((target / "demo" / "zxplugin.json").read_text(encoding="utf-8"))["version"] == "2.0.0"


def test_manager_status_exposes_market_update_state(tmp_path):
    target = tmp_path / "installed"
    target.mkdir()
    (target / "demo.py").write_text("NAME='demo'\n", encoding="utf-8")
    registry = target / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"demo": {
        "version": "1.0.0", "permissions": ["x"], "enabled": True}}}), encoding="utf-8")
    with patch.object(plugin_ops, "REGISTRY", str(registry)), \
         patch.object(plugin_host, "_INSTALLED_DIR", str(target)):
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
        assert assets["plugins"] == []
    finally:
        plugin_host._registry = old_registry


def test_feature_toggle_uses_real_current_channel(tmp_path):
    import features
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"features": {"packages": True},
                                  "features_by_src": {
                                      "pc": {"packages": False},
                                      "wechat": {"packages": True}}}),
                      encoding="utf-8")
    previous = features._CFG
    try:
        features._CFG = str(config)
        features.invalidate()
        with features.with_channel("pc"):
            assert features.current_src() == "pc"
            assert features.on("packages") is False
        with features.with_channel("wechat"):
            assert features.current_src() == "wechat"
            assert features.on("packages") is True
        assert features.current_src() == "pc"
        assert features.on("packages") is False
    finally:
        features._CFG = previous
        features.invalidate()


def _plugin_fixture(name, path, *, start=False, frontend=False):
    mod = types.ModuleType("fixture_" + name)
    mod.NAME = name
    mod.DESC = name
    mod.FEATURE = ""
    calls = []
    mod.TOOLS = {name + "_tool": lambda arg: calls.append(arg) or "called"}
    mod.TOOL_SPECS = {name + "_tool": {"desc": "fixture", "params": []}}
    mod.prompt = lambda ctx: calls.append("prompt") or "fixture prompt"
    mod.on_turn_in = lambda ctx: calls.append("turn") or "fixture turn"
    mod.on_reply = lambda ctx, value: calls.append("reply") or value + "!"
    if start:
        mod.start = lambda brain: calls.append("start")
    if frontend:
        (Path(path).parent / "frontend.js").write_text("window.fixture = true", encoding="utf-8")
        (Path(path).parent / "frontend.css").write_text(".fixture{}", encoding="utf-8")
    mod.ROUTES = [("GET", "/api/plugin/" + name, lambda body, query: {"ok": True})]
    return plugin_host.Plugin(mod, str(path)), calls


def test_lifecycle_blocks_all_surfaces_then_uninstalls_without_purging_data(tmp_path):
    user_plugins = tmp_path / "installed_plugins"
    user_plugins.mkdir()
    source = user_plugins / "wallpaper"
    source.mkdir()
    entry = source / "__init__.py"
    entry.write_text("# fixture", encoding="utf-8")
    data = tmp_path / "plugins" / "wallpaper"
    data.mkdir(parents=True)
    saved = data / "settings.json"
    saved.write_text("{}", encoding="utf-8")
    registry = tmp_path / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"wallpaper": {
        "version": "1.0.0", "enabled": True}}}), encoding="utf-8")
    fake, calls = _plugin_fixture("wallpaper", entry, frontend=True)
    old_registry, old_index, old_started = plugin_host._registry, dict(plugin_host._TOOL_INDEX), plugin_host._started
    plugin_host._registry = {"wallpaper": fake}
    plugin_host._started = set()
    plugin_host._reindex_tools()

    def reload_target(name=None, brain=None, allow_missing=False):
        if allow_missing:
            plugin_host._registry.pop(name, None)
            plugin_host._reindex_tools()
        return True

    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(user_plugins)), \
             patch.object(plugin_host, "_DIR", str(tmp_path / "builtin")), \
             patch.object(plugin_ops, "_PLUG_DIR", str(user_plugins)), \
             patch.object(plugin_ops, "REGISTRY", str(registry)), \
             patch.object(plugin_manager, "_reload", side_effect=reload_target):
            assert plugin_host.is_enabled("wallpaper")
            assert plugin_host.frontend_assets()["plugins"]
            assert plugin_host.tool_specs().get("wallpaper_tool")
            assert plugin_manager.disable("wallpaper")[0]
            assert not plugin_host.is_enabled("wallpaper")
            assert plugin_host.frontend_assets()["plugins"] == []
            assert "wallpaper_tool" not in plugin_host.tool_specs()
            assert plugin_host.run_tool("wallpaper_tool", "x") is None
            assert plugin_host.prompt_block() == ""
            assert plugin_host.on_turn_in(plugin_host.Turn()) == ""
            assert plugin_host.on_reply(plugin_host.Turn(), "reply") == "reply"
            assert plugin_manager.enable("wallpaper")[0]
            assert plugin_host.is_enabled("wallpaper")
            assert plugin_host.frontend_assets()["plugins"]
            ok, result = plugin_manager.uninstall("wallpaper")
            assert ok, result
            assert not source.exists()
            assert saved.read_text(encoding="utf-8") == "{}"
            assert not plugin_host.get("wallpaper")
            # A fresh loader sees neither code nor an enabled registry entry.
            assert plugin_manager.status("wallpaper")["installed"] is False
    finally:
        plugin_host._registry = old_registry
        plugin_host._started = old_started
        plugin_host._TOOL_INDEX.clear()
        plugin_host._TOOL_INDEX.update(old_index)


def test_builtin_is_disableable_but_never_uninstallable(tmp_path):
    import zipfile
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    entry = builtin / "coreplug.py"
    entry.write_text("# fixture", encoding="utf-8")
    runtime, _calls = _plugin_fixture("coreplug", entry)
    registry = tmp_path / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {}}), encoding="utf-8")
    old_registry = plugin_host._registry
    plugin_host._registry = {"coreplug": runtime}
    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(tmp_path / "installed")), \
             patch.object(plugin_host, "_DIR", str(builtin)), \
             patch.object(plugin_ops, "REGISTRY", str(registry)), \
             patch.object(plugin_ops, "_PLUG_DIR", str(tmp_path / "installed")), \
             patch.object(plugin_manager, "_reload", return_value=True):
            status = plugin_manager.status("coreplug")
            assert status["builtin"] and not status["removable"]
            assert plugin_manager.disable("coreplug")[0]
            ok, result = plugin_manager.uninstall("coreplug")
            assert not ok and result["state"] == "builtin"
            assert entry.exists()
            archive = tmp_path / "coreplug.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("coreplug/__init__.py", "NAME='coreplug'\n")
                zf.writestr("coreplug/zxplugin.json", json.dumps({
                    "name": "coreplug", "version": "2.0.0",
                    "dependencies": {}, "permissions": []}))
            ok, result = plugin_manager.install_archive(str(archive), reload=False)
            assert not ok and result["state"] == "builtin"
            ok, result = plugin_manager.update("coreplug", str(archive), reload=False)
            assert not ok and result["state"] == "builtin"
            assert not (tmp_path / "installed" / "coreplug").exists()
    finally:
        plugin_host._registry = old_registry


def test_background_plugin_with_stop_hook_is_stopped_on_disable(tmp_path):
    installed = tmp_path / "installed"
    installed.mkdir()
    entry = installed / "threaded.py"
    entry.write_text("# fixture", encoding="utf-8")
    registry = tmp_path / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"threaded": {
        "enabled": True}}}), encoding="utf-8")
    runtime, calls = _plugin_fixture("threaded", entry, start=True)
    runtime.mod.stop = lambda: calls.append("stop")
    old_registry, old_started = plugin_host._registry, plugin_host._started
    plugin_host._registry, plugin_host._started = {"threaded": runtime}, {"threaded"}
    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(installed)), \
             patch.object(plugin_host, "_DIR", str(tmp_path / "builtin")), \
             patch.object(plugin_ops, "REGISTRY", str(registry)), \
             patch.object(plugin_ops, "_PLUG_DIR", str(installed)), \
             patch.object(plugin_manager, "_reload", return_value=True):
            ok, result = plugin_manager.disable("threaded")
            assert ok, result
            assert calls == ["stop"]
            assert "threaded" not in plugin_host.started_names()
            assert json.loads(registry.read_text(encoding="utf-8"))["plugins"]["threaded"]["enabled"] is False
    finally:
        plugin_host._registry, plugin_host._started = old_registry, old_started


def test_start_only_disable_persists_until_restart_and_blocks_uninstall(tmp_path):
    user_plugins = tmp_path / "installed"
    user_plugins.mkdir()
    entry = user_plugins / "worker.py"
    entry.write_text("# fixture", encoding="utf-8")
    registry = tmp_path / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {"worker": {
        "version": "1.0.0", "enabled": True}}}), encoding="utf-8")
    runtime, _calls = _plugin_fixture("worker", entry, start=True)
    old_registry, old_started = plugin_host._registry, plugin_host._started
    plugin_host._registry, plugin_host._started = {"worker": runtime}, {"worker"}
    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(user_plugins)), \
             patch.object(plugin_host, "_DIR", str(tmp_path / "builtin")), \
             patch.object(plugin_ops, "REGISTRY", str(registry)), \
             patch.object(plugin_ops, "_PLUG_DIR", str(user_plugins)), \
             patch.object(plugin_manager, "_reload", return_value=True):
            ok, result = plugin_manager.disable("worker")
            assert not ok and result["state"] == "restart_required"
            assert json.loads(registry.read_text(encoding="utf-8"))["plugins"]["worker"]["enabled"] is False
            pending = json.loads((user_plugins / "_pending_restart.json").read_text(encoding="utf-8"))
            assert pending["changed_at"] > pending["started_at"]
            assert plugin_manager.status("worker")["state"] == "restart_required"
            ok, result = plugin_manager.uninstall("worker")
            assert not ok and result["state"] == "restart_required"
            assert entry.exists()

            # Simulate a successful Brain restart: the disabled start hook is
            # skipped, then the persistent pending marker is cleared by startup time.
            plugin_host._started = set()
            time.sleep(0.01)
            import restart_state
            restart_state.note_start("plugin", str(user_plugins))
            assert plugin_manager.status("worker")["state"] == "disabled"
            assert restart_state.snapshot("plugin", str(user_plugins))["pending"] is False
            assert plugin_manager.uninstall("worker")[0]
            assert not entry.exists()
    finally:
        plugin_host._registry, plugin_host._started = old_registry, old_started


def test_uninstall_stop_hook_failure_keeps_files_and_installed_status(tmp_path):
    user_plugins = tmp_path / "installed"
    user_plugins.mkdir()
    entry = user_plugins / "blocked.py"
    entry.write_text("# fixture", encoding="utf-8")
    registry = user_plugins / "_registry.json"
    initial = {"version": 1, "plugins": {"blocked": {"version": "1.0.0", "enabled": True}}}
    registry.write_text(json.dumps(initial), encoding="utf-8")
    runtime, _calls = _plugin_fixture("blocked", entry, start=True)
    def fail_stop():
        raise RuntimeError("fixture stop failure")
    runtime.mod.stop = fail_stop
    old_registry, old_started = plugin_host._registry, plugin_host._started
    plugin_host._registry, plugin_host._started = {"blocked": runtime}, {"blocked"}
    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(user_plugins)), \
             patch.object(plugin_host, "_DIR", str(tmp_path / "builtin")), \
             patch.object(plugin_ops, "_PLUG_DIR", str(user_plugins)), \
             patch.object(plugin_ops, "REGISTRY", str(registry)):
            ok, result = plugin_manager.uninstall("blocked")
            assert not ok
            assert "fixture stop failure" in result["err"]
            assert result["state"] == "error"
            assert result["restart_needed"] is False
            assert entry.exists()
            assert json.loads(registry.read_text(encoding="utf-8")) == initial
            assert plugin_manager.status("blocked")["installed"] is True
    finally:
        plugin_host._registry, plugin_host._started = old_registry, old_started


def test_uninstall_reloads_remaining_plugins_without_missing_target_warning():
    with patch.object(plugin_manager, "_valid_name", return_value=True), \
         patch.object(plugin_manager, "_stop", return_value=(True, "")), \
         patch.object(plugin_ops, "uninstall", return_value=(True, {"name": "demo"})), \
         patch.object(plugin_manager, "_reload", return_value=True):
        ok, result = plugin_manager.uninstall("demo")
    assert ok
    assert "runtime_warning" not in result


def test_registry_orphan_does_not_report_plugin_as_installed(tmp_path):
    user_plugins = tmp_path / "installed"
    builtin_plugins = tmp_path / "builtin"
    user_plugins.mkdir()
    builtin_plugins.mkdir()
    registry = user_plugins / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {
        "ghost": {"version": "1.0.0", "state": "error"}}}), encoding="utf-8")
    old_registry = plugin_host._registry
    plugin_host._registry = {}
    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(user_plugins)), \
             patch.object(plugin_host, "_DIR", str(builtin_plugins)), \
             patch.object(plugin_ops, "_PLUG_DIR", str(user_plugins)), \
             patch.object(plugin_ops, "REGISTRY", str(registry)):
            status = plugin_manager.status("ghost", "2.0.0")
        assert status["installed"] is False
        assert status["source_kind"] == "unknown"
        assert status["removable"] is False
        assert status["state"] == "not_installed"
        assert status["update_state"] == "not_installed"
    finally:
        plugin_host._registry = old_registry


def test_uninstall_user_overlay_restores_builtin_tools_and_routes(tmp_path):
    user_plugins = tmp_path / "installed"
    builtin_plugins = tmp_path / "builtin"
    user_plugins.mkdir()
    builtin_plugins.mkdir()
    user_file = user_plugins / "overlay.py"
    builtin_file = builtin_plugins / "overlay.py"
    user_file.write_text(
        'NAME="overlay"\nTOOLS={"user_tool": lambda arg: "user"}\n'
        'TOOL_SPECS={"user_tool": {"desc":"user", "params":[]}}\n'
        'ROUTES=[("GET", "/api/user-overlay", lambda body, query: {"ok": True})]\n',
        encoding="utf-8")
    builtin_file.write_text(
        'NAME="overlay"\nTOOLS={"builtin_tool": lambda arg: "builtin"}\n'
        'TOOL_SPECS={"builtin_tool": {"desc":"builtin", "params":[]}}\n'
        'ROUTES=[("GET", "/api/builtin-overlay", lambda body, query: {"ok": True})]\n',
        encoding="utf-8")
    registry = user_plugins / "_registry.json"
    registry.write_text(json.dumps({"version": 1, "plugins": {
        "overlay": {"version": "2.0.0", "enabled": True}}}), encoding="utf-8")
    old_registry = plugin_host._registry
    old_started = plugin_host._started
    old_index = dict(plugin_host._TOOL_INDEX)
    plugin_host._registry = {}
    plugin_host._started = set()
    try:
        with patch.object(plugin_host, "_INSTALLED_DIR", str(user_plugins)), \
             patch.object(plugin_host, "_DIR", str(builtin_plugins)), \
             patch.object(plugin_ops, "_PLUG_DIR", str(user_plugins)), \
             patch.object(plugin_ops, "REGISTRY", str(registry)):
            plugin_host.load(force=True, only="overlay")
            assert plugin_host.get("overlay").path == str(user_file)
            assert "/api/user-overlay" in [route[1] for route in plugin_host.route_table()]
            ok, result = plugin_manager.uninstall("overlay")
            assert ok, result
            assert not user_file.exists()
            assert plugin_host.get("overlay").path == str(builtin_file)
            assert "/api/user-overlay" not in [route[1] for route in plugin_host.route_table()]
            assert "/api/builtin-overlay" in [route[1] for route in plugin_host.route_table()]
            assert "user_tool" not in plugin_host.tool_specs()
            assert "builtin_tool" in plugin_host.tool_specs()
            assert result["state"] == "builtin_restored"
            assert result["source_kind"] == "builtin"
            assert result["installed"] is True
            assert result["removable"] is False
    finally:
        plugin_host._registry = old_registry
        plugin_host._started = old_started
        plugin_host._TOOL_INDEX.clear()
        plugin_host._TOOL_INDEX.update(old_index)


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
