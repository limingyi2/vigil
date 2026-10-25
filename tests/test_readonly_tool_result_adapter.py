"""Structured result status for ordinary-chat read-only Function Calling."""
import sys
import shutil
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "brain"))

import tools
import features
import plugin_host
import plugin_ops


@pytest.fixture(autouse=True)
def installed_websearch(tmp_path, monkeypatch):
    installed = tmp_path / "installed_plugins"
    installed.mkdir()
    shutil.copy2(ROOT / "brain/plugins/websearch.py", installed / "websearch.py")
    old_dir = plugin_host._INSTALLED_DIR
    old_registry = plugin_ops.read_registry
    monkeypatch.setattr(plugin_host, "_INSTALLED_DIR", str(installed))
    monkeypatch.setattr(plugin_ops, "read_registry", lambda: {"plugins": {}})
    monkeypatch.setattr(features, "on", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(features, "register", lambda *_args, **_kwargs: True)
    plugin_host.load(force=True)
    yield plugin_host.get_by_tool("search").mod
    plugin_host._INSTALLED_DIR = old_dir
    plugin_ops.read_registry = old_registry
    plugin_host.load(force=True)


def _invoke(name, args):
    return tools.invoke_readonly_spec(name, args, type("Context", (), {"src": "pc"})())


def test_search_success_and_empty_search_are_completed_queries(installed_websearch):
    websearch = installed_websearch
    websearch._mem.clear()
    with patch.object(websearch, "_post", return_value=[{
        "title": "Vigil release", "url": "https://example.test/release", "snippet": "v2"
    }]):
        result = _invoke("search", {"query": "Vigil release"})
    assert result.ok is True
    assert "Vigil release" in result.text

    websearch._mem.clear()
    with patch.object(websearch, "_post", return_value=[]):
        result = _invoke("search", {"query": "no matches"})
    assert result.ok is True
    assert "没搜到" in result.text


def test_search_connection_failure_is_not_a_successful_verification(installed_websearch):
    websearch = installed_websearch
    websearch._mem.clear()
    with patch.object(websearch, "_post", return_value=None):
        result = _invoke("search", {"query": "Vigil release outage"})
    assert result.ok is False
    assert "搜不了" in result.text
    assert result.for_model().startswith("【查询未核实：只读工具执行失败】")
    # The public plugin API remains a string for existing callers.
    websearch._mem.clear()
    with patch.object(websearch, "_post", return_value=None):
        assert isinstance(websearch._t_search("legacy search"), str)


def test_search_cache_without_structured_status_fails_closed(installed_websearch):
    websearch = installed_websearch
    import time
    websearch._mem.clear()
    query, options = websearch._parse("legacy cache entry")
    websearch._mem[websearch._ckey(query, options)] = (
        time.time() + 60, "cached response with unknown execution status")
    result = _invoke("search", {"query": "legacy cache entry"})
    assert result.ok is False
    assert result.for_model().startswith("【查询未核实：只读工具执行失败】")
    websearch._mem.clear()


def test_weather_and_tracking_api_failures_return_structured_failure():
    import uapi
    with patch.object(tools, "_uapi_token", return_value="fixture"), \
         patch.object(uapi, "fetch_weather", return_value=None):
        weather = _invoke("weather", {"location": "上海"})
    assert weather.ok is False
    assert "接口失败" in weather.text

    with patch.object(tools, "_uapi_token", return_value="fixture"), \
         patch.object(uapi, "fetch_tracking", return_value=None):
        tracking = _invoke("tracking", {"tracking_number": "SF123456"})
    assert tracking.ok is False
    assert "查不到物流" in tracking.text
    assert "未核实" in tracking.for_model()


def test_weather_and_tracking_success_are_structured_without_changing_legacy_text():
    import uapi
    with patch.object(tools, "_uapi_token", return_value="fixture"), \
         patch.object(uapi, "fetch_weather", return_value={
             "weather": "晴", "temperature": 23
         }):
        weather = _invoke("weather", {"location": "上海"})
        assert isinstance(tools._t_weather("上海"), str)
    assert weather.ok is True
    assert "上海现在" in weather.text

    with patch.object(tools, "_uapi_token", return_value="fixture"), \
         patch.object(uapi, "fetch_tracking", return_value={
             "carrier_name": "顺丰", "status": "运输中", "tracks": []
         }):
        tracking = _invoke("tracking", {"tracking_number": "SF123456"})
        assert isinstance(tools._t_tracking("SF123456"), str)
    assert tracking.ok is True
    assert "运输中" in tracking.text


def test_retired_calc_cannot_be_invoked_through_the_readonly_adapter():
    """calc 已在 P21.2 下线；旧客户端/旧会话再点它的名字必须被挡在名单外。"""
    result = _invoke("calc", {"expression": "128*3"})
    assert result.ok is False
    assert "未执行" in result.for_model()


def test_unknown_tool_cannot_cross_readonly_adapter_boundary():
    result = _invoke("local_exec", {"command": "whoami"})
    assert result.ok is False
    assert "未执行" in result.for_model()
