"""A small shared capability registry and event bus for Vigil modules.

This is intentionally an adapter layer, not a second tool implementation.  A
module registers the real status reader and delegates operations to its
existing API.  The registry only records capability metadata, checks the
declared permission, publishes bounded state changes, and returns the adapter's
actual result.

Authorization model
-------------------
Three permission tiers, and they mean different things:

``read``
    Safe to run for any authenticated caller.  No grant required.
``delegated``
    The adapter performs its own authorization (localtools' risk gate, the
    Electron client's per-call confirmation).  The registry passes through and
    reports the real receipt.
anything else
    A write/control operation.  It needs an explicit **grant** that is scoped
    to identity, session and target resource.

🔴 A grant is the only thing that authorizes a write.  There is deliberately
no ``authorized=True`` style flag: the caller of :meth:`CapabilityRegistry.invoke`
used to be able to pass it and thereby declare every operation pre-approved,
which meant "the caller is an administrator" was silently equal to "every
capability operation was allowed".  The identity of the caller is now an
*input* to the decision, never a way to skip it.
"""

from __future__ import annotations

import copy
import threading
import time
import uuid
from collections import deque
from typing import Any, Callable, Mapping


class CapabilityError(RuntimeError):
    pass


# --- permission tiers -------------------------------------------------------
READ = "read"
DELEGATED = "delegated"
WRITE = "write"
# Everything that is neither READ nor DELEGATED needs a grant.

# --- operation states reported to the Agent ----------------------------------
STATE_QUERYABLE = "queryable"          # 可查询
STATE_EXECUTABLE = "executable"        # 可执行
STATE_UNAVAILABLE = "unavailable"      # 不可用
STATE_AWAITING = "awaiting_authorization"  # 等待授权

# --- run states -------------------------------------------------------------
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_AWAITING = "awaiting_authorization"
RUN_UNAVAILABLE = "unavailable"
RUN_DENIED = "denied"
RUN_UNKNOWN = "unknown"


class CapabilityGrant:
    """An explicit authorization for one capability operation.

    A grant is scoped by four things, because "the caller is an administrator"
    is not a permission:

    * ``capability`` / ``operation`` — what may be done.
    * ``identity`` — who may do it (``"*"`` = any authenticated caller).
    * ``session`` — in which conversation it applies (``""`` = any).
    * ``target`` — on which resource (``""`` = any).

    A grant for ``operation="focus"`` says nothing about ``operation="start"``.
    """

    __slots__ = ("grant_id", "capability", "operation", "identity", "session",
                 "target", "created_at", "expires_at", "source")

    def __init__(self, capability: str, operation: str, *, identity: str = "*",
                 session: str = "", target: str = "", expires_at: float = 0.0,
                 source: str = ""):
        self.grant_id = "capg_" + uuid.uuid4().hex[:12]
        self.capability = str(capability or "")
        self.operation = str(operation or "*")
        self.identity = str(identity or "*")
        self.session = str(session or "")
        self.target = str(target or "")
        self.created_at = time.time()
        self.expires_at = float(expires_at or 0.0)
        self.source = str(source or "")

    @property
    def expired(self) -> bool:
        return bool(self.expires_at) and self.expires_at <= time.time()

    def matches(self, capability: str, operation: str, identity: str,
                session: str, target: str) -> bool:
        if self.expired:
            return False
        if self.capability not in ("*", capability):
            return False
        if self.operation not in ("*", operation):
            return False
        if self.identity not in ("*", identity):
            return False
        if self.session and self.session not in ("*", session):
            return False
        if self.target and self.target not in ("*", target):
            return False
        return True

    def public(self) -> dict[str, Any]:
        return {"grant_id": self.grant_id, "capability": self.capability,
                "operation": self.operation, "identity": self.identity,
                "session": self.session, "target": self.target,
                "created_at": self.created_at, "expires_at": self.expires_at,
                "source": self.source, "expired": self.expired}


