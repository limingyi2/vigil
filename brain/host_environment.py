"""Read-only, secret-free self-knowledge for the Vigil Brain.

This module reports facts exposed by the running host. It never infers access
from an API existing in source, and it deliberately omits paths, credentials,
environment variables, and raw exception messages.
"""
from __future__ import annotations

import sys
import re
import importlib


_SAFE_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,119}$")
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,79}$")
_SAFE_STATES = {"available", "unavailable", "online", "offline", "connected",
                "disconnected", "unknown", "error", "ready", "configured",
                "unconfigured", "disabled", "starting", "stopping", "busy",
                "queryable", "executable", "awaiting_authorization"}


def _safe_model_name(value):
    if type(value) is not str:
        return None
    text = str(value or "").strip()
    if not text or not _SAFE_MODEL.fullmatch(text):
        return None
    if (text.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", text)
            or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text)
            or re.match(r"^(?:sk-|rk-|pk-|ghp_|github_pat_|bearer\b)", text, re.I)
            or any(marker in text.lower() for marker in ("api_key", "api-key", "token="))):
        return None
    return text


def _safe_identifier(value):
    if type(value) is not str:
        return "unconfirmed"
    text = value
    sensitive_label = re.search(
        r"(?:^|[_-])(?:api[_-]?key|token|secret|password|credential)(?:[_-]|$)",
        text, re.I)
    return text if _SAFE_IDENTIFIER.fullmatch(text) and not sensitive_label else "unconfirmed"


def _safe_permission(value):
    if type(value) is not str:
        return "unconfirmed"
    text = value.strip().lower()
    if text in {"read", "delegated"}:
        return text
    return "requires_explicit_grant" if text else "unconfirmed"


def _safe_build_version():
    try:
        import build_identity
        value = str((build_identity.identity() or {}).get("version") or "")
        if value and value != "development":
            return {"value": value, "state": "confirmed", "source": "Brain build identity"}
    except Exception:
        pass
    return {"value": None, "state": "unconfirmed", "source": "Brain build identity unavailable"}


def _provider_info(brain, api_config=None, provider=None):
    cfg = dict(api_config or (brain._api_snapshot() if brain is not None else {}) or {})
    mode = str(cfg.get("model_mode") or "cloud").strip().lower()
    if mode not in {"cloud", "local", "hybrid"}:
        mode = "unconfirmed"
    if provider is None and brain is not None:
        try:
            provider = brain._provider(cfg)
        except Exception:
            provider = None
    provider_kind = str(getattr(provider, "kind", "") or "").strip() or None
    if mode == "local" or provider_kind == "ollama":
        model = _safe_model_name(((cfg.get("ollama") or {}).get("model"))) or ""
    else:
        model = _safe_model_name(cfg.get("model")) or ""
    supports_tools = None
    if provider is not None:
        xml_allowed = str(cfg.get("allow_xml_tool_fallback", True)).strip().lower() not in (
            "false", "0", "off", "no")
        supports_tools = bool(getattr(provider, "supports_tools", False)
                              or (xml_allowed and getattr(provider, "supports_xml_tools", False)))
    return {
        "mode": {"value": mode, "state": "confirmed" if mode != "unconfirmed" else "unconfirmed"},
        "current_model": {"value": model or None,
                           "state": "confirmed" if model else "unconfirmed"},
        "current_provider": {"value": provider_kind,
                              "state": "confirmed" if provider_kind else "unconfirmed"},
        "supports_tool_calls": {"value": supports_tools,
                                "state": "confirmed" if supports_tools is not None else "unconfirmed"},
    }


def _workspace_status(brain):
    if brain is None:
        return {"selected": {"value": None, "state": "unconfirmed"},
                "file_access_authorization": {"value": None, "state": "unconfirmed"}}
    try:
        state = brain.turn_workspace_status
        selected = bool(state.get("selected"))
    except Exception:
        selected = False
    return {
        "selected": {"value": selected, "state": "confirmed_available"},
        # Workspace membership locates work; it does not grant local file access.
        "file_access_authorization": {"value": None, "state": "unconfirmed"},
    }


