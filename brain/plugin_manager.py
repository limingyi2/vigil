# -*- coding: utf-8 -*-
"""统一插件生命周期管理器。

安装格式由 :mod:`plugin_ops` 继续负责，运行时由 :mod:`plugin_host` 负责；
本模块是两者之间的唯一生命周期协调层。它保存兼容的注册表元数据，提供
安装/更新/启停/卸载/重载及市场版本状态，并把失败原因持久化，方便客户端
和后续模型、工具、工作流插件复用同一套契约。
"""
import re
import os
import shutil
import tempfile
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
        if callable(plugin.hook("start")) and not any(callable(plugin.hook(h)) for h in ("stop", "shutdown", "close")):
            return False, "插件提供 start(brain) 但没有 stop/shutdown/close；需要重启 Brain"
        stopped, errors = plugin_host.stop(name)
        if not stopped:
            return False, "; ".join(errors)
        if errors:
            return False, "; ".join(errors)
        return True, ""
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:160])
    return True, ""


def _needs_restart(detail):
    return "需要重启 Brain" in str(detail or "")


def _reload(name=None, brain=None, allow_missing=False):
    import plugin_host
    if name:
        # A targeted reload must leave unrelated workers and their module
        # instances alone. Only the target may be stopped/recreated here.
        active = plugin_host.started_names()
        if name in active:
            plugin = plugin_host.get(name)
            if (plugin is not None and callable(plugin.hook("start"))
                    and not any(callable(plugin.hook(h)) for h in ("stop", "shutdown", "close"))):
                return False
            stopped, _errors = plugin_host.stop(name)
            if not stopped:
                return False
        loaded = plugin_host.load(force=True, only=name)
        if name not in loaded:
            if allow_missing:
                return True
            raise RuntimeError("插件 %s 未能加载；请查看插件错误诊断" % name)
        running_brain = brain if brain is not None else plugin_host.runtime_brain()
        target = [name]
        needs_brain = (callable(loaded[name].hook("start"))
                       and plugin_host.is_enabled(name))
        if needs_brain and running_brain is None:
            return False
        if running_brain is not None:
            details = plugin_host.start_all(running_brain, names=target, detailed=True)
            if details["failed"]:
                raise RuntimeError("插件 start(brain) 启动失败：%s" % ", ".join(details["failed"]))
            if needs_brain and not plugin_host.is_started(name):
                raise RuntimeError("插件 start(brain) 未完成：%s" % name)
        return True

    # A forced load recreates module objects. Stop every previously started
    # hook first so a full reload cannot leave old worker threads running while
    # the new module is started. The Brain object itself remains registered in
    # plugin_host and is intentionally not cleared by stop().
    active = plugin_host.started_names()
    for running_name in sorted(active):
        plugin = plugin_host.get(running_name)
        if (plugin is not None and callable(plugin.hook("start"))
                and not any(callable(plugin.hook(h)) for h in ("stop", "shutdown", "close"))):
            return False
    stopped, stop_errors = plugin_host.stop()
    if not stopped:
        return False
    loaded = plugin_host.load(force=True)
    running_brain = brain if brain is not None else plugin_host.runtime_brain()
    # A full reload restarts every enabled start hook after all old modules
    # have been stopped and recreated.
    restart_names = None
    candidate_names = list(loaded)
    needs_brain = any(
        callable(loaded[n].hook("start")) and plugin_host.is_enabled(n)
        for n in candidate_names
    )
    if needs_brain and running_brain is None:
        return False
    if running_brain is not None:
        details = plugin_host.start_all(running_brain, names=restart_names, detailed=True)
        if details["failed"]:
            failed = ", ".join(details["failed"])
            raise RuntimeError("插件 start(brain) 启动失败：%s" % failed)
        required = [n for n in candidate_names
                    if callable(loaded[n].hook("start")) and plugin_host.is_enabled(n)]
        missing = [n for n in required if not plugin_host.is_started(n)]
        if missing:
            raise RuntimeError("插件 start(brain) 未完成：%s" % ", ".join(missing))
    return True


