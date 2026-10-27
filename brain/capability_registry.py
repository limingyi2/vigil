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

 A grant is the only thing that authorizes a write.  There is deliberately
no ``authorized=True`` style flag: the caller of :meth:`CapabilityRegistry.invoke`
used to be able to pass it and thereby declare every operation pre-approved,
which meant "the caller is an administrator" was silently equal to "every
capability operation was allowed".  The identity of the caller is now an
*input* to the decision, never a way to skip it.
"""

from __future__ import annotations

import copy
import re
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


# 当前这次调用的调用方身份（线程本地）。DELEGATED 操作的 handler 要自己做
# 门控，而 handler 的入参只有 args —— 由 ``invoke`` 在调用前写入，handler 用
# ``current_actor()`` 读。它来自这次对话，不是模型能填的字段。
_ACTOR = threading.local()


def current_actor() -> dict[str, str]:
    return {"identity": str(getattr(_ACTOR, "identity", "") or ""),
            "session": str(getattr(_ACTOR, "session", "") or ""),
            "target": str(getattr(_ACTOR, "target", "") or "")}


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
        connected = bool((status or {}).get("connected"))
        if permission == READ:
            return STATE_QUERYABLE
        if permission == DELEGATED:
            # Delegated adapters own the final risk/approval gate, but the
            # operation cannot be executable when its backing host is offline.
            return STATE_EXECUTABLE if connected else STATE_UNAVAILABLE
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

        去重按**完整请求指纹**（capability/operation/identity/session/target
        加规范化后的完整参数）判定。参数不同的两个请求（比如同一个插件
        卸载、purge_data 一个 True 一个 False）是两笔不同的副作用，合并
        它们会让用户批 A 执行 B。
        """
        with self._lock:
            key, op = str(capability or "").strip(), str(operation or "").strip()
            req_target = str(target or "").strip() or self._target_from_args(args)
            norm_args = copy.deepcopy(dict(args or {}))
            fingerprint = self._request_fingerprint(key, op, identity, session,
                                                    req_target, norm_args)
            for item in self._requests.values():
                # 去重覆盖 pending 与 executing：同一请求正在执行时，
                # 相同指纹的第二次 invoke 复用原请求（拿到 executing 状态），
                # 绝不创建第二个可执行审批。completed/denied 不去重 ——
                # 用户有意重复同一个操作必须能再次发起。
                if (item["fingerprint"] == fingerprint
                        and item["state"] in ("pending", "executing")):
                    return copy.deepcopy(item)
            rid = "capr_" + uuid.uuid4().hex[:12]
            request = {"request_id": rid, "capability": key, "operation": op,
                       "args": norm_args, "identity": identity,
                       "session": session, "target": req_target,
                       "fingerprint": fingerprint,
                       "task_id": str(task_id or ""), "state": "pending",
                       "created_at": time.time(), "result": None,
                       "approved_by": "", "approved_at": 0.0,
                       "execution_token": None}
            self._requests[rid] = request
            self._publish_locked("capability_authorization_requested", key,
                                 {"operation": op, "request_id": rid,
                                  "identity": identity, "session": session,
                                  "target": req_target})
            return copy.deepcopy(request)

    @staticmethod
    def _request_fingerprint(capability: str, operation: str, identity: str,
                             session: str, target: str,
                             args: Mapping[str, Any]) -> str:
        import hashlib
        import json as _json
        payload = _json.dumps(
            {"capability": capability, "operation": operation,
             "identity": identity, "session": session, "target": target,
             "args": args},
            sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def requests(self, state: str = "", *, public: bool = False) -> list[dict[str, Any]]:
        """审批请求清单。

        ``public=True`` 供 HTTP 端点使用：参数做脱敏和截断（zip_b64 这类
        大体积/敏感内容不回传），避免把一个任务的完整参数暴露给别处。
        """
        with self._lock:
            items = [copy.deepcopy(v) for v in self._requests.values()]
        if state:
            items = [v for v in items if v["state"] == state]
        if public:
            items = [self._public_request(v) for v in items]
        return items

    #  敏感字段识别按**组成部分**判定，不做裸子串匹配：api_key /
    # access_token / refresh_token / client_secret 都能命中（api、access、
    # refresh、client、token 等组成部分），而 keyboard、monkey、keyframe
    # 这类恰好包含子串的普通字段不受影响。data/content/zip_b64 等整字段
    # 名保持精确匹配。
    _SENSITIVE_TOKENS = frozenset({
        'api', 'access', 'refresh', 'client', 'secret', 'token', 'password',
        'passwd', 'pwd', 'credential', 'credentials', 'auth', 'authorization',
        'apikey', 'private', 'b64', 'key'})
    _SENSITIVE_EXACT = frozenset({
        'zip_b64', 'data', 'content', 'secret', 'password', 'token',
        'credential', 'authorization'})
    _TOKEN_RE = re.compile(r'[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+')
    _ARG_VALUE_LIMIT = 160

    @classmethod
    def _is_sensitive_key(cls, name: Any) -> bool:
        raw = str(name or '').strip()
        if not raw:
            return False
        if raw.lower() in cls._SENSITIVE_EXACT:
            return True
        # camelCase / snake_case / kebab-case 统一拆成组成部分
        tokens = {t.lower() for t in cls._TOKEN_RE.findall(raw)}
        return bool(tokens & cls._SENSITIVE_TOKENS)

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {k: ('[已省略]' if cls._is_sensitive_key(k) else cls._sanitize_value(v))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [cls._sanitize_value(v) for v in value[:20]]
        text = str(value)
        if len(text) > cls._ARG_VALUE_LIMIT:
            return text[:cls._ARG_VALUE_LIMIT] + '…[截断]'
        return value

    @classmethod
    def _public_request(cls, item: Mapping[str, Any]) -> dict[str, Any]:
        out = dict(item)
        out["args"] = cls._sanitize_value(item.get("args") or {})
        if isinstance(item.get("result"), Mapping):
            result = dict(item["result"])
            if "receipts" in result and isinstance(result["receipts"], list):
                result["receipts"] = result["receipts"][:8]
            out["result"] = cls._sanitize_value(result)
        out.pop("fingerprint", None)
        # 执行令牌绝不出端点：拿到它等于拿到一次免审批执行权。
        out.pop("execution_token", None)
        return out

    def approve(self, request_id: str, *, approved_by: str = "") -> dict[str, Any]:
        """Approve a pending request and execute it exactly once.

         执行授权不经过 registry 的 grant 表。approve 在**同一次加锁内**
        把请求置为 executing 并签发一次性执行令牌；:meth:`invoke` 校验
        令牌 + 完整参数 + 身份/会话/目标严格相等后**原子消费**令牌再跑
        handler —— 并发重放（哪怕拿着同一个请求号）最多进入 handler 一次。

        - 范围（identity/session/target/完整参数）锁死在请求产生时的值上；
          审批主体只签名（approved_by 进审计字段），**不能**改写原任务的
          身份、会话或目标，也不能扩大执行范围。
        - 执行异常时请求收尾为 completed + 未知状态回执：绝不把状态留在
          executing，也绝不宣称"执行成功"或"未执行"。
        """
        rid = str(request_id or "")
        token = uuid.uuid4().hex
        with self._lock:
            request = self._requests.get(rid)
            if not request:
                return {"ok": False, "error": "request_not_found", "request_id": rid}
            if request["state"] == "denied":
                return {"ok": False, "error": "request_denied", "request_id": rid}
            if request["state"] == "completed":
                return copy.deepcopy(request.get("result")
                                     or {"ok": False, "error": "already_completed"})
            if request["state"] == "executing":
                # 另一个审批动作已在执行这一请求。重复提交必须在这里挡下，
                # 否则同一个副作用操作会被跑两遍。
                return {"ok": False, "error": "request_in_flight", "request_id": rid}
            request["state"] = "executing"
            request["execution_token"] = token
            request["approved_by"] = str(approved_by or "")
            request["approved_at"] = time.time()
        try:
            result = self.invoke(request["capability"], request["operation"],
                                 request["args"], identity=request["identity"],
                                 session=request["session"], target=request["target"],
                                 _via_request=rid, _via_token=token)
        except Exception as exc:
            detail = "%s: %s" % (type(exc).__name__, str(exc)[:240])
            result = {"ok": False, "status": RUN_UNKNOWN,
                      "error": "approval_execution_error", "detail": detail,
                      "receipts": [self._receipt(request["capability"],
                                                 request["operation"],
                                                 RUN_UNKNOWN, reason="exception",
                                                 detail=detail)]}
        finally:
            with self._lock:
                request["execution_token"] = None
        with self._lock:
            if request["state"] != "completed":
                request["state"] = "completed"
            request["result"] = copy.deepcopy(result)
        return result

    def deny(self, request_id: str) -> dict[str, Any]:
        rid = str(request_id or "")
        with self._lock:
            request = self._requests.get(rid)
            if not request:
                return {"ok": False, "error": "request_not_found", "request_id": rid}
            if request["state"] == "denied":
                # 幂等：重复拒绝返回同一个结果。
                return {"ok": True, "request_id": rid, "state": "denied"}
            if request["state"] in ("executing", "approved"):
                # 执行中：既不能说"拒绝且未执行"（可能已经生效），也不能
                # 把 completed 之后的状态覆盖掉。如实拒绝这次操作。
                return {"ok": False, "error": "request_in_flight", "request_id": rid}
            if request["state"] == "completed":
                return {"ok": False, "error": "already_completed", "request_id": rid,
                        "result": copy.deepcopy(request.get("result") or {})}
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
               task_id: str = "", _via_request: str = "",
               _via_token: str = "") -> dict[str, Any]:
        """Run one capability operation and report a unified receipt.

         There is no ``authorized`` parameter.  The caller's identity is an
        input to the decision, not a way to skip it: a write needs a grant.

        ``_via_request`` 是审批路径的私有入口：approve() 执行时带上请求
        编号，这里校验"请求处于 approved、capability/operation/完整参数/
        身份/会话/目标与本次调用完全一致"后才放行 —— 授权不落在共享的
        grant 表里，执行期间并发 invoke 借不到它。参数对不上（哪怕只差
        一个 purge_data）一律拒绝。
        """
        key, op = str(name or "").strip(), str(operation or "").strip()
        tid = str(task_id or ("capt_" + uuid.uuid4().hex[:12]))
        if _via_request:
            #  一次性执行令牌：approve 签发（同一次加锁内写入），这里
            # 校验后**原子消费**。两个线程并发重放同一请求时，令牌只能被
            # 消费一次 —— handler 最多执行一次。identity/session/target
            # 必须与请求创建时的绑定值**严格相等**（空绑定只接受空调用方，
            # 不存在"空 = 任意"）。
            with self._lock:
                req = self._requests.get(str(_via_request))
                args_norm = copy.deepcopy(dict(args or {}))
                valid = bool(
                    req and req.get("state") == "executing"
                    and str(req.get("execution_token") or "") == str(_via_token or "")
                    and bool(_via_token)
                    and req["capability"] == key and req["operation"] == op
                    and req["args"] == args_norm
                    and str(req["identity"] or "") == str(identity or "")
                    and str(req["session"] or "") == str(session or "")
                    and str(req["target"] or "") == str(target or ""))
                if valid:
                    req["execution_token"] = None
            if not valid:
                receipt = self._receipt(key, op, RUN_DENIED, task_id=tid,
                                         reason="request_mismatch",
                                         detail="执行参数与审批请求不一致，或授权已被消费")
                return {"ok": False, "task_id": tid, "capability": key, "operation": op,
                        "status": RUN_DENIED, "error": "request_mismatch",
                        "receipts": [receipt]}
        else:
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
                            # 复用已存在的请求时（比如命中 executing），调用方
                            # 据此知道这不是一笔新的待审批操作。
                            "request_state": request["state"],
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
            # DELEGATED 操作的 handler 要自己做门控（例如链接安装要区分
            # "可信来源直接装"和"其他来源出审批卡"），而 handler 只拿到
            # 参数。调用方身份由这里写入线程本地，handler 用
            # ``current_actor()`` 读 —— 它同样不是模型能填的字段。
            _ACTOR.identity = identity
            _ACTOR.session = session
            _ACTOR.target = target
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
        finally:
            _ACTOR.identity = ""
            _ACTOR.session = ""
            _ACTOR.target = ""
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
                # 链接安装：DELEGATED —— 授权判定在 handler 里，因为"可信来源
                # 直接装、其他来源出审批卡"这条分层规则需要看到来源；registry
                # 只认识权限等级，不认识"这个仓库是不是你自己的"。
                "link_preview": {"permission": READ, "handler": _link_preview_handler},
                "install_link": {"permission": DELEGATED, "handler": _install_link_handler},
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

        import external_apps
        external_apps.register_capability(REGISTRY)
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
    from retrieval_adapters import kb_rows
    results = kb_rows(
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
    from core_features import skill as _skill
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


def _link_preview_handler(args):
    """只读：解析链接并回报清单摘要。下载清单，不下载制品、不落盘。"""
    import link_install as _link
    url = str(args.get("url") or "").strip()
    try:
        plan_data = _link.plan(url, name=str(args.get("name") or ""))
    except _link.LinkError as exc:
        return {"ok": False, "error": exc.code, "detail": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": "link_unresolved",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    out = {"ok": True, "source": plan_data.get("source"),
           "trusted": bool(plan_data.get("trusted")),
           "requires_approval": not bool(plan_data.get("trusted"))}
    if plan_data.get("kind") == "choice":
        out["state"] = "needs_choice"
        out["candidates"] = plan_data.get("candidates") or []
        out["detail"] = plan_data.get("detail")
    else:
        out["summary"] = plan_data.get("summary")
    return out


def _approved_link_request(url: str) -> bool:
    """这次调用是不是"人已经批准过"的那一次。

    审批通过后 ``approve()`` 会把请求置为 executing 再用同一份参数调用一次
    handler —— 这是本进程里"有人点过同意"的唯一证据，模型无法伪造
    （请求只能由 invoke 在授权被拒时创建，approve 只能由 HTTP 端点触发）。
    """
    try:
        for request in REGISTRY.requests(state="executing"):
            if (request.get("capability") == "plugins_skills"
                    and request.get("operation") == "install_link"
                    and str((request.get("args") or {}).get("url") or "").strip() == url):
                return True
    except Exception:
        return False
    return False


def _install_link_handler(args):
    """从链接安装插件。可信来源直接装，其他来源先出审批卡。

     分层放行不等于放松校验：两条路都要过 market.json 校验、制品
    SHA-256 与 Manifest 逐文件哈希；差别只在"要不要人点头"。
    """
    import link_install as _link
    url = str(args.get("url") or "").strip()
    replace = bool(args.get("replace"))
    try:
        plan_data = _link.plan(url, name=str(args.get("name") or ""))
    except _link.LinkError as exc:
        return {"ok": False, "error": exc.code, "detail": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": "link_unresolved",
                "detail": "%s: %s" % (type(exc).__name__, str(exc)[:160])}
    if plan_data.get("kind") == "choice":
        return {"ok": False, "state": "needs_choice",
                "error": "plugin_name_required",
                "source": plan_data.get("source"),
                "detail": plan_data.get("detail"),
                "candidates": plan_data.get("candidates") or []}
    if plan_data.get("trusted") or _approved_link_request(url):
        return _link.execute(plan_data, replace=replace)
    actor = current_actor()
    request = REGISTRY.request_authorization(
        "plugins_skills", "install_link",
        {"url": url, "name": plan_data.get("name")},
        identity=actor["identity"], session=actor["session"],
        target=str(plan_data.get("name") or ""))
    return {"ok": False, "status": RUN_AWAITING,
            "error": "authorization_required",
            "request_id": request["request_id"],
            "source": plan_data.get("source"),
            "summary": plan_data.get("summary"),
            "detail": "来源 %s 不在可信清单内。清单已核验（版本/权限/哈希），"
                      "但要你确认之后才安装" % plan_data.get("source")}


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


__all__ = ["CapabilityRegistry", "CapabilityError", "CapabilityGrant", "REGISTRY",
           "bootstrap", "READ", "DELEGATED", "STATE_QUERYABLE", "STATE_EXECUTABLE",
           "STATE_UNAVAILABLE", "STATE_AWAITING", "RUN_SUCCEEDED", "RUN_FAILED",
           "RUN_AWAITING", "RUN_UNAVAILABLE", "RUN_DENIED", "RUN_UNKNOWN"]


def _runtime_persona_key():
    from pc_context import runtime_persona_key
    return runtime_persona_key()
