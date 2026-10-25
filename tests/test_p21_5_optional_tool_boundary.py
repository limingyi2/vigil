"""Optional tools are exposed only through installed Plugin Host packages."""
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import features
import plugin_host
import plugin_ops
import tools


@pytest.fixture
def isolated_plugin_runtime(tmp_path, monkeypatch):
    installed = tmp_path / "data" / "installed_plugins"
    installed.mkdir(parents=True)
    core = tmp_path / "core_features"
    core.mkdir()
    feature_values = {"tools": True}
    registry = {"plugins": {}}
    monkeypatch.setattr(plugin_host, "_INSTALLED_DIR", str(installed))
    monkeypatch.setattr(plugin_host, "_CORE_PLUGIN_ROOT", str(core))
    monkeypatch.setattr(features, "on", lambda key, src=None: feature_values.get(key, True))
    monkeypatch.setattr(features, "register", lambda key, default=True: True)
    monkeypatch.setattr(plugin_ops, "read_registry", lambda: registry)

    plugin_host.load(force=True)
    yield installed, feature_values, registry
    monkeypatch.undo()
    plugin_host.load(force=True)


def _names(src="pc"):
    return {entry["function"]["name"] for entry in tools.all_specs(src=src)}


def _install_sources(installed, *names):
    for name in names:
        shutil.copy2(ROOT / "brain" / "plugins" / (name + ".py"),
                     installed / (name + ".py"))
    plugin_host.load(force=True)


def test_clean_install_hides_optional_tools_even_when_unknown_features_default_on(
        isolated_plugin_runtime):
    _installed, _feature_values, _registry = isolated_plugin_runtime
    names = _names()
    assert {"weather", "tracking", "capability_status", "capability_call"} <= names
    assert not ({"search", "reminder_set", "reminder_list", "reminder_cancel", "kb_search"} & names)
    assert features.on("websearch", "pc") is True  # unknown feature default is not install evidence
    assert not tools.static_tool_enabled("search", "pc")
    result = tools.invoke_readonly_spec("search", {"query": "should not run"},
                                        SimpleNamespace(src="pc"))
    assert result.ok is False
    assert "未安装或未加载" in result.text


def test_plugin_cannot_expose_unregistered_tools_with_a_stale_schema(
        isolated_plugin_runtime):
    installed, _feature_values, _registry = isolated_plugin_runtime
    (installed / "stale.py").write_text(
        "NAME='stale'\nDESC='fixture'\nTOOLS={}\n"
        "TOOL_SPECS={'ghost_tool': {'desc': 'must not leak', 'params': [], 'arg_param': None}}\n",
        encoding="utf-8")
    plugin_host.load(force=True)
    assert "ghost_tool" not in _names()
    assert not tools.static_tool_enabled("ghost_tool", "pc")


def test_installed_optional_tools_appear_disable_and_disappear_on_uninstall_and_reload(
        isolated_plugin_runtime):
    installed, feature_values, registry = isolated_plugin_runtime
    _install_sources(installed, "websearch", "reminder", "kb")
    names = _names()
    assert {"search", "reminder_set", "reminder_list", "reminder_cancel", "kb_search"} <= names
    assert tools.static_tool_enabled("search", "pc")
    assert tools.static_tool_enabled("reminder_set", "pc")

    # The standard plugin feature gate removes schemas and blocks dispatch.
    feature_values["websearch"] = False
    assert "search" not in _names()
    assert not tools.static_tool_enabled("search", "pc")
    disabled = tools.invoke_readonly_spec("search", {"query": "x"},
                                          SimpleNamespace(src="pc"))
    assert disabled.ok is False and "已禁用" in disabled.text
    feature_values["websearch"] = True

    # Plugin Manager state also removes the tools without relying on FEATURES.
    registry["plugins"]["reminder"] = {"enabled": False}
    assert not ({"reminder_set", "reminder_list", "reminder_cancel"} & _names())
    registry["plugins"].clear()

    # Uninstall removes source, and a later host reload must not resurrect it.
    for name in ("websearch", "reminder", "kb"):
        (installed / (name + ".py")).unlink()
    plugin_host.load(force=True)
    plugin_host.load(force=True)  # restart/reload path
    names = _names()
    assert not ({"search", "reminder_set", "reminder_list", "reminder_cancel", "kb_search"} & names)
    assert not tools.static_tool_enabled("search", "pc")


def test_search_structured_dispatch_uses_loaded_plugin_and_retains_sources(
        isolated_plugin_runtime):
    installed, _feature_values, _registry = isolated_plugin_runtime
    _install_sources(installed, "websearch")
    owner = plugin_host.get_by_tool("search")
    assert owner is not None
    owner.mod._mem.clear()
    with patch.object(owner.mod, "_post", return_value=[{
            "title": "Official source", "url": "https://example.test/source",
            "snippet": "verified detail"}]) as request:
        outcome = tools.invoke_readonly_spec("search", {"query": "fixture query"},
                                             SimpleNamespace(src="pc"))
    request.assert_called_once()
    assert outcome.ok is True
    assert "https://example.test/source" in outcome.text


def test_pre_p21_5_installed_plugins_keep_tools_through_host_compatibility_adapter(
        isolated_plugin_runtime):
    installed, _feature_values, _registry = isolated_plugin_runtime
    _install_sources(installed, "websearch", "reminder")
    search = plugin_host.get_by_tool("search")
    reminder = plugin_host.get_by_tool("reminder_set")
    del search.mod.TOOL_SPECS
    del search.mod.READONLY_TOOLS
    del reminder.mod.TOOL_SPECS

    names = _names()
    assert {"search", "reminder_set", "reminder_list", "reminder_cancel"} <= names
    search.mod._mem.clear()
    with patch.object(search.mod, "_post", return_value=[{
            "title": "Legacy source", "url": "https://example.test/legacy",
            "snippet": "still installed"}]) as request:
        outcome = tools.invoke_readonly_spec("search", {"query": "legacy plugin"},
                                             SimpleNamespace(src="pc"))
    request.assert_called_once()
    assert outcome.ok is True
    assert "https://example.test/legacy" in outcome.text
