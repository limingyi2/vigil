# -*- coding: utf-8 -*-
"""统一插件生命周期管理器。

安装格式由 :mod:`plugin_ops` 继续负责，运行时由 :mod:`plugin_host` 负责；
本模块是两者之间的唯一生命周期协调层。它保存兼容的注册表元数据，提供
安装/更新/启停/卸载/重载及市场版本状态，并把失败原因持久化，方便客户端
和后续模型、工具、工作流插件复用同一套契约。
"""
import re
import threading
import time

_LOCK = threading.RLock()
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


def _ops():
    import plugin_ops
    return plugin_ops


def _registry():
    return _ops().read_registry()


def _save(reg):
    return _ops().write_registry(reg)


def _valid_name(name):
    return bool(_NAME.fullmatch(str(name or "").strip()))


def _version(value):
    """Compare ordinary semver and graceful legacy versions."""
    text = str(value or "").strip().lstrip("vV")
    nums = re.findall(r"\d+", text)
    if not nums:
        return (0, 0, 0, text)
    return tuple((int(x) for x in (nums + ["0", "0"])[:3])) + (text,)


def compare_versions(left, right):
    """Return -1/0/1; exported for market and client code."""
    a, b = _version(left), _version(right)
    return (a > b) - (a < b)


def _meta(reg, name):
    return (reg.get("plugins") or {}).get(name) or {}