class CapabilityRegistry:
    def __init__(self, event_limit: int = 256):
        self._lock = threading.RLock()
        self._items: dict[str, dict[str, Any]] = {}
        self._events = deque(maxlen=max(16, int(event_limit)))
        self._seq = 0
        self._grants: list[CapabilityGrant] = []
        self._requests: dict[str, dict[str, Any]] = {}

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

    # --- registration ------------------------------------------------------
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

    # --- grants ------------------------------------------------------------
    def grant(self, capability: str, operation: str, *, identity: str = "*",
              session: str = "", target: str = "", ttl: float = 0.0,
              source: str = "") -> CapabilityGrant:
        """Record an explicit authorization.  Only the approval path calls this."""
        item = CapabilityGrant(capability, operation, identity=identity,
                               session=session, target=target,
                               expires_at=(time.time() + float(ttl)) if ttl else 0.0,
                               source=source)
        with self._lock:
            self._grants.append(item)
            self._publish_locked("capability_granted", item.capability,
                                 {"operation": item.operation, "grant_id": item.grant_id,
                                  "identity": item.identity, "session": item.session,
                                  "target": item.target})
        return item

    def revoke(self, grant_id: str) -> bool:
        with self._lock:
            for item in list(self._grants):
                if item.grant_id == str(grant_id or ""):
                    self._grants.remove(item)
                    self._publish_locked("capability_revoked", item.capability,
                                         {"grant_id": item.grant_id})
                    return True
        return False

    def grants(self) -> list[dict[str, Any]]:
        with self._lock:
            return [g.public() for g in self._grants]

    def _find_grant(self, capability: str, operation: str, identity: str,
                    session: str, target: str) -> CapabilityGrant | None:
        for item in self._grants:
            if item.matches(capability, operation, identity, session, target):
                return item
        return None

    # --- authorization -----------------------------------------------------
    def check(self, name: str, operation: str, *, identity: str = "",
              session: str = "", target: str = "") -> dict[str, Any]:
        """Decide whether one operation may run.  Returns the decision only."""
        key, op = str(name or "").strip(), str(operation or "").strip()
        with self._lock:
            item = self._items.get(key)
            if not item:
                return {"allowed": False, "reason": "capability_not_found",
                        "capability": key, "operation": op}
            spec = (item.get("operations") or {}).get(op)
            if not isinstance(spec, Mapping) or not callable(spec.get("handler")):
                return {"allowed": False, "reason": "operation_not_supported",
                        "capability": key, "operation": op}
            permission = str(spec.get("permission") or READ)
            if permission == READ:
                return {"allowed": True, "reason": "", "permission": permission,
                        "capability": key, "operation": op}
            if permission == DELEGATED:
                return {"allowed": True, "reason": "", "permission": permission,
                        "capability": key, "operation": op}
            grant = self._find_grant(key, op, identity or "*", session, target)
            if grant is not None:
                return {"allowed": True, "reason": "", "permission": permission,
                        "capability": key, "operation": op, "grant_id": grant.grant_id}
            return {"allowed": False, "reason": "authorization_required",
                    "permission": permission, "capability": key, "operation": op,
                    "identity": identity, "session": session, "target": target}

    def _operation_state(self, item: Mapping[str, Any], op: str, spec: Mapping[str, Any],
                         status: Mapping[str, Any] | None, actor: Mapping[str, Any] | None) -> str:
        if not callable(spec.get("handler")):
            return STATE_UNAVAILABLE
        permission = str(spec.get("permission") or READ)
        if permission in (READ, DELEGATED):
            return STATE_QUERYABLE if permission == READ else STATE_EXECUTABLE
        connected = bool((status or {}).get("connected"))
        if not connected:
            return STATE_UNAVAILABLE
        identity = str((actor or {}).get("identity") or "")
        session = str((actor or {}).get("session") or "")
        target = str((actor or {}).get("target") or "")
        with self._lock:
            granted = self._find_grant(item["name"], op, identity or "*", session, target) is not None
        return STATE_EXECUTABLE if granted else STATE_AWAITING

    # --- reads -------------------------------------------------------------
    def _public(self, item: Mapping[str, Any], actor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        status = item.get("last_status")
        if status is None:
            status = self._clean_status(item.get("status_reader"))
        operations = item.get("operations") or {}
        permissions = copy.deepcopy(item.get("permissions") or {})
        states: dict[str, str] = {}
        for op, spec in operations.items():
            if isinstance(spec, Mapping) and op not in permissions:
                permissions[str(op)] = str(spec.get("permission") or READ)
            states[str(op)] = self._operation_state(item, str(op), spec if isinstance(spec, Mapping) else {},
                                                    status, actor)
        return {
            "name": item["name"], "status": status,
            "operations": sorted(str(k) for k in operations),
            "permissions": permissions,
            "operation_states": states,
            "source": item.get("source") or "",
            "metadata": copy.deepcopy(item.get("metadata") or {}),
            "updated_at": item.get("updated_at"),
        }

    def get(self, name: str, actor: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.get(str(name or "").strip())
            return self._public(item, actor) if item else None

    def snapshot(self, actor: Mapping[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            return {"items": [self._public(item, actor) for item in self._items.values()],
                    "cursor": self._seq}

    # --- events ------------------------------------------------------------
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

    # --- approval requests -------------------------------------------------
    @staticmethod
    def _target_from_args(args: Mapping[str, Any] | None) -> str:
        """Derive the operation target from the requested arguments.

        审批必须落到具体目标上："禁用某个插件"的授权只对那个插件有效，
        不能因为没填 target 就退化成对任意资源都有效的授权。
        """
        if not isinstance(args, Mapping):
            return ""
        for key in ("plugin", "skill", "name", "package", "pkg", "target", "doc", "id"):
            value = args.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:100]
        return ""

    def request_authorization(self, capability: str, operation: str,
                              args: Mapping[str, Any] | None = None, *,
                              identity: str = "", session: str = "",
                              target: str = "", task_id: str = "") -> dict[str, Any]:
        """Record that somebody wants an operation that needs a grant.

        The request is what the client shows the user.  It carries no
        permission of its own — it only says "somebody asked".
        """
        with self._lock:
            key, op = str(capability or "").strip(), str(operation or "").strip()
            req_target = str(target or "").strip() or self._target_from_args(args)
            for item in self._requests.values():
                if (item["capability"] == key and item["operation"] == op
                        and item["identity"] == identity and item["session"] == session
                        and item["target"] == req_target
                        and item["state"] in ("pending", "approved")):
                    return copy.deepcopy(item)
            rid = "capr_" + uuid.uuid4().hex[:12]
            request = {"request_id": rid, "capability": key, "operation": op,
                       "args": copy.deepcopy(dict(args or {})), "identity": identity,
                       "session": session, "target": req_target,
                       "task_id": str(task_id or ""), "state": "pending",
                       "created_at": time.time(), "result": None}
            self._requests[rid] = request
            self._publish_locked("capability_authorization_requested", key,
                                 {"operation": op, "request_id": rid,
                                  "identity": identity, "session": session,
                                  "target": target})
            return copy.deepcopy(request)

    def requests(self, state: str = "") -> list[dict[str, Any]]:
        with self._lock:
            items = [copy.deepcopy(v) for v in self._requests.values()]
        if state:
            items = [v for v in items if v["state"] == state]
        return items

    DEFAULT_GRANT_TTL = 300.0  # 秒。兜底上限：正常情况下授权是一次性的。

    def approve(self, request_id: str, *, identity: str = "", session: str = "",
                target: str = "", grant_ttl: float | None = None) -> dict[str, Any]:
        """Approve a pending request: create the grant, run once, revoke.

        🔴 一次审批只授权**一次**执行。授权在执行后立即作废，无论结果是
        成功、失败还是未知 —— 残留的授权就是绕过下一次审批的后门。TTL 只是
        执行异常中断时的兜底，正常路径靠 revoke。
        """
        rid = str(request_id or "")
        with self._lock:
            request = self._requests.get(rid)
            if not request:
                return {"ok": False, "error": "request_not_found", "request_id": rid}
            if request["state"] == "denied":
                return {"ok": False, "error": "request_denied", "request_id": rid}
            if request["state"] == "completed":
                return dict(request.get("result") or {"ok": False, "error": "already_completed"})
            if request["state"] == "approved":
                # 另一个审批动作已在执行这一请求。重复提交必须在这里挡下，
                # 否则同一个副作用操作会被跑两遍。
                return {"ok": False, "error": "request_in_flight", "request_id": rid}
            request["state"] = "approved"
            # 授权范围锁死在请求产生时的身份/会话/目标上；审批方传值只能
            # 收窄（留空），不能改成别人或别的目标。
            req_identity = str(identity or request["identity"] or "")
            req_session = str(session or request["session"] or "")
            req_target = str(target or request["target"] or "")
        # 双方都不知道身份时维持历史语义（无身份调用方按 "*" 校验）；
        # 只要知道其中一方，授权就绑定到那个身份，绝不放大成 "*"。
        grant_identity = req_identity or "*"
        ttl = self.DEFAULT_GRANT_TTL if grant_ttl is None else float(grant_ttl or 0)
        grant = self.grant(request["capability"], request["operation"],
                           identity=grant_identity, session=req_session,
                           target=req_target, ttl=ttl, source="approval:" + rid)
        try:
            result = self.invoke(request["capability"], request["operation"],
                                 request["args"], identity=req_identity,
                                 session=req_session, target=req_target)
        finally:
            self.revoke(grant.grant_id)
        with self._lock:
            request["state"] = "completed"
            request["result"] = copy.deepcopy(result)
        return result

    def deny(self, request_id: str) -> dict[str, Any]:
        rid = str(request_id or "")
        with self._lock:
            request = self._requests.get(rid)
            if not request:
                return {"ok": False, "error": "request_not_found", "request_id": rid}
            request["state"] = "denied"
            self._publish_locked("capability_authorization_denied", request["capability"],
                                 {"operation": request["operation"], "request_id": rid})
            return {"ok": True, "request_id": rid, "state": "denied"}

    # --- execution ---------------------------------------------------------
    @staticmethod
    def _receipt(capability: str, operation: str, state: str, *, task_id: str = "",
                 reason: str = "", detail: str = "") -> dict[str, Any]:
        return {"capability": capability, "operation": operation, "state": state,
                "task_id": task_id, "reason": str(reason or ""),
                "detail": str(detail or "")[:400]}

    def invoke(self, name: str, operation: str, args: Mapping[str, Any] | None = None,
               *, identity: str = "", session: str = "", target: str = "",
               task_id: str = "") -> dict[str, Any]:
        """Run one capability operation and report a unified receipt.

        🔴 There is no ``authorized`` parameter.  The caller's identity is an
        input to the decision, not a way to skip it: a write needs a grant.
        """
        key, op = str(name or "").strip(), str(operation or "").strip()
        tid = str(task_id or ("capt_" + uuid.uuid4().hex[:12]))
        decision = self.check(key, op, identity=identity, session=session, target=target)
        if not decision.get("allowed"):
            reason = str(decision.get("reason") or "authorization_required")
            receipt = self._receipt(key, op, RUN_DENIED, task_id=tid,
                                     reason=reason,
                                     detail="权限 %s 未获授权" % decision.get("permission"))
            if reason == "authorization_required":
                request = self.request_authorization(key, op, args, identity=identity,
                                                     session=session, target=target, task_id=tid)
                receipt["reason"] = "authorization_required"
                receipt["request_id"] = request["request_id"]
                return {"ok": False, "task_id": tid, "capability": key, "operation": op,
                        "status": RUN_AWAITING, "error": "authorization_required",
                        "permission": decision.get("permission"),
                        "request_id": request["request_id"],
                        "receipts": [receipt]}
            return {"ok": False, "task_id": tid, "capability": key, "operation": op,
                    "status": RUN_UNAVAILABLE, "error": reason, "receipts": [receipt]}

        with self._lock:
            item = self._items.get(key)
            spec = (item.get("operations") or {}).get(op) if item else None
        if not isinstance(spec, Mapping) or not callable(spec.get("handler")):
            receipt = self._receipt(key, op, RUN_UNAVAILABLE, task_id=tid,
                                     reason="operation_not_supported")
            return {"ok": False, "task_id": tid, "capability": key, "operation": op,
                    "status": RUN_UNAVAILABLE, "error": "operation_not_supported",
                    "receipts": [receipt]}
        try:
            result = spec["handler"](dict(args or {}))
        except Exception as exc:
            detail = "%s: %s" % (type(exc).__name__, str(exc)[:240])
            receipt = self._receipt(key, op, RUN_FAILED, task_id=tid,
                                     reason="exception", detail=detail)
            self.publish("operation_result", key,
                         {"operation": op, "ok": False, "task_id": tid,
                          "result": None, "detail": detail})
            return {"ok": False, "task_id": tid, "capability": key, "operation": op,
                    "status": RUN_FAILED, "error": detail, "receipts": [receipt]}
        if isinstance(result, Mapping) and "ok" in result:
            out = dict(result)
        else:
            out = {"ok": True, "result": result}
        # handler 知道真实状态时以它为准：一次"没拿到回执"是 unknown，
        # 不是 failed —— 把不确定说成失败会诱使 Agent 重放一次已经生效的操作。
        status = str(out.get("status") or "").strip() or (
            RUN_SUCCEEDED if out.get("ok") else RUN_FAILED)
        receipt = self._receipt(key, op, status, task_id=tid,
                                 reason="" if out.get("ok") else str(out.get("error") or ""),
                                 detail=str(out.get("detail") or ""))
        receipts = list(out.get("receipts") or [])
        receipts.append(receipt)
        out.update({"task_id": out.get("task_id") or tid, "capability": key,
                    "operation": op, "status": status, "receipts": receipts})
        self.publish("operation_result", key,
                     {"operation": op, "ok": bool(out.get("ok")),
                      "task_id": out.get("task_id") or tid,
                      "result": out.get("result") if "result" in out else None})
        return out


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
                "status": {"permission": READ, "handler": lambda _a: codex.status()},
                "tasks": {"permission": READ, "handler": lambda _a: codex.snapshot()},
                "events": {"permission": READ, "handler": lambda a: codex.event_log(int(a.get("after") or 0))},
                # Creating a Codex task lets Codex act on the machine, so it is
                # a delegated operation: the Codex app-server keeps its own
                # approval policy and the Agent never bypasses it.  A task
                # with no prompt never runs anything, so creation and the
                # first turn belong together.
                "create_task": {"permission": DELEGATED,
                                "handler": lambda a: _codex_create_task(codex, a)},
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
                "status": {"permission": READ, "handler": lambda _a: {"online": localtools.pc_online()}},
                "specs": {"permission": READ, "handler": lambda a: localtools.specs(src=a.get("src"))},
                # localtools.call() enforces its own risk/channel matrix and the
                # client asks the user before anything dangerous, so the
                # registry must not gate it a second time.
                "call": {"permission": DELEGATED, "handler": lambda a: localtools.call(str(a.get("name") or ""), a.get("args") or {})},
            })
        except Exception as exc:
            REGISTRY.register("local_tools", status={"connected": False, "state": "unavailable",
                                                      "error": str(exc)[:180]}, source="brain.localtools")

        try:
            import plugin_host
            REGISTRY.register("plugins_skills", status=_safe_status(lambda: {
                "connected": True, "state": "ready", "plugins": len(plugin_host.listing()),
            }), source="brain.plugin_manager", operations={
                # 查询是只读的：插件清单与单个插件的真实状态直接来自
                # plugin_manager，不经任何缓存或模型转述。
                "list": {"permission": READ, "handler": _plugin_list_handler},
                "plugin_status": {"permission": READ,
                                  "handler": lambda a: _plugin_status_handler(a)},
                # 启用/禁用/卸载会改变插件生命周期，全部需要审批后的一次性
                # 授权。实现只委托 plugin_manager 的既有接口，不另起一套。
                "enable": {"permission": WRITE,
                           "handler": lambda a: _plugin_lifecycle_handler("enable", a)},
                "disable": {"permission": WRITE,
                            "handler": lambda a: _plugin_lifecycle_handler("disable", a)},
                "uninstall": {"permission": WRITE,
                              "handler": lambda a: _plugin_lifecycle_handler("uninstall", a)},
                "skill_status": {"permission": READ, "handler": _skill_status_handler},
                "skill_list": {"permission": READ, "handler": _skill_list_handler},
                "skill_install": {"permission": WRITE, "handler": _skill_install_handler},
                "skill_uninstall": {"permission": WRITE, "handler": _skill_uninstall_handler},
                "skill_reload": {"permission": WRITE, "handler": _skill_reload_handler},
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
                "status": {"permission": READ, "handler": lambda _a: model_hub.public_current(_active_config(cfg))},
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
                          operations={
                              "status": {"permission": READ,
                                         "handler": lambda _a: REGISTRY.get("video_workbench")["status"]},
                              # Window control runs on the client, which asks
                              # the user before touching anything.  Delegated:
                              # the registry must not pretend it can verify the
                              # client's confirmation from here.
                              "focus": {"permission": DELEGATED, "handler": lambda a: _video_window_call("focus", a)},
                              "show": {"permission": DELEGATED, "handler": lambda a: _video_window_call("show", a)},
                              "hide": {"permission": DELEGATED, "handler": lambda a: _video_window_call("hide", a)},
                              "attach": {"permission": DELEGATED, "handler": lambda a: _video_window_call("attach", a)},
                              "detach": {"permission": DELEGATED, "handler": lambda a: _video_window_call("detach", a)},
                              "close": {"permission": DELEGATED, "handler": lambda a: _video_window_call("close", a)},
                          }, metadata={"window_control": {"status": "client_only",
                                                          "operations": ["show", "hide", "focus", "attach", "detach", "close"]}})
        REGISTRY.register("workspace", status={"connected": True, "state": "ready"},
                          source="brain.workspaces", operations={
                              "status": {"permission": READ, "handler": lambda _a: {"state": "ready"}},
                          })

        # ---- 统一检索（storage/unified）----
        # 把聊天/记忆/知识库/工作区的混合检索暴露给 Agent。
        # search 是只读；sync/reindex 会触发文件扫描和向量化，属于 delegated。
        try:
            from storage import unified as _unified
            def _unified_status():
                st = _unified.status()
                st["state"] = "ready" if st.get("unified") else "disabled"
                st["connected"] = bool(st.get("unified"))
                return st
            REGISTRY.register("unified_search", status=_safe_status(_unified_status),
                              source="brain.storage.unified", operations={
                                  "status": {"permission": READ,
                                             "handler": lambda a: _unified.status(
                                                 owner=a.get("owner", ""), src=a.get("src") or "pc",
                                                 persona=a.get("persona") or _runtime_persona_key(), chan=a.get("chan", ""))},
                                  "search": {"permission": READ,
                                             "handler": lambda a: _unified_search_handler(a)},
                                  "sync": {"permission": DELEGATED,
                                           "handler": lambda a: _unified_sync_handler(a)},
                                  "reindex": {"permission": DELEGATED,
                                              "handler": lambda a: _unified_reindex_handler(a)},
                              }, metadata={"description": "混合检索（BM25+向量+RRF），覆盖聊天/记忆/知识库/工作区"})
        except Exception as exc:
            REGISTRY.register("unified_search", status={"connected": False, "state": "unavailable",
                                                        "error": str(exc)[:180]},
                              source="brain.storage.unified")

        # ---- 知识库（kb）----
        # 投喂/搜索/管理知识库文档。ingest 会写入并触发向量化，属于 delegated。
        try:
            import kb as _kb
            def _kb_status():
                st = _kb.stats()
                st["state"] = "ready" if st.get("enabled") else "disabled"
                st["connected"] = bool(st.get("enabled"))
                return st
            REGISTRY.register("knowledge_base", status=_safe_status(_kb_status),
                              source="brain.kb", operations={
                                  "status": {"permission": READ,
                                             "handler": lambda a: _kb.stats(
                                                 persona_key=a.get("persona") or _runtime_persona_key(), src=a.get("src") or "pc")},
                                  "list": {"permission": READ,
                                           "handler": lambda a: _kb.list_docs(
                                               persona_key=a.get("persona") or _runtime_persona_key(), src=a.get("src") or "pc")},
                                  "search": {"permission": READ,
                                             "handler": lambda a: _kb_search_handler(a)},
                                  "ingest_text": {"permission": DELEGATED,
                                                  "handler": lambda a: _kb_ingest_text_handler(a)},
                                  "ingest_path": {"permission": DELEGATED,
                                                  "handler": lambda a: _kb_ingest_path_handler(a)},
                                  "delete": {"permission": DELEGATED,
                                             "handler": lambda a: _kb_delete_handler(a)},
                                  "reindex": {"permission": DELEGATED,
                                              "handler": lambda a: _kb_reindex_handler(a)},
                              }, metadata={"description": "知识库：投喂文档/文本，语义搜索，自动向量化"})
        except Exception as exc:
            REGISTRY.register("knowledge_base", status={"connected": False, "state": "unavailable",
                                                        "error": str(exc)[:180]},
                              source="brain.kb")

        _BOOTSTRAPPED = True
    return REGISTRY


# --------------------------------------------------------------------------
# 统一检索 handler
# --------------------------------------------------------------------------

def _unified_search_handler(args):
    """统一检索：返回结果列表 + 诊断信息。"""
    from storage import unified
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "empty_query"}
    results, info = unified.retrieve(
        query,
        owner=args.get("owner", ""),
        src=args.get("src") or "pc",
        persona=args.get("persona") or _runtime_persona_key(),
        chan=args.get("chan", ""),
        workspace_ids=args.get("workspace_ids"),
        kinds=args.get("kinds"),
        top_k=int(args.get("top_k") or 6),
        budget_chars=int(args.get("budget_chars") or 2400),
    )
    return {
        "ok": True,
        "query": query,
        "method": info.get("method", "unknown"),
        "hits": info.get("hits", 0),
        "vector_available": info.get("vector_available", False),
        "resolution": info.get("resolution"),
        "evidence_status": info.get("evidence_status"),
        "results": [r.to_dict() for r in results],
    }


def _unified_sync_handler(args):
    """同步所有来源数据进检索索引。"""
    from storage import unified
    result = unified.sync_all(
        owner=args.get("owner", ""),
        src=args.get("src") or "pc",
        persona=args.get("persona") or _runtime_persona_key(),
        chan=args.get("chan", ""),
        include_workspace=bool(args.get("include_workspace", True)),
    )
    return {"ok": True, "synced": result}


def _unified_reindex_handler(args):
    """重建向量索引。耗时操作，放 delegated。"""
    from storage import unified
    doc_ids = args.get("doc_ids")
    if doc_ids is not None and not isinstance(doc_ids, list):
        return {"ok": False, "error": "doc_ids_must_be_list"}
    result = unified.reindex_embeddings(doc_ids=doc_ids)
    return {"ok": bool(result.get("ok")), "result": result}


# --------------------------------------------------------------------------
# 知识库 handler
# --------------------------------------------------------------------------

def _kb_search_handler(args):
    """知识库语义搜索。"""
    import kb
    query = str(args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "empty_query"}
    results = kb.search(
        query,
        persona_key=args.get("persona") or _runtime_persona_key(),
        src=args.get("src") or "pc",
        top_k=args.get("top_k"),
        min_score=args.get("min_score"),
    )
    return {"ok": True, "query": query, "count": len(results), "results": results}


def _kb_ingest_text_handler(args):
    """投喂文本到知识库。"""
    import kb
    text = str(args.get("text") or "").strip()
    name = str(args.get("name") or "").strip()
    if not text or not name:
        return {"ok": False, "error": "text_and_name_required"}
    result = kb.ingest_text(
        text, name,
        persona_key=args.get("persona") or _runtime_persona_key(),
        src=args.get("src") or "pc",
    )
    return {"ok": True, "ingested": result}


def _kb_ingest_path_handler(args):
    """投喂文件到知识库。"""
    import kb
    path = str(args.get("path") or "").strip()
    if not path:
        return {"ok": False, "error": "path_required"}
    result = kb.ingest_path(
        path,
        persona_key=args.get("persona") or _runtime_persona_key(),
        src=args.get("src") or "pc",
    )
    return {"ok": True, "ingested": result}


def _kb_delete_handler(args):
    """删除知识库文档。"""
    import kb
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required"}
    result = kb.delete_doc(
        name,
        persona_key=args.get("persona") or _runtime_persona_key(),
        src=args.get("src") or "pc",
    )
    return {"ok": True, "deleted": result}


def _kb_reindex_handler(args):
    """重建知识库索引。"""
    import kb
    result = kb.reindex(
        persona_key=args.get("persona") or _runtime_persona_key(),
        src=args.get("src") or "pc",
    )
    return {"ok": True, "reindexed": result}


# --------------------------------------------------------------------------
# plugins_skills handlers（P20.2）
# --------------------------------------------------------------------------

def _plugin_list_handler(args):
    """插件清单：直接来自 plugin_manager 的真实状态，无缓存无转述。"""
    import plugin_manager
    return {"ok": True, "plugins": plugin_manager.list_plugins()}


def _plugin_status_handler(args):
    import plugin_manager
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required",
                "detail": "要指明插件名才能查状态"}
    item = plugin_manager.status(name)
    if not item.get("installed"):
        return {"ok": False, "error": "not_installed",
                "detail": "插件不存在：%s" % name}
    return {"ok": True, "plugin": item}