def _backup(name):
    """Snapshot plugin files and registry before an update.

    The backup remains on disk when restoration fails, so an operator can
    recover manually instead of receiving a false ``rollback=restored``.
    """
    ops = _ops()
    root = os.path.abspath(str(ops._PLUG_DIR))
    os.makedirs(root, exist_ok=True)
    path = tempfile.mkdtemp(prefix="_manager_backup_", dir=root)
    entries = []
    for item in os.listdir(root):
        if item == name or item == name + ".py" or item.startswith("_" + name + "_"):
            source = os.path.join(root, item)
            target = os.path.join(path, item)
            if os.path.isdir(source):
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
            entries.append(item)
    registry = os.path.join(path, "_registry.snapshot")
    if os.path.isfile(ops.REGISTRY):
        shutil.copy2(ops.REGISTRY, registry)
    return {"path": path, "root": root, "name": name, "registry": registry,
            "registry_path": ops.REGISTRY, "entries": entries}


def _restore_backup(backup):
    """Restore files and registry, returning (success, diagnostic)."""
    if not backup:
        return False, "没有可用的更新备份"
    try:
        root = backup["root"]
        owned_prefix = "_" + backup["name"] + "_"
        for item in list(os.listdir(root)):
            if item == backup["name"] or item == backup["name"] + ".py" or item.startswith(owned_prefix):
                _remove_path(os.path.join(root, item))
        for item in backup["entries"]:
            source = os.path.join(backup["path"], item)
            target = os.path.join(root, item)
            if os.path.isdir(source):
                shutil.copytree(source, target)
            else:
                shutil.copy2(source, target)
        if os.path.isfile(backup["registry"]):
            os.makedirs(os.path.dirname(backup["registry_path"]), exist_ok=True)
            shutil.copy2(backup["registry"], backup["registry_path"])
        else:
            try:
                os.remove(backup["registry_path"])
            except FileNotFoundError:
                pass
        return True, ""
    except Exception as exc:
        return False, "%s: %s" % (type(exc).__name__, str(exc)[:180])