def _tool_detail(brain, src, provider_info, tool_specs=None, specs_supplied=False,
                 summary_only=False, tool_policy=""):
    try:
        import features
        enabled = bool(features.on("tools", src))
        feature_state = "confirmed_available" if enabled else "registered_but_disabled"
    except Exception:
        enabled, feature_state = None, "unconfirmed"

    specs = []
    if specs_supplied:
        specs = list(tool_specs or [])
    elif not summary_only:
        try:
            import tools
            specs = tools.all_specs(brain=brain, src=src)
        except Exception:
            pass
    actual_names = sorted({_safe_identifier((s.get("function") or {}).get("name"))
                           for s in specs if isinstance(s, dict)
                           and isinstance(s.get("function"), dict)
                           and s["function"].get("name")})
    actual_names = [name for name in actual_names if name != "unconfirmed"]
    try:
        import tools as _tools
        if specs_supplied:
            actual_names = [name for name in actual_names
                            if (name not in _tools._STATIC_SPECS or
                                _tools.static_tool_enabled(name, src))]
            if tool_policy == "readonly":
                actual_names = [name for name in actual_names
                                if name in _tools.READ_ONLY_TOOL_NAMES]
    except Exception:
        if specs_supplied:
            actual_names = []
    provider_support = provider_info["supports_tool_calls"]["value"]
    if enabled is False:
        state = "registered_but_disabled"
        available = []
    elif provider_support is False:
        state = "currently_unavailable"
        available = []
    elif summary_only:
        state = "confirmed_available"
        available = []
    elif specs_supplied and not tool_specs:
        state = "currently_unavailable"
        available = []
    elif specs_supplied and tool_specs is None:
        state = "unconfirmed"
        available = []
    elif enabled is True and provider_support is True:
        state = "confirmed_available"
        available = actual_names
    else:
        state = "unconfirmed"
        available = []

    registered_disabled = []
    try:
        import tools
        for name in sorted(tools._STATIC_SPECS):
            if name in actual_names:
                continue
            is_enabled = bool(enabled and tools.static_tool_enabled(name, src))
            if not is_enabled or (specs_supplied and tool_policy == "readonly"):
                why = ("not_available_in_this_context" if
                       (is_enabled and specs_supplied and tool_policy == "readonly") else
                       "feature_disabled")
                registered_disabled.append({"name": name,
                                            "state": "registered_but_disabled",
                                            "reason": why})
    except Exception:
        pass
    return {
        "state": state,
        "source": "current Brain tool registry, feature switches, and provider support",
        "available": [{"name": name, "state": "confirmed_available"}
                      for name in available],
        "registered_but_disabled": [
            item if isinstance(item, dict) else
            {"name": item, "state": "registered_but_disabled"}
            for item in registered_disabled],
        "provider_support": provider_info["supports_tool_calls"],
    }


def _extension_status():
    plugins = {"state": "unconfirmed", "registered_count": None,
               "agent_installation": "currently_unavailable",
               "install_entry_in_client_source": "unconfirmed",
               "current_client_install_entry": "unconfirmed"}
    try:
        import plugin_host
        listing = plugin_host.listing()
        plugins.update({"state": "confirmed_available", "registered_count": len(listing),
                        "install_entry_in_client_source": "present",
                        "current_client_install_entry": "unconfirmed"})
    except Exception:
        plugins["state"] = "currently_unavailable"

    skills = {"mechanism": "unconfirmed",
              "agent_installation": "currently_unavailable",
              "install_entry_in_client_source": "unconfirmed",
              "current_client_install_entry": "unconfirmed"}
    try:
        importlib.import_module("plugins.skill")
        skills.update({"mechanism": "confirmed_available",
                       "install_entry_in_client_source": "present",
                       "current_client_install_entry": "unconfirmed"})
    except Exception:
        skills.update({"mechanism": "currently_unavailable"})
    return {"plugins": plugins, "skills": skills}