def _plugin_lifecycle_handler(action, args):
    """启用/禁用/卸载插件：只委托 plugin_manager，内置不可卸载由它保护。"""
    import plugin_manager
    name = str(args.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "name_required", "detail": "要指明插件名"}
    if action == "enable":
        ok, detail = plugin_manager.enable(name)
    elif action == "disable":
        ok, detail = plugin_manager.disable(name)
    elif action == "uninstall":
        ok, detail = plugin_manager.uninstall(name, purge_data=bool(args.get("purge_data")))
    else:
        return {"ok": False, "error": "operation_not_supported"}
    out = dict(detail if isinstance(detail, dict) else {})
    # plugin_manager 用 err 字段报错；registry 回执统一 error。
    if not ok:
        out["ok"] = False
        out["error"] = str(out.pop("err", "") or action + "_failed")
    out["ok"] = bool(ok and out.get("ok", True))
    out["action"] = action
    # 复核：不信任返回码，重读真实状态作为执行证据。
    current = plugin_manager.status(name)
    out["after"] = {k: current.get(k) for k in
                    ("installed", "enabled", "source_kind", "builtin",
                     "removable", "state", "runtime_state")}
    return out


def _skill_module():
    from plugins import skill as _skill
    return _skill


def _skill_host_offline() -> dict[str, Any]:
    return {"ok": False, "error": "skill_host_offline",
            "status": RUN_UNAVAILABLE,
            "detail": "Skill Host（Node）未运行，此操作不会生效，已停止。请先启动技能宿主。"}


