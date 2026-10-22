"""A small shared capability registry and event bus for Vigil modules.

This is intentionally an adapter layer, not a second tool implementation.  A
module registers the real status reader and delegates operations to its
existing API.  The registry only records capability metadata, checks the
declared permission, publishes bounded state changes, and returns the adapter's
actual result.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import deque
from typing import Any, Callable, Mapping


class CapabilityError(RuntimeError):
    pass


class CapabilityRegistry:
    def __init__(self, event_limit: int = 256):
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self._events = deque(maxlen=max(16, int(event_limit)))
        self._seq = 0

    @staticmethod
    def _clean_status(value: Any) -> dict[str, Any]:
        if callable(value):
            try:
                value = value()
            except Exception as exc:
                value = {"connected": False, "state": "error",
                         "error": "%s: %s" % (type(exc).__name__, str(exc)[:180])}
        if isinstance(value, Mapping):
            return copy.deepcopy(dict(value))
        return {"connected": bool(value), "state": "available" if value else "offline"}

    def register(self, name: str, *, status: Any = None,
                 operations: Mapping[str, Any] | None = None,
                 permissions: Mapping[str, Any] | None = None,
                 source: str = "", metadata: Mapping[str, Any] | None = None,
                 replace: bool = False) -> dict[str, Any]:
        key = str(name or "").strip()
        if not key or len(key) > 100:
            raise CapabilityError("能力名称无效")
        with self._lock:
            current = self._items.get(key)
            if current and not replace:
                # Idempotent registration updates live status/metadata while
                # preserving the adapter already registered by the module.
                if status is not None:
                    current["status_reader"] = status
                if operations is not None:
                    current["operations"] = copy.deepcopy(dict(operations))
                if permissions is not None:
                    current["permissions"] = copy.deepcopy(dict(permissions))
                if source:
                    current["source"] = str(source)
                if metadata:
                    current["metadata"].update(copy.deepcopy(dict(metadata)))
                current["updated_at"] = time.time()
                return self._public(current)
            item = {
                "name": key, "status_reader": status,
                "operations": copy.deepcopy(dict(operations or {})),
                "permissions": copy.deepcopy(dict(permissions or {})),
                "source": str(source or ""),
                "metadata": copy.deepcopy(dict(metadata or {})),
                "updated_at": time.time(),
            }
            self._items[key] = item
            self._publish_locked("capability_registered", key, {"source": item["source"]})
            return self._public(item)

    def update(self, name: str, *, status: Any = None,
               metadata: Mapping[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
        key = str(name or "").strip()
        with self._lock:
            item = self._items.get(key)
            if not item:
                raise CapabilityError("能力未注册：%s" % key)
            if status is not None:
                # An event is a concrete state snapshot, not a new status
                # callback.  Keep both: future reads use the latest event until
                # the module supplies a fresh status callback.
                item["last_status"] = self._clean_status(status)
            if metadata:
                item["metadata"].update(copy.deepcopy(dict(metadata)))
            item.update({k: copy.deepcopy(v) for k, v in fields.items()
                         if k in {"source", "operations", "permissions"}})
            item["updated_at"] = time.time()
            self._publish_locked("capability_updated", key, {"status": item.get("last_status"),
                                                                "metadata": item["metadata"]})
            return self._public(item)

    def _public(self, item: Mapping[str, Any]) -> dict[str, Any]:
        status = item.get("last_status")
        if status is None:
            status = self._clean_status(item.get("status_reader"))
        operations = item.get("operations") or {}
        permissions = copy.deepcopy(item.get("permissions") or {})
        for op, spec in operations.items():
            if isinstance(spec, Mapping) and op not in permissions:
                permissions[str(op)] = str(spec.get("permission") or "read")
        return {
            "name": item["name"], "status": status,
            "operations": sorted(str(k) for k in operations),
            "permissions": permissions,
            "source": item.get("source") or "",
            "metadata": copy.deepcopy(item.get("metadata") or {}),
            "updated_at": item.get("updated_at"),
        }

    def get(self, name: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(str(name or "").strip())
            return self._public(item) if item else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {"items": [self._public(item) for item in self._items.values()],
                    "cursor": self._seq}

    def _publish_locked(self, event_type: str, capability: str,
                        payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._seq += 1
        event = {"seq": self._seq, "type": str(event_type),
                 "capability": capability, "at": time.time(),
                 "payload": copy.deepcopy(dict(payload or {}))}
        self._events.append(event)
        return event

    def publish(self, event_type: str, capability: str,
                payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            if capability not in self._items:
                raise CapabilityError("能力未注册：%s" % capability)
            event = self._publish_locked(event_type, capability, payload)
            if event_type in {"state", "capability_updated"} and isinstance(payload, Mapping):
                self._items[capability]["last_status"] = self._clean_status(payload.get("status", payload))
                self._items[capability]["updated_at"] = time.time()
            return copy.deepcopy(event)

    def events(self, after: int = 0) -> dict[str, Any]:
        with self._lock:
            cursor = max(0, int(after or 0))
            items = [copy.deepcopy(item) for item in self._events if item["seq"] > cursor]
            earliest = self._events[0]["seq"] if self._events else self._seq + 1
            return {"items": items, "cursor": self._seq,
                    "gap": bool(cursor and cursor < earliest - 1)}

    def invoke(self, name: str, operation: str, args: Mapping[str, Any] | None = None,
               *, authorized: bool = False) -> dict[str, Any]:
        key, op = str(name or "").strip(), str(operation or "").strip()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return {"ok": False, "error": "capability_not_found", "capability": key}
            spec = (item.get("operations") or {}).get(op)
        if not isinstance(spec, Mapping) or not callable(spec.get("handler")):
            return {"ok": False, "error": "operation_not_supported", "capability": key,
                    "operation": op}
        permission = str(spec.get("permission") or "read")
        if permission != "read" and not authorized:
            return {"ok": False, "error": "authorization_required", "permission": permission}
        try:
            result = spec["handler"](dict(args or {}))
            if isinstance(result, Mapping) and "ok" in result:
                out = dict(result)
            else:
                out = {"ok": True, "result": result}
            self.publish("operation_result", key,
                         {"operation": op, "ok": bool(out.get("ok")),
                          "result": out.get("result") if "result" in out else None})
            return out
        except Exception as exc:
            return {"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:240])}


REGISTRY = CapabilityRegistry()
_BOOTSTRAPPED = False
_BOOTSTRAP_LOCK = threading.Lock()


def _safe_status(fn: Callable[[], Any]) -> Callable[[], dict[str, Any]]:
    def reader() -> dict[str, Any]:
        try:
            value = fn()
            return value if isinstance(value, dict) else {"connected": bool(value), "state": "available" if value else "offline"}
        except Exception as exc:
            return {"connected": False, "state": "error", "error": "%s: %s" % (type(exc).__name__, str(exc)[:180])}
    return reader


def _active_config(fallback: Mapping[str, Any]) -> dict[str, Any]:
    """Read the live runtime config after a model-center hot switch."""
    try:
        import runtime
        return dict(getattr(runtime, "API_CFG", {}) or {})
    except Exception:
        return dict(fallback or {})


def bootstrap(api_config: Mapping[str, Any] | None = None) -> CapabilityRegistry:
    """Register adapters for the existing modules once per Brain process."""
    global _BOOTSTRAPPED
    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return REGISTRY
        cfg = dict(api_config or {})
        try:
            import codex_adapter
            codex = codex_adapter.get_adapter()
            REGISTRY.register("codex", status=_safe_status(lambda: {
                "connected": codex.status().get("connection_status") == "connected",
                "state": codex.status().get("connection_status"),
                "login": codex.status().get("runtime", {}).get("auth_status"),
                "tasks": len(codex.snapshot().get("owned", [])),
            }), source="brain.codex_adapter", operations={
                "status": {"permission": "read", "handler": lambda _a: codex.status()},
                "tasks": {"permission": "read", "handler": lambda _a: codex.snapshot()},
                "events": {"permission": "read", "handler": lambda a: codex.event_log(int(a.get("after") or 0))},
            })
        except Exception as exc:
            REGISTRY.register("codex", status={"connected": False, "state": "unavailable",
                                                "error": str(exc)[:180]}, source="brain.codex_adapter")

        try:
            import localtools
            REGISTRY.register("local_tools", status=_safe_status(lambda: {
                "connected": bool(localtools.pc_online()),
                "state": "online" if localtools.pc_online() else "offline",
                "tools": sorted(localtools.specs().keys()),
            }), source="brain.localtools", operations={
                "status": {"permission": "read", "handler": lambda _a: {"online": localtools.pc_online()}},
                "specs": {"permission": "read", "handler": lambda a: localtools.specs(src=a.get("src"))},
                "call": {"permission": "workspace", "handler": lambda a: localtools.call(str(a.get("name") or ""), a.get("args") or {})},
            })
        except Exception as exc:
            REGISTRY.register("local_tools", status={"connected": False, "state": "unavailable",
                                                      "error": str(exc)[:180]}, source="brain.localtools")

        try:
            import plugin_host
            REGISTRY.register("plugins_skills", status=_safe_status(lambda: {
                "connected": True, "state": "ready", "plugins": len(plugin_host.listing()),
            }), source="brain.plugin_host", operations={
                "list": {"permission": "read", "handler": lambda _a: plugin_host.listing()},
            })
        except Exception as exc:
            REGISTRY.register("plugins_skills", status={"connected": False, "state": "unavailable",
                                                        "error": str(exc)[:180]}, source="brain.plugin_host")

        try:
            import model_hub
            REGISTRY.register("model_center", status=_safe_status(lambda: {
                "connected": bool((_active_config(cfg).get("api_base") or _active_config(cfg).get("model"))),
                "state": "configured" if (_active_config(cfg).get("api_base") or _active_config(cfg).get("model")) else "unconfigured",
                "model": model_hub.public_current(_active_config(cfg)),
            }), source="brain.model_hub", operations={
                "status": {"permission": "read", "handler": lambda _a: model_hub.public_current(_active_config(cfg))},
            }, metadata={"image_generation": True})
        except Exception as exc:
            REGISTRY.register("model_center", status={"connected": False, "state": "unavailable",
                                                       "error": str(exc)[:180]}, source="brain.model_hub")

        # The Electron P15 window host publishes its authoritative state via
        # /api/capabilities/events.  Until then the truthful state is unknown;
        # a registered capability is not the same as a claimed running app.
        REGISTRY.register("video_workbench", status={"connected": False, "state": "unknown",
                                                       "hosted_software": None},
                          source="client.electron.native-window-service",
                          operations={"status": {"permission": "read", "handler": lambda _a: REGISTRY.get("video_workbench")["status"]}},
                          metadata={"window_control": {"status": "client_only",
                                                        "operations": ["show", "hide", "focus", "attach", "detach", "close"]}})
        REGISTRY.register("workspace", status={"connected": True, "state": "ready"},
                          source="brain.workspaces", operations={
                              "status": {"permission": "read", "handler": lambda _a: {"state": "ready"}},
                          })
        _BOOTSTRAPPED = True
    return REGISTRY


__all__ = ["CapabilityRegistry", "CapabilityError", "REGISTRY", "bootstrap"]