def snapshot(brain=None, src=None, *, api_config=None, provider=None,
             tool_specs=None, detailed=False, turn_snapshot=False,
             summary_only=False, tool_policy=""):
    """Return a bounded public snapshot. Values carry explicit evidence states."""
    source = str(src or getattr(brain, "_cur_src", "pc") or "pc")
    provider_info = _provider_info(brain, api_config, provider)
    try:
        import localtools
        client_online = bool(localtools.pc_online())
        client_state = "confirmed_available" if client_online else "currently_unavailable"
    except Exception:
        client_online, client_state = None, "unconfirmed"
    active_specs = tool_specs
    readonly_context = str(tool_policy or "") == "readonly"
    if readonly_context and active_specs is None:
        try:
            import tools as _tools
            active_specs = _tools.read_only_specs(
                _tools.all_specs(brain=brain, src=source))
        except Exception:
            active_specs = []
    env = {
        "application": {"name": "Vigil AI Agent desktop client", "state": "confirmed",
                        "source": "Vigil host identity"},
        "application_version": {"value": None, "state": "unconfirmed",
                                 "reason": "The running Electron client does not report its version to Brain."},
        "brain": {"process": "current Brain runtime", "state": "confirmed",
                  "packaged": bool(getattr(sys, "frozen", False)),
                  "build_version": _safe_build_version()},
        "desktop_client_connection": {"value": client_online, "state": client_state},
        "model": provider_info,
        "tooling": _tool_detail(brain, source, provider_info,
                                tool_specs=active_specs,
                                specs_supplied=(turn_snapshot or readonly_context),
                                summary_only=summary_only,
                                tool_policy=tool_policy),
        "extensions": _extension_status(),
        "workspace": _workspace_status(brain),
        "source": "Vigil Brain runtime and registered host interfaces",
    }
    return env


def _brief(value):
    labels = {"confirmed_available": "可用", "registered_but_disabled": "未启用",
              "currently_unavailable": "不可用", "unconfirmed": "未确认"}

    def label(state):
        return labels.get(str(state or "unconfirmed"), "未确认")

    appver = value.get("application_version", {}).get("value") or "未确认"
    brain = value.get("brain", {})
    brain_ver = (brain.get("build_version") or {}).get("value") or "未确认"
    packaged = "打包" if brain.get("packaged") else "Python"
    client = value.get("desktop_client_connection", {}).get("state", "unconfirmed")
    model = value.get("model", {})
    mode = model.get("mode", {}).get("value") or "未确认"
    current = model.get("current_model", {}).get("value") or "未确认"
    provider = model.get("current_provider", {}).get("value") or "未确认"
    tools = value.get("tooling", {})
    workspace = value.get("workspace", {})
    plugin = value.get("extensions", {}).get("plugins", {}).get("state", "unconfirmed")
    skill = value.get("extensions", {}).get("skills", {}).get("mechanism", "unconfirmed")
    selected = (workspace.get("selected") or {})
    selected_text = ("已选择" if selected.get("value") else "未选择") \
        if selected.get("state") != "unconfirmed" else "未确认"
    return (
        "\n\n【Vigil 宿主状态｜Brain 读取】"
        f"客户端版本={appver}；Brain={packaged}/{brain_ver}；连接={label(client)}；"
        f"模型={mode}/{provider}/{current}；工具={label(tools.get('state'))}；"
        f"插件={label(plugin)}；Skill={label(skill)}；Agent 安装=不可用；UI 入口=未确认；"
        f"工作区={selected_text}；文件授权={label((workspace.get('file_access_authorization') or {}).get('state'))}。"
        "状态按可用/未启用/不可用/未确认理解；工具清单与权限按需查 capability_status。"
    )


def prompt_summary(brain=None, src=None, *, api_config=None, provider=None,
                   tool_specs=None):
    """Small dynamic suffix, kept outside the cached persona text."""
    return _brief(snapshot(brain, src, api_config=api_config, provider=provider,
                           tool_specs=tool_specs, turn_snapshot=True,
                           summary_only=True))