def _skill_status_handler(args):
    skill = _skill_module()
    alive = skill.host_alive()
    return {"ok": True, "host_alive": alive,
            "skills": skill.list_skills() if alive else [],
            "detail": "" if alive else "Skill Host 未运行；技能清单不可用"}


def _skill_list_handler(args):
    skill = _skill_module()
    return {"ok": True, "skills": skill.list_skills()}


def _skill_install_handler(args):
    """安装 Skill：只接受面板已上传的安装包内容（zip_b64），不接受网址。

    安装 API 返回成功不等于技能可用——装后必须由 Skill Host 清单确认
    （_host_has_skill），Host 不在线时根本不落盘。
    """
    import base64 as _b64
    skill = _skill_module()
    raw_b64 = str(args.get("zip_b64") or "").strip()
    if not raw_b64:
        return {"ok": False, "error": "zip_b64_required",
                "detail": "Skill 安装需要上传的安装包内容；不能凭一个网址安装，"
                          "也不会把任意网址当作可信安装源"}
    if not skill.host_alive():
        return _skill_host_offline()
    try:
        raw = _b64.b64decode(raw_b64, validate=False)
    except Exception as exc:
        return {"ok": False, "error": "invalid_base64", "detail": str(exc)[:160]}
    result = skill._install_zip(raw, note="capability")
    out = dict(result if isinstance(result, dict) else {})
    if "err" in out and "error" not in out:
        out["error"] = str(out.pop("err"))
    out["ok"] = bool(out.get("ok"))
    if out["ok"]:
        # 装后核验：Host 清单里必须真的看得见这个包，且工具清单非空。
        verify = skill._host_has_skill(out.get("skill") or "", "")
        out["verified_on_host"] = bool(verify.get("ok"))
        if not out["verified_on_host"]:
            out["ok"] = False
            out["status"] = RUN_UNKNOWN
            out["error"] = "install_unverified"
            out["detail"] = "安装事务成功但 Skill Host 未确认该技能：%s" % (
                verify.get("err") or "清单中不存在")
    return out