def _set_state(name, **patch):
    reg = _registry()
    item = dict(_meta(reg, name))
    item.update(patch)
    item.setdefault("enabled", True)
    item["state_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    reg.setdefault("plugins", {})[name] = item
    _save(reg)
    return item


def _stop(name):
    """Call the optional stop/shutdown hook before replacing or deleting code."""
    try:
        import plugin_host
        plugin = plugin_host.get(name)
        if not plugin:
            return True, ""
        for hook_name in ("stop", "shutdown", "close"):
            hook = plugin.hook(hook_name)
            if callable(hook):
                hook()
                return True, hook_name
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return True, ""


def _reload(name=None, brain=None):
    import plugin_host
    if name:
        _stop(name)
    loaded = plugin_host.load(force=True)
    if brain is not None:
        plugin_host.start_all(brain)
    return name is None or name in loaded


def _dependencies(manifest, reg):
    raw = (manifest or {}).get("dependencies", {})
    if raw is None:
        return []
    if isinstance(raw, list):
        raw = {str(x): "" for x in raw}
    if not isinstance(raw, dict):
        return ["dependencies 必须是对象或列表"]
    missing = []
    for dep, wanted in raw.items():
        if not _valid_name(dep):
            missing.append("非法依赖 %s" % dep)
            continue
        installed = _meta(reg, dep).get("version")
        if not installed:
            missing.append("缺少 %s" % dep)
        elif wanted and compare_versions(installed, str(wanted).lstrip("><=~^ ")) < 0:
            missing.append("%s %s（已安装 %s）" % (dep, wanted, installed))
    return missing


def _update_meta(name, manifest, *, enabled=None, source=""):
    reg = _registry()
    item = dict(_meta(reg, name))
    item.update({
        "manifest_schema": (manifest or {}).get("schema", "vigil.brain-plugin/v1"),
        "version": str((manifest or {}).get("version") or item.get("version") or ""),
        "dependencies": (manifest or {}).get("dependencies", {}),
        "permissions": (manifest or {}).get("permissions", []),
        "manifest": dict(manifest or {}),
        "state": "installed",
        "last_error": "",
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    })
    if source:
        item["source"] = source
    if enabled is not None:
        item["enabled"] = bool(enabled)
    item.setdefault("enabled", True)
    reg.setdefault("plugins", {})[name] = item
    _save(reg)
    return item


def install_archive(path, *, source="zip", replace=False, reload=True):
    """Install one archive through the existing safe transaction."""
    name = ""
    try:
        incoming, manifest_error = _ops().manifest_from_zip(path)
        if manifest_error:
            return False, {"ok": False, "err": manifest_error, "state": "error"}
        package_name = str((incoming or {}).get("name") or "")
        if package_name and not replace and _meta(_registry(), package_name):
            return False, {"ok": False, "name": package_name,
                           "err": "插件已安装，请使用更新操作", "state": "already_installed"}
        missing = _dependencies(incoming, _registry())
        if missing:
            return False, {"ok": False, "err": "依赖检查失败：" + "; ".join(missing), "state": "error"}
        ok, result = _ops().install(path, note=source, replace=replace)
        name = result.get("name") or result.get("package") or ""
        if not ok:
            if name:
                _set_state(name, state="error", last_error=result.get("err", "安装失败"))
            return False, result
        manifest = result.get("manifest") or {}
        reg = _registry()
        _update_meta(name, manifest, source=source)
        if reload:
            try:
                _reload(name)
            except Exception as exc:
                result.update(runtime_warning="文件已提交，运行时重载失败：%s" % str(exc)[:120])
                _set_state(name, state="error", last_error=result["runtime_warning"])
                return False, result
        result.update(ok=True, name=name, state="installed", runtime_state="loaded" if reload else "pending")
        return True, result
    except Exception as exc:
        if name:
            _set_state(name, state="error", last_error=str(exc)[:180])
        return False, {"ok": False, "err": str(exc)[:180], "state": "error"}


def install(name, archive_path, **kwargs):
    """Named entry point used by future non-market providers."""
    if not _valid_name(name):
        return False, {"ok": False, "err": "插件名不合法"}
    return install_archive(archive_path, **kwargs)


def update(name, archive_path, *, source="market", reload=True):
    """Replace an existing plugin atomically; plugin_ops keeps its backup until commit."""
    reg = _registry()
    if not _meta(reg, name):
        return False, {"ok": False, "err": "插件尚未安装", "state": "not_installed"}
    ok, stopped = _stop(name)
    if not ok:
        _set_state(name, state="error", last_error="停止旧插件失败：" + stopped)
        return False, {"ok": False, "err": "停止旧插件失败：" + stopped, "state": "error"}
    result_ok, result = install_archive(archive_path, source=source, replace=True, reload=reload)
    if not result_ok:
        # Transaction rollback preserves the old files; restore the old runtime too.
        try:
            _reload(name)
        except Exception:
            pass
        result.setdefault("rollback", "restored")
        result["state"] = "error"
    return result_ok, result


def enable(name):
    if not _valid_name(name):
        return False, {"ok": False, "err": "插件名不合法"}
    reg = _registry()
    if not _meta(reg, name):
        return False, {"ok": False, "err": "插件尚未安装"}
    item = _set_state(name, enabled=True, state="enabled", last_error="")
    try:
        import features
        feature = (item.get("manifest") or {}).get("feature")
        if feature:
            features.set_features({feature: True})
        _reload(name)
    except Exception as exc:
        _set_state(name, state="error", last_error=str(exc)[:180])
        return False, {"ok": False, "err": str(exc)[:180], "state": "error"}
    return True, {"ok": True, "name": name, "enabled": True, "state": "enabled"}


def disable(name):
    if not _valid_name(name):
        return False, {"ok": False, "err": "插件名不合法"}
    reg = _registry()
    if not _meta(reg, name):
        return False, {"ok": False, "err": "插件尚未安装"}
    item = _set_state(name, enabled=False, state="disabled", last_error="")
    try:
        import features
        feature = (item.get("manifest") or {}).get("feature")
        if feature:
            features.set_features({feature: False})
        _stop(name)
        _reload(name)
    except Exception as exc:
        _set_state(name, state="error", last_error=str(exc)[:180])
        return False, {"ok": False, "err": str(exc)[:180], "state": "error"}
    return True, {"ok": True, "name": name, "enabled": False, "state": "disabled"}


def uninstall(name, *, purge_data=False):
    if not _valid_name(name):
        return False, {"ok": False, "err": "插件名不合法"}
    ok, detail = _stop(name)
    if not ok:
        return False, {"ok": False, "err": "停止插件失败：" + detail, "state": "error"}
    ok, result = _ops().uninstall(name, purge_data=purge_data)
    if not ok:
        _set_state(name, state="error", last_error=result.get("err", "卸载失败"))
        return False, result
    try:
        _reload(name)
    except Exception as exc:
        result["runtime_warning"] = str(exc)[:120]
    result.update(ok=True, name=name, state="uninstalled", user_data="purged" if purge_data else "preserved")
    return True, result


def reload(name=None, brain=None):
    try:
        ok = _reload(name, brain=brain)
        return {"ok": bool(ok), "name": name or "*", "state": "loaded" if ok else "error"}
    except Exception as exc:
        if name:
            _set_state(name, state="error", last_error=str(exc)[:180])
        return {"ok": False, "name": name or "*", "state": "error", "err": str(exc)[:180]}


def status(name, latest_version=""):
    reg = _registry()
    item = dict(_meta(reg, name))
    installed = bool(item)
    version = item.get("version", "")
    try:
        import plugin_host
        runtime = plugin_host.get(name)
        runtime_state = "loaded" if runtime else ("disabled" if item.get("enabled") is False else "not_loaded")
        diagnostic = runtime.err if runtime and runtime.err else item.get("last_error", "")
    except Exception:
        runtime_state, diagnostic = "unknown", item.get("last_error", "")
    update_state = "not_installed" if not installed else "current"
    if installed and latest_version:
        cmp = compare_versions(version, latest_version)
        update_state = "update_available" if cmp < 0 else ("ahead" if cmp > 0 else "current")
    return {"name": name, "installed": installed, "version": version,
            "latest_version": latest_version, "update_state": update_state,
            "state": item.get("state", "installed" if installed else "not_installed"),
            "enabled": item.get("enabled", True) if installed else False,
            "runtime_state": runtime_state, "dependencies": item.get("dependencies", {}),
            "permissions": item.get("permissions", []), "diagnostic": diagnostic,
            "manifest": item.get("manifest", {})}


def list_plugins(market=None):
    market = market or []
    latest = {str(x.get("name")): x for x in market if isinstance(x, dict) and x.get("name")}
    names = set((_registry().get("plugins") or {}).keys()) | set(latest)
    return [dict(status(name, (latest.get(name) or {}).get("version", "")),
                 market=latest.get(name)) for name in sorted(names)]