def _safe_capability(item):
    """Project registry data onto a tiny public shape; never return raw metadata/errors."""
    if not isinstance(item, dict):
        item = {}
    status = item.get("status") if isinstance(item.get("status"), dict) else {}
    public_status = {}
    for key in ("connected", "state", "model", "tasks", "plugins", "enabled", "unified"):
        value = status.get(key)
        if key == "model":
            value = _safe_model_name(value)
            if value is not None:
                public_status[key] = value
        elif key == "state":
            public_status[key] = value if type(value) is str and value in _SAFE_STATES else "unconfirmed"
        elif key in {"connected", "enabled", "unified"}:
            if type(value) is bool:
                public_status[key] = value
        elif key in {"tasks", "plugins"}:
            if type(value) is int and 0 <= value <= 1_000_000:
                public_status[key] = value
    tools = status.get("tools")
    if isinstance(tools, (list, tuple)):
        public_status["tools"] = [name for name in
                                   (_safe_identifier(x) for x in tools[:100])
                                   if name != "unconfirmed"]
    operations = item.get("operations")
    if isinstance(operations, dict):
        operation_names = list(operations.keys())[:100]
    elif isinstance(operations, (list, tuple)):
        operation_names = list(operations[:100])
    else:
        operation_names = []
    permissions = item.get("permissions")
    permissions = permissions if isinstance(permissions, dict) else {}
    operation_states = item.get("operation_states")
    operation_states = operation_states if isinstance(operation_states, dict) else {}
    safe_permissions = {}
    for key, value in list(permissions.items())[:100]:
        safe_key = _safe_identifier(key)
        if safe_key != "unconfirmed":
            safe_permissions[safe_key] = _safe_permission(value)
    safe_operation_states = {}
    for key, value in list(operation_states.items())[:100]:
        safe_key = _safe_identifier(key)
        if safe_key != "unconfirmed":
            safe_operation_states[safe_key] = (
                value if type(value) is str and value in _SAFE_STATES else "unconfirmed")
    return {
        "name": _safe_identifier(item.get("name")),
        "status": public_status,
        "operations": [_safe_identifier(x) for x in operation_names
                       if _safe_identifier(x) != "unconfirmed"],
        "permissions": safe_permissions,
        "operation_states": safe_operation_states,
    }


def capability_status(ctx=None, capability=""):
    """Detailed safe view used by the existing read-only capability_status tool."""
    import capability_registry
    registry = capability_registry.bootstrap()
    brain = getattr(ctx, "brain", None)
    src = getattr(ctx, "src", None)
    readonly_context = str(getattr(ctx, "tool_policy", "") or "") == "readonly"
    request_specs = getattr(ctx, "allowed_tool_specs", None)
    api_config = getattr(ctx, "api_config", None)
    if not isinstance(api_config, dict) or not api_config:
        api_config = None
    try:
        if api_config is None and brain is not None:
            api_config = brain._api_snapshot()
        provider = brain._provider(api_config) if brain is not None and api_config is not None else None
    except Exception:
        provider, api_config = None, None
    env = snapshot(brain, src, api_config=api_config, provider=provider,
                   tool_specs=request_specs, detailed=True,
                   turn_snapshot=request_specs is not None,
                   tool_policy="readonly" if readonly_context else "")
    if capability:
        item = registry.get(capability)
        return {"item": _safe_capability(item) if item else None,
                "environment": env,
                "source": "Vigil Brain capability registry"}
    raw = registry.snapshot()
    raw_items = raw.get("items") if isinstance(raw, dict) else []
    if not isinstance(raw_items, (list, tuple)):
        raw_items = []
    cursor = raw.get("cursor") if isinstance(raw, dict) else None
    return {"items": [_safe_capability(x) for x in raw_items],
            "cursor": cursor if type(cursor) is int and 0 <= cursor <= 2**53 else None,
            "environment": env,
            "source": "Vigil Brain capability registry"}