def _skill_uninstall_handler(args):
    skill = _skill_module()
    pkg = str(args.get("skill") or args.get("package") or args.get("name") or "").strip()
    if not pkg:
        return {"ok": False, "error": "skill_required", "detail": "要指明技能包名"}
    if not skill.host_alive():
        return _skill_host_offline()
    result = skill._uninstall(pkg)
    out = dict(result if isinstance(result, dict) else {})
    if "err" in out and "error" not in out:
        out["error"] = str(out.pop("err"))
    out["ok"] = bool(out.get("ok"))
    return out


def _skill_reload_handler(args):
    skill = _skill_module()
    if not skill.host_alive():
        return _skill_host_offline()
    try:
        d = skill._post_json("/reload", {}, 30)
        with skill._cache_lock:
            skill._cache["at"] = 0.0
            skill._cache["tools"] = []
        return {"ok": True, "reloaded": d.get("reloaded")}
    except Exception as exc:
        return {"ok": False, "error": "reload_failed", "detail": str(exc)[:160]}


def _codex_create_task(codex: Any, args: Mapping[str, Any]) -> dict[str, Any]:
    """Create a Codex task and give it its first prompt.

    The Codex adapter owns its own approval policy (``approvalPolicy:
    on-request``), so a task that needs the user's approval ends up waiting
    in the Agent Center instead of being force-approved here.
    """
    prompt = str((args or {}).get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "invalid_task_input",
                "detail": "创建 Codex 任务必须带上要它做的事"}
    task = codex.create_task((args or {}).get("model") or None)
    task_id = str((task or {}).get("id") or "")
    started = codex.start_turn(task_id, prompt)
    return {"ok": True, "result": {"task_id": task_id,
                                   "status": (started or {}).get("status"),
                                   "thread_id": (started or {}).get("thread_id") or (task or {}).get("thread_id")},
            "detail": "任务已创建；需要你审批的动作会在 Agent 工作室里等你"}