def _remove_path(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    elif os.path.lexists(path):
        os.remove(path)


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


def install_archive(path, *, source="zip", replace=False, reload=True, manifest_override=None):
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
        metadata_manifest = manifest_override if manifest_override is not None else incoming
        missing = _dependencies(metadata_manifest, _registry())
        if missing:
            return False, {"ok": False, "err": "依赖检查失败：" + "; ".join(missing), "state": "error"}
        ok, result = _ops().install(path, note=source, replace=replace)
        name = result.get("name") or result.get("package") or ""
        if not ok:
            if name:
                _set_state(name, state="error", last_error=result.get("err", "安装失败"))
            return False, result
        # Market packages carry a signed/verified manifest.json in addition to
        # the optional legacy zxplugin.json. Keep those formats distinct and
        # persist the already-verified market manifest as registry metadata.
        manifest = dict(manifest_override if manifest_override is not None
                        else (result.get("manifest") or {}))
        _update_meta(name, manifest, source=source)
        if reload:
            try:
                if not _reload(name):
                    raise RuntimeError("运行中的 Brain 不可用，插件需要重启后才能启动")
            except Exception as exc:
                message = "文件已提交，运行时重载失败：%s" % str(exc)[:120]
                if "运行中的 Brain 不可用" in message:
                    _set_state(name, state="restart_required", last_error=message)
                    result.update(ok=True, state="restart_required", restart_needed=True,
                                  runtime_state="pending", runtime_warning=message)
                    return True, result
                result.update(runtime_warning=message)
                _set_state(name, state="error", last_error=message)
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


def update(name, archive_path, *, source="market", reload=True, manifest=None):
    """Replace an existing plugin atomically; plugin_ops keeps its backup until commit."""
    reg = _registry()
    if not _meta(reg, name):
        return False, {"ok": False, "err": "插件尚未安装", "state": "not_installed"}
    backup = _backup(name)
    ok, stopped = _stop(name)
    if not ok:
        shutil.rmtree(backup["path"], ignore_errors=True)
        state = "restart_required" if _needs_restart(stopped) else "error"
        message = "停止旧插件失败：" + stopped
        _set_state(name, state=state, last_error=message)
        return False, {"ok": False, "err": message, "state": state,
                       "restart_needed": state == "restart_required"}
    result_ok, result = install_archive(
        archive_path, source=source, replace=True, reload=False,
        manifest_override=manifest,
    )
    if result_ok and reload:
        try:
            if not _reload(name):
                raise RuntimeError("运行中的 Brain 不可用，插件需要重启后才能启动")
            result.update(runtime_state="loaded")
        except Exception as exc:
            result_ok = False
            result["err"] = "更新后重载失败：%s" % str(exc)[:180]
    if result_ok:
        shutil.rmtree(backup["path"], ignore_errors=True)
        if reload:
            result.update(ok=True, state="updated", rollback="not_needed")
        else:
            _set_state(name, state="restart_required", last_error="更新文件已提交，尚未重载运行时")
            result.update(ok=True, state="restart_required", restart_needed=True,
                          runtime_state="pending", rollback="not_needed")
        return True, result
    restored, restore_err = _restore_backup(backup)
    if restored:
        try:
            reloaded = _reload(name)
        except Exception as exc:
            reloaded = False
            restore_err = "旧插件重载异常：%s" % str(exc)[:180]
        if reloaded:
            shutil.rmtree(backup["path"], ignore_errors=True)
            diagnostic = "旧文件、注册表和运行态均已恢复"
            result.update(ok=False, rollback="restored", rollback_diagnostic=diagnostic, state="error")
        else:
            diagnostic = "文件和注册表已恢复，但旧插件无法重新加载；备份保留于 %s" % backup["path"]
            result.update(ok=False, rollback="failed", rollback_diagnostic=diagnostic, recovery_path=backup["path"], state="error")
    else:
        diagnostic = "恢复旧插件失败：%s；备份保留于 %s" % (restore_err, backup["path"])
        result.update(ok=False, rollback="failed", rollback_diagnostic=diagnostic, recovery_path=backup["path"], state="error")
    if result.get("rollback") == "failed":
        # Recovery diagnostics must win over the original update error and be
        # persisted so a restart still tells the operator where the backup is.
        _set_state(name, state="error", last_error=result.get("rollback_diagnostic", "更新回滚失败"),
                   rollback="failed", recovery_path=result.get("recovery_path", ""))
    else:
        _set_state(name, state="error", last_error=result.get("err") or result.get("rollback_diagnostic", "更新失败"),
                   rollback="restored", recovery_path="")
    return False, result


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
        if not _reload(name):
            _set_state(name, state="restart_required", last_error="运行中的 Brain 不可用")
            return False, {"ok": False, "name": name, "state": "restart_required",
                           "err": "插件已启用，但运行中的 Brain 不可用；请重启后启动插件"}
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
        stopped, stop_err = _stop(name)
        if not stopped:
            if _needs_restart(stop_err):
                _set_state(name, state="restart_required", last_error=stop_err)
                return False, {"ok": False, "name": name, "state": "restart_required",
                               "restart_needed": True, "err": stop_err}
            raise RuntimeError(stop_err)
        if not _reload(name):
            raise RuntimeError("插件已禁用，但运行中的 Brain 不可用；请重启后完成重载")
    except Exception as exc:
        _set_state(name, state="error", last_error=str(exc)[:180])
        return False, {"ok": False, "err": str(exc)[:180], "state": "error"}
    return True, {"ok": True, "name": name, "enabled": False, "state": "disabled"}


def uninstall(name, *, purge_data=False):
    if not _valid_name(name):
        return False, {"ok": False, "err": "插件名不合法"}
    ok, detail = _stop(name)
    if not ok:
        state = "restart_required" if _needs_restart(detail) else "error"
        return False, {"ok": False, "err": "停止插件失败：" + detail,
                       "state": state, "restart_needed": state == "restart_required"}
    ok, result = _ops().uninstall(name, purge_data=purge_data)
    if not ok:
        _set_state(name, state="error", last_error=result.get("err", "卸载失败"))
        return False, result
    try:
        # The target no longer exists. Refresh only its registry slot instead
        # of stopping unrelated background plugins or reporting an expected
        # absence as a runtime failure.
        if not _reload(name, allow_missing=True):
            result["runtime_warning"] = "插件已卸载，但其他插件运行态未完成重载；请重启 Brain"
    except Exception as exc:
        result["runtime_warning"] = str(exc)[:120]
    result.update(ok=True, name=name, state="uninstalled", user_data="purged" if purge_data else "preserved")
    return True, result


def reload(name=None, brain=None):
    try:
        if name:
            stopped, detail = _stop(name)
            if not stopped:
                return {"ok": False, "name": name, "state": "restart_required",
                        "restart_needed": True, "err": detail}
        ok = _reload(name, brain=brain)
        if not ok:
            message = "运行中的 Brain 不可用；请重启后启动插件"
            if name:
                _set_state(name, state="restart_required", last_error=message)
            return {"ok": False, "name": name or "*", "state": "restart_required", "restart_needed": True, "err": message}
        return {"ok": True, "name": name or "*", "state": "loaded"}
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