def _video_window_call(action: str, args: Mapping[str, Any]) -> dict[str, Any]:
    """Route a window action to the Electron client over the existing bridge.

    The Brain must not touch Electron IPC directly: the client owns the native
    window service and asks the user before acting.  We therefore reuse the
    same poll/result channel the local tools use, and report the client's real
    receipt instead of claiming success.
    """
    import json as _json
    import localtools as _lt
    name = "local_video_window_%s" % str(action or "").strip()
    if name not in _lt.REMOTE_TOOLS:
        return {"ok": False, "error": "operation_not_supported",
                "detail": "客户端没有提供这个窗口操作：%s" % action}
    text = str(_lt.call(name, dict(args or {})) or "")
    # 🔴 客户端第一行是机器可读回执，后面才是给人看的说明。以前靠
    # "文字里有没有『失败』"判成败 —— 那是猜，不是凭据：措辞一改就会把
    # 没执行的操作报成成功。拿不到回执就只能说"不确定"，不能算成功。
    head, _, rest = text.partition("\n")
    receipt: Mapping[str, Any] = {}
    try:
        parsed = _json.loads(head.strip())
        if isinstance(parsed, dict):
            receipt = parsed
    except (TypeError, ValueError):
        receipt = {}
    if not receipt:
        return {"ok": False, "status": RUN_UNKNOWN, "error": "receipt_unverified",
                "result": text[:400],
                "detail": "没拿到客户端的执行回执，无法确认这一步是否已经生效"}
    out: dict[str, Any] = {"ok": bool(receipt.get("ok")), "result": receipt,
                           "detail": rest.strip() or str(receipt.get("detail") or "")}
    if not out["ok"]:
        out["error"] = str(receipt.get("error") or "") or "client_rejected"
    return out


__all__ = ["CapabilityRegistry", "CapabilityError", "CapabilityGrant", "REGISTRY",
           "bootstrap", "READ", "DELEGATED", "STATE_QUERYABLE", "STATE_EXECUTABLE",
           "STATE_UNAVAILABLE", "STATE_AWAITING", "RUN_SUCCEEDED", "RUN_FAILED",
           "RUN_AWAITING", "RUN_UNAVAILABLE", "RUN_DENIED", "RUN_UNKNOWN"]


def _runtime_persona_key():
    from pc_context import runtime_persona_key
    return runtime_persona_key()
