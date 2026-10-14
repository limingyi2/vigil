"""Codex supervision and owned-session consent.

Only registered owned threads may be resumed, controlled or receive a manually
submitted response. Observed desktop sessions remain read-only. The native
login stays in Codex; raw prompts, outputs, commands and credentials are not
persisted in the adapter journal. Only explicitly published reasoning summaries
are retained as bounded summary text. Polling uses a sequence cursor.
"""
import atexit
import collections
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
import uuid

from codex_process import attach_job, close_job


class AdapterError(Exception):
    def __init__(self, code, status=409):
        super().__init__(code)
        self.code, self.status = code, status


TERMINAL = {"completed", "failed", "interrupted"}
SUMMARY_METHODS = {"item/reasoning/summaryTextDelta", "item/reasoning/summaryPartAdded"}
SUMMARY_MAX_CHARS = 65536
SUMMARY_MAX_ITEMS = 16
SUMMARY_MAX_PARTS = 32
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
    "item/permissions/requestApproval", "execCommandApproval", "applyPatchApproval",
}
RPC_METHODS = {
    "initialize", "config/read", "model/list", "thread/start", "thread/resume",
    "thread/list", "thread/read", "thread/loaded/list", "turn/start", "turn/interrupt",
}


class CodexAdapter:
    def __init__(self, root, cli=None, home=None, launch_args=(), environment=None,
                 auto_reconnect=True, request_timeout=25):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspaces = self.root / "workspaces"
        self.workspaces.mkdir(exist_ok=True)
        self.cli = cli or shutil.which("codex")
        self.home, self.launch_args = home, list(launch_args)
        self.environment = dict(environment or {})  # trusted constructor only
        self.auto_reconnect, self.request_timeout = auto_reconnect, request_timeout
        self.connection_id = uuid.uuid4().hex
        self.generation = None
        self.process = None
        self.connection_status = "disconnected"
        self.last_error = None
        self.enabled = False
        self.closed = False
        self.lock = threading.RLock()
        self.connect_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.cv = threading.Condition(self.lock)
        self.responses = {}
        self.requests = 0
        self.transport_counts = {"client_requests": 0, "client_notifications": 0,
                                 "server_responses": 0}
        self.seq = 0
        self.events = collections.deque(maxlen=2000)
        self.seen = collections.OrderedDict()
        self.pending = {}
        # Live item previews are deliberately memory-only. File changes may be
        # delivered on item/started before the approval request references them.
        self.live_items = {}
        self.tasks = {}
        self.observed = {}
        self.models = set()
        self.default_model = None
        self.thread_config = {}
        self.reconnect_worker = None
        self._load_registry()

    def _load_registry(self):
        path = self.root / "owned.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            for record in data["tasks"]:
                task_id = record["id"]
                if len(task_id) != 32 or any(c not in "0123456789abcdef" for c in task_id):
                    raise ValueError("invalid task id")
                if not isinstance(record["thread_id"], str) or not record["thread_id"]:
                    raise ValueError("invalid thread")
                workspace = self._workspace(task_id)
                self.tasks[task_id] = {
                    **record, "workspace": str(workspace), "ownership": "owned",
                    "status": "unknown", "source": "history", "live": False,
                    "connection_generation": None,
                    "turn_request_generation": None,
                }
                for turn in self.tasks[task_id].get("turns", {}).values():
                    turn["source"] = "history"
        except (ValueError, KeyError, TypeError, OSError):
            self.tasks.clear()
            self.last_error = "registry_invalid"

    def _workspace(self, task_id):
        path = (self.workspaces / task_id).resolve()
        if not path.is_relative_to(self.workspaces.resolve()):
            raise AdapterError("workspace_outside_root", 403)
        return path

    def _save(self):
        # Persist owned metadata and the latest turn's reasoning_summary only.
        # Approvals, raw reasoning, prompt/answer text and tool arguments are absent.
        tmp = self.root / "owned.json.tmp"
        tmp.write_text(json.dumps({"version": 1, "tasks": list(self.tasks.values())},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.root / "owned.json")

    @staticmethod
    def _label(value):
        return hashlib.sha256(str(value).encode()).hexdigest()[:16] if value is not None else None

    def _record(self, method, source="realtime", task=None, **fields):
        self.seq += 1
        event = {"seq": self.seq, "at": time.time(), "method": method,
                 "source": source, "connection": self.connection_id,
                 "generation": self.generation, **fields}
        if task:
            event["task"] = self._label(task["id"])
            event["thread"] = self._label(task["thread_id"])
        self.events.append(event)
        # Bounded whitelisted metadata and published summary deltas, never raw payloads.
        path = self.root / "events.jsonl"
        if path.exists() and path.stat().st_size > 4 * 1024 * 1024:
            path.replace(self.root / "events.previous.jsonl")
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _spawn(self):
        if not self.cli:
            raise AdapterError("codex_cli_missing", 503)
        env = os.environ.copy()
        env.update(self.environment)
        if self.home:
            env["CODEX_HOME"] = str(self.home)
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        proc = subprocess.Popen(
            [self.cli, "app-server", "--listen", "stdio://", "-c", "analytics.enabled=false",
             *self.launch_args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, cwd=str(self.root), env=env, **flags)
        try:
            attach_job(proc)
        except OSError:
            proc.terminate(); proc.wait(timeout=4)
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()
            raise
        return proc

    def connect(self):
        with self.connect_lock:
            with self.lock:
                if self.closed:
                    raise AdapterError("adapter_closed", 503)
                self.enabled = True
                if self.connection_status == "connected":
                    return self.status()
                self.connection_status = "connecting"
                self.generation = uuid.uuid4().hex
                generation = self.generation
                self.responses.clear()
            try:
                proc = self._spawn()
                with self.lock:
                    self.process = proc
                proc._codex_reader = threading.Thread(target=self._read, args=(proc, generation), daemon=True)
                proc._codex_stderr_reader = threading.Thread(target=self._drain_stderr, args=(proc,), daemon=True)
                proc._codex_reader.start(); proc._codex_stderr_reader.start()
                initialized = self._rpc("initialize", {
                    "clientInfo": {"name": "vigil_agent_center", "version": "0.1.0"},
                    "capabilities": {"experimentalApi": True},
                })
                self._write({"method": "initialized", "params": {}}, generation)
                cfg = self._rpc("config/read", {"includeLayers": False}).get("config", {})
                self.thread_config = {"web_search": "disabled", "features.apps": False,
                                      "history.persistence": "none", "analytics.enabled": False}
                for group in ("mcp_servers", "plugins"):
                    for name in (cfg.get(group) or {}):
                        self.thread_config[group + "." + name + ".enabled"] = False
                models = self._rpc("model/list", {"includeHidden": False, "limit": 50}).get("data", [])
                self.models = {m["model"] for m in models}
                self.default_model = next((m["model"] for m in models if m.get("isDefault")), None)
                with self.lock:
                    self.connection_status = "connected"
                    self.last_error = None
                    self._record("adapter/connected", protocol_agent=initialized.get("userAgent", "")[:240])
                for task_id in list(self.tasks):
                    self._resume(task_id)
                return self.status()
            except Exception as exc:
                self._disconnect_generation(generation, "connect_failed")
                self._terminate_current(generation)
                raise AdapterError(getattr(exc, "code", "connect_failed"), 503) from None

    @staticmethod
    def _drain_stderr(proc):
        # Drain bounded chunks; do not persist stderr (may contain local config or secrets).
        try:
            while proc.stderr.read(8192):
                pass
        finally:
            proc.stderr.close()

    def _read(self, proc, generation):
        try:
            while True:
                line = proc.stdout.readline(4 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 4 * 1024 * 1024:
                    raise ValueError("frame limit")
                message = json.loads(line)
                with self.cv:
                    if generation != self.generation:
                        continue
                    if "id" in message and "method" not in message:
                        self.responses[message["id"]] = message
                        self.cv.notify_all()
                    else:
                        self.handle_event(message, generation)
        except (OSError, ValueError, TypeError, KeyError):
            pass
        finally:
            close_job(proc)
            if proc.poll() is None:
                proc.terminate()
            proc.wait(timeout=4)
            proc.stdout.close()
            proc.stdin.close()
            self._disconnect_generation(generation, "transport_closed")

    def _disconnect_generation(self, generation, reason):
        with self.cv:
            if generation != self.generation or self.connection_status == "disconnected":
                return
            self.connection_status = "disconnected"
            self.last_error = reason
            for request in self.pending.values():
                if request["state"] in ("pending", "responding"):
                    request["state"] = "expired"
                    request["expired_reason"] = reason
            self.live_items.clear()
            for task in self.tasks.values():
                task["last_known_status"] = task["status"]
                task["status"], task["live"] = "unknown", False
                task["current_operation"] = None
                self._record("task/status/changed", task=task, turn=self._label(task.get("current_turn")),
                             state="unknown", previous_state=task["last_known_status"],
                             state_transition=task["last_known_status"] != "unknown", reason=reason)
            self._record("adapter/disconnected", reason=reason)
            self._save()
            self.cv.notify_all()
            if self.enabled and not self.closed and self.auto_reconnect:
                if not self.reconnect_worker or not self.reconnect_worker.is_alive():
                    self.reconnect_worker = threading.Thread(target=self._reconnect, daemon=True)
                    self.reconnect_worker.start()

    def _reconnect(self):
        for delay in (1, 2, 4):
            time.sleep(delay)
            with self.lock:
                if not self.enabled or self.closed or self.connection_status == "connected":
                    return
            try:
                self.connect()
                return
            except AdapterError:
                continue
        with self.lock:
            if self.closed or not self.enabled:
                return
            self.last_error = "reconnect_exhausted"
            self._record("adapter/reconnect_exhausted")

    def _write(self, message, generation):
        # Guard the transport as well as HTTP: a JSON-RPC server-request answer
        # has result/error instead of method and is forbidden in P1.
        if ("result" in message or "error" in message
                or message.get("method") not in RPC_METHODS | {"initialized"}):
            raise AdapterError("server_response_disabled", 403)
        with self.write_lock:
            with self.lock:
                if generation != self.generation or not self.process or self.process.poll() is not None:
                    raise AdapterError("transport_unavailable", 503)
                proc = self.process
            try:
                proc.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
                proc.stdin.flush()
                with self.lock:
                    counter = "client_requests" if "id" in message else "client_notifications"
                    self.transport_counts[counter] += 1
            except (OSError, ValueError):
                raise AdapterError("transport_unavailable", 503) from None

    def _rpc(self, method, params):
        if method not in RPC_METHODS:
            raise AdapterError("rpc_not_allowed", 403)
        with self.lock:
            generation = self.generation
            self.requests += 1
            request_id = "vigil_" + str(self.requests)
        self._write({"id": request_id, "method": method, "params": params}, generation)
        deadline = time.monotonic() + self.request_timeout
        with self.cv:
            while request_id not in self.responses:
                if generation != self.generation or self.connection_status == "disconnected":
                    raise AdapterError("transport_unavailable", 503)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AdapterError("rpc_timeout", 504)
                self.cv.wait(min(remaining, 1))
            result = self.responses.pop(request_id)
        if "error" in result:
            raise AdapterError("codex_rpc_error", 502)
        return result.get("result", {})

    def _owned(self, task_id):
        task = self.tasks.get(task_id)
        if not task or task.get("ownership") != "owned":
            raise AdapterError("owned_task_required", 403)
        return task

    def _resume(self, task_id):
        with self.lock:
            task = self._owned(task_id)
            params = self._thread_params(task["workspace"], task.get("model"))
            params["threadId"] = task["thread_id"]
            params.pop("ephemeral", None)
        try:
            response = self._rpc("thread/resume", params)
            self._check_permissions(response)
            thread = response.get("thread", {})
            with self.lock:
                self._apply_history(task, thread)
                task["connection_generation"] = self.generation
                self._record("thread/resumed", "history", task)
                self._save()
        except AdapterError as exc:
            with self.lock:
                task["status"], task["live"] = "unknown", False
                task["last_error"] = exc.code
                self._record("thread/resume_failed", "history", task, code=exc.code)

    def _thread_params(self, workspace, model=None):
        return {"model": model or self.default_model, "cwd": workspace,
                "runtimeWorkspaceRoots": [workspace], "sandbox": "read-only",
                "approvalPolicy": "on-request", "approvalsReviewer": "user",
                "ephemeral": False, "config": dict(self.thread_config)}

    @staticmethod
    def _check_permissions(response):
        sandbox = response.get("sandbox") or {}
        if (response.get("approvalPolicy") != "on-request"
                or response.get("approvalsReviewer") != "user"
                or sandbox.get("type") != "readOnly"
                or sandbox.get("networkAccess") is not False):
            raise AdapterError("unexpected_codex_permissions", 403)

    def create_task(self, model=None):
        self.connect()
        if model and model not in self.models:
            raise AdapterError("model_not_available", 400)
        task_id = uuid.uuid4().hex
        workspace = self._workspace(task_id)
        workspace.mkdir()
        response = self._rpc("thread/start", self._thread_params(str(workspace), model))
        self._check_permissions(response)
        thread = response["thread"]
        with self.lock:
            task = {"id": task_id, "thread_id": thread["id"], "workspace": str(workspace),
                    "model": response.get("model", model), "ownership": "owned",
                    "status": "idle", "source": "realtime", "live": True,
                    "created_at": time.time(), "last_activity": time.time(),
                    "connection_generation": self.generation, "turns": {},
                    "current_turn": None, "tool_failures": 0}
            self.tasks[task_id] = task
            self._record("task/created", task=task)
            self._save()
            return copy.deepcopy(task)

    def start_turn(self, task_id, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 16000:
            raise AdapterError("invalid_task_input", 400)
        with self.lock:
            task = self._owned(task_id)
            if self.connection_status != "connected" or task["connection_generation"] != self.generation:
                raise AdapterError("task_not_connected", 409)
            if task["status"] in ("running", "starting", "waiting_approval", "waiting_input"):
                raise AdapterError("turn_already_active", 409)
            task["status"] = "starting"
            task["turn_request_generation"] = self.generation
            thread_id = task["thread_id"]
        try:
            turn = self._rpc("turn/start", {"threadId": thread_id,
                             "input": [{"type": "text", "text": text}], "effort": "low",
                             "summary": "auto",
                             "sandboxPolicy": {"type": "readOnly", "networkAccess": False},
                             "approvalPolicy": "on-request", "approvalsReviewer": "user"})["turn"]
            with self.lock:
                task["current_turn"] = turn["id"]
                existing = task["turns"].get(turn["id"], {})
                if existing.get("status") not in TERMINAL:
                    task["turns"][turn["id"]] = {"id": turn["id"], "status": turn.get("status", "inProgress"), "source": "realtime", "generation": self.generation}
                    if task["status"] == "starting":
                        task["status"] = "running"
                self._save()
                return self.get_task(task_id)
        except AdapterError:
            with self.lock:
                task["status"], task["live"] = "unknown", False
            raise

    def interrupt(self, task_id):
        with self.lock:
            task = self._owned(task_id)
            if task["connection_generation"] != self.generation or not task.get("current_turn"):
                raise AdapterError("no_owned_active_turn", 409)
            params = {"threadId": task["thread_id"], "turnId": task["current_turn"]}
        self._rpc("turn/interrupt", params)
        return {"ok": True, "interrupt_requested": True}

    def handle_event(self, message, generation):
        with self.lock:
            if generation != self.generation:
                return False
            method = message.get("method", "")
            # Full reasoning is neither a summary nor displayable event content.
            if method == "item/reasoning/textDelta":
                return False
            params = message.get("params") or {}
            thread_id = params.get("threadId") or (params.get("thread") or {}).get("id")
            task = next((t for t in self.tasks.values() if t["thread_id"] == thread_id), None)
            # Unowned events may never establish ownership, approval eligibility or control.
            if not task or task.get("connection_generation") != generation:
                return False
            is_summary = method in SUMMARY_METHODS
            if is_summary and not self._valid_summary_event(task, params, generation, method):
                return False
            event_turn = params.get("turnId") or (params.get("turn") or {}).get("id") or task.get("current_turn")
            if method == "turn/started" and task.get("turn_request_generation") != generation:
                return False
            if method == "turn/completed" and task["turns"].get(event_turn, {}).get("generation") != generation:
                return False
            fingerprint = hashlib.sha256(json.dumps([generation, event_turn, message], sort_keys=True).encode()).hexdigest()
            # Identical streaming chunks can be legitimate; structural events are deduped.
            structural = not method.endswith("Delta") and not method.endswith("/delta")
            if structural and fingerprint in self.seen:
                return False
            if structural:
                self.seen[fingerprint] = True
                if len(self.seen) > 8192:
                    self.seen.popitem(last=False)
            now = time.time()
            previous_state = task["status"]
            task["last_activity"] = now
            live_turn = task["turns"].get(task.get("current_turn"), {}).get("generation") == generation
            if method in ("turn/started", "turn/completed") or (method == "thread/status/changed" and live_turn):
                task["source"], task["live"] = "realtime", True
            turn = params.get("turn") or {}
            turn_id = params.get("turnId") or turn.get("id")
            if method in APPROVAL_METHODS or method == "item/tool/requestUserInput":
                request_id = message.get("id")
                if (type(request_id) not in (str, int) or not turn_id or not params.get("itemId")
                        or turn_id != task.get("current_turn")
                        or task["turns"].get(turn_id, {}).get("generation") != generation):
                    self._record("request/unbound", task=task)
                    return False
                if task["turns"].get(turn_id, {}).get("status") in TERMINAL:
                    return False
                identity = json.dumps([self.connection_id, generation, thread_id, turn_id, type(request_id).__name__, request_id])
                key = hashlib.sha256(identity.encode()).hexdigest()
                if key in self.pending:
                    return False
                protocol = self._approval_protocol_data(method, params, task, generation, turn_id)
                self.pending[key] = {"id": key, "connection": self.connection_id,
                                     "generation": generation, "connection_generation": generation,
                                     "task_id": task["id"],
                                     "thread_id": thread_id, "turn_id": turn_id,
                                     "item_id": params["itemId"], "request_id": request_id,
                                     "method": method, "ownership": "owned", "state": "pending",
                                     "created_at": now, "expires_at": now + 300,
                                     "response_enabled": bool(protocol.get("can_respond")),
                                     **protocol}
                if method != "item/tool/requestUserInput" or params.get("isBlocking", True):
                    task["status"] = "waiting_input" if method == "item/tool/requestUserInput" else "waiting_approval"
            elif method in ("turn/started", "turn/completed"):
                if method == "turn/started" and task.get("turn_request_generation") != generation:
                    return False
                if method == "turn/completed" and task["turns"].get(turn_id, {}).get("generation") != generation:
                    return False
                if task["turns"].get(turn_id, {}).get("status") in TERMINAL and method == "turn/started":
                    return False
                if method == "turn/completed" and task.get("current_turn") not in (None, turn_id):
                    task["turns"][turn_id] = {"id": turn_id, "status": turn.get("status", "unknown"), "source": "realtime", "at": now, "generation": generation}
                    self._expire_requests(task["id"], turn_id, "turn_ended")
                    self._record("turn/late_completion", task=task, turn=self._label(turn_id), state=task["status"])
                    self._save()
                    return True
                task["current_turn"] = turn_id
                task["turns"][turn_id] = {"id": turn_id, "status": turn.get("status", "inProgress"), "source": "realtime", "at": now, "generation": generation}
                if method == "turn/started":
                    previous_summary = task.get("reasoning_summary") or {}
                    if previous_summary.get("turn_id") != turn_id or previous_summary.get("generation") != generation:
                        task.pop("reasoning_summary", None)
                    task["status"] = "running"
                else:
                    task["status"] = turn.get("status") if turn.get("status") in TERMINAL else "unknown"
                    task["finished_at"] = now
                    self._expire_requests(task["id"], turn_id, "turn_ended")
            elif method == "thread/status/changed":
                if not live_turn:
                    self._record(method, task=task, state="unknown", state_transition=False)
                    return True
                status = params.get("status") or {}
                flags = status.get("activeFlags", [])
                if "waitingOnApproval" in flags:
                    task["status"] = "waiting_approval"
                elif "waitingOnUserInput" in flags:
                    task["status"] = "waiting_input"
                elif status.get("type") == "active":
                    task["status"] = "running"
                elif status.get("type") == "systemError":
                    task["status"] = "failed"
                    task["last_error"] = "codex_system_error"
            elif method == "serverRequest/resolved":
                request_id = params.get("requestId")
                for request in self.pending.values():
                    if (request["generation"] == generation and request["thread_id"] == thread_id
                            and type(request["request_id"]) is type(request_id)
                            and request["request_id"] == request_id):
                        request["state"] = "resolved"
            elif method == "item/completed":
                item = params.get("item") or {}
                if item.get("id"):
                    self.live_items.pop((task["id"], generation, event_turn, item["id"]), None)
                if item.get("type") == "commandExecution" and item.get("status") == "failed":
                    task["tool_failures"] += 1
            elif method == "error":
                task["last_error"] = "codex_error"
            # P2 view metadata only: never expose commands, arguments or outputs.
            if method == "item/started" and live_turn and event_turn == task.get("current_turn"):
                item = params.get("item") or {}
                if item.get("type") == "fileChange" and isinstance(item.get("changes"), list):
                    self.live_items[(task["id"], generation, event_turn, item.get("id"))] = {
                        "type": "fileChange", "changes": self._bounded_copy(item["changes"], 512 * 1024)}
                if item.get("type") in ("commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall", "webSearch"):
                    task["current_operation"] = {"type": item["type"],
                                                 "item_key": self._label(item.get("id")),
                                                 "started_at": now}
            elif method == "item/completed":
                item = params.get("item") or {}
                if (task.get("current_operation") or {}).get("item_key") == self._label(item.get("id")):
                    task["current_operation"] = None
            if method == "turn/completed":
                task["current_operation"] = None
            # Only controlled metadata is logged; never persist params wholesale.
            metadata = {}
            if is_summary:
                metadata = self._retain_reasoning_summary(task, params, method)
            item = params.get("item") or {}
            if method in ("item/started", "item/completed"):
                metadata = {k: item[k] for k in ("type", "status", "exitCode") if k in item}
                if item.get("id"):
                    metadata["item_key"] = self._label(item["id"])
                duration = item.get("durationMs")
                if type(duration) is int and 0 <= duration <= 86400000:
                    metadata["duration_ms"] = duration
            if method == "thread/tokenUsage/updated":
                last = (params.get("tokenUsage") or {}).get("last") or {}
                metadata["tokens"] = {k: v for k, v in last.items()
                                      if k in ("inputTokens", "outputTokens", "cachedInputTokens", "totalTokens")
                                      and type(v) is int}
            self._record(method, task=task, turn=self._label(event_turn),
                         state=task["status"], previous_state=previous_state,
                         state_transition=previous_state != task["status"],
                         server_request="id" in message, **metadata)
            if structural or is_summary:
                self._save()
            return True

    def _valid_summary_event(self, task, params, generation, method):
        turn_id, item_id, index = params.get("turnId"), params.get("itemId"), params.get("summaryIndex")
        return (self.connection_status == "connected"
                and turn_id == task.get("current_turn") and bool(turn_id)
                and task.get("status") in ("starting", "running", "waiting_approval", "waiting_input")
                and task.get("turns", {}).get(turn_id, {}).get("generation") == generation
                and isinstance(item_id, str) and 0 < len(item_id) <= 256
                and type(index) is int and 0 <= index < SUMMARY_MAX_PARTS
                and (method != "item/reasoning/summaryTextDelta" or isinstance(params.get("delta"), str)))

    def _retain_reasoning_summary(self, task, params, method):
        """Retain only published summary fields, never reasoning content/textDelta."""
        turn_id, index = params["turnId"], params["summaryIndex"]
        item_key = self._label(params["itemId"])
        summary = task.get("reasoning_summary")
        if not summary or summary["turn_id"] != turn_id or summary["generation"] != self.generation:
            summary = {"turn_id": turn_id, "generation": self.generation, "items": [], "truncated": False}
            task["reasoning_summary"] = summary
        metadata = {"item_key": item_key, "summary_index": index}
        item = next((x for x in summary["items"] if x["item_key"] == item_key), None)
        if item is None:
            if len(summary["items"]) >= SUMMARY_MAX_ITEMS:
                summary["truncated"] = True
                return {**metadata, "summary_truncated": True}
            item = {"item_key": item_key, "parts": []}
            summary["items"].append(item)
        part = next((x for x in item["parts"] if x["summary_index"] == index), None)
        if part is None:
            part = {"summary_index": index, "text": ""}
            item["parts"].append(part)
            item["parts"].sort(key=lambda x: x["summary_index"])
        if method == "item/reasoning/summaryTextDelta":
            used = sum(len(p["text"]) for x in summary["items"] for p in x["parts"])
            delta = params["delta"][:max(0, SUMMARY_MAX_CHARS - used)]
            part["text"] += delta
            summary["truncated"] |= len(delta) < len(params["delta"])
            metadata["summary_delta"] = delta
        return {**metadata, "summary_truncated": summary["truncated"]}

    def _expire_requests(self, task_id, turn_id, reason):
        for request in self.pending.values():
            if request["task_id"] == task_id and request["turn_id"] == turn_id and request["state"] in ("pending", "responding"):
                request["state"], request["expired_reason"] = "expired", reason
        for key in list(self.live_items):
            if key[0] == task_id and key[2] == turn_id:
                self.live_items.pop(key, None)

    def approvals(self):
        with self.lock:
            for request in self.pending.values():
                if request["state"] == "pending" and request["expires_at"] <= time.time():
                    request["state"], request["expired_reason"] = "expired", "timeout"
            return copy.deepcopy(list(self.pending.values()))

    @staticmethod
    def _bounded_copy(value, limit):
        try:
            encoded = json.dumps(value, ensure_ascii=False)
            if len(encoded.encode("utf-8")) > limit:
                return None
            return json.loads(encoded)
        except (TypeError, ValueError):
            return None

    def _approval_protocol_data(self, method, params, task, generation, turn_id):
        """Keep only current-turn, in-memory data needed for informed consent.

        This metadata is never written to owned.json or the event journal.
        Methods without all P3 identity and review fields remain display-only.
        """
        common = {"can_respond": False, "blocked_reason": "unsupported_or_incomplete"}
        if method == "item/commandExecution/requestApproval":
            command, cwd = params.get("command"), params.get("cwd")
            decisions = params.get("availableDecisions")
            extra_permissions = params.get("additionalPermissions")
            bounded = None if extra_permissions is None else self._bounded_copy(extra_permissions, 65536)
            risk = {"command": command if isinstance(command, str) and len(command) <= 32768 else None,
                    "cwd": cwd if isinstance(cwd, str) and len(cwd) <= 4096 else None,
                    "reason": params.get("reason") if isinstance(params.get("reason"), str) else None,
                    "kind": params.get("kind") or "command",
                    "additional_permissions": bounded,
                    "command_actions": self._bounded_copy(params.get("commandActions"), 65536),
                    "environment_id": params.get("environmentId") if isinstance(params.get("environmentId"), str) else None,
                    "network_approval_context": self._bounded_copy(params.get("networkApprovalContext"), 65536),
                    "proposed_execpolicy_amendment": self._bounded_copy(params.get("proposedExecpolicyAmendment"), 65536),
                    "proposed_network_policy_amendments": self._bounded_copy(params.get("proposedNetworkPolicyAmendments"), 65536)}
            safe = (risk["command"] is not None and bool(risk["command"].strip())
                    and risk["cwd"] is not None and bool(risk["cwd"].strip())
                    and risk["kind"] == "command"
                    and (extra_permissions is None or bounded is not None)
                    and not extra_permissions
                    and not params.get("networkApprovalContext")
                    and not params.get("proposedExecpolicyAmendment")
                    and not params.get("proposedNetworkPolicyAmendments")
                    and isinstance(decisions, list) and "accept" in decisions and "decline" in decisions)
            return {"risk": risk, "can_respond": safe,
                    "blocked_reason": None if safe else "command_risk_incomplete_or_extra_permissions"}
        if method == "item/fileChange/requestApproval":
            item = self.live_items.get((task["id"], generation, turn_id, params.get("itemId")))
            changes = item.get("changes") if item else None
            changes = self._bounded_copy(changes, 512 * 1024)
            safe = isinstance(changes, list) and bool(changes) and all(
                isinstance(c, dict) and isinstance(c.get("path"), str) and c.get("path")
                and isinstance(c.get("kind"), str) and isinstance(c.get("diff"), str)
                for c in changes)
            risk = {"reason": params.get("reason") if isinstance(params.get("reason"), str) else None,
                    "grant_root": params.get("grantRoot") if isinstance(params.get("grantRoot"), str) else None,
                    "changes": changes}
            return {"risk": risk, "can_respond": safe,
                    "blocked_reason": None if safe else "file_change_preview_unavailable"}
        if method == "item/tool/requestUserInput":
            questions = self._bounded_copy(params.get("questions"), 256 * 1024)
            safe = (params.get("isBlocking") is True and isinstance(questions, list) and bool(questions)
                    and all(isinstance(q, dict) and isinstance(q.get("id"), str) and q["id"]
                            and isinstance(q.get("question"), str) and q["question"]
                            and isinstance(q.get("header"), str)
                            and (q.get("options") is None or isinstance(q.get("options"), list)
                                 and all(isinstance(o, dict) and isinstance(o.get("label"), str)
                                         and isinstance(o.get("description"), str) for o in q["options"]))
                            for q in questions)
                    and len({q["id"] for q in questions}) == len(questions))
            return {"risk": {"questions": questions, "is_blocking": params.get("isBlocking") is True},
                    "can_respond": safe,
                    "blocked_reason": None if safe else "user_input_schema_incomplete_or_nonblocking"}
        return common

    def respond(self, approval_id, identity, action=None, answers=None):
        if not isinstance(identity, dict):
            raise AdapterError("approval_binding_required", 400)
        with self.lock:
            request = self.pending.get(approval_id)
            if not request:
                raise AdapterError("approval_not_found", 404)
            fields = {"connection": "connection", "connection_generation": "connection_generation",
                      "thread_id": "thread_id", "turn_id": "turn_id", "item_id": "item_id"}
            if any(identity.get(external) != request.get(internal)
                   for external, internal in fields.items()):
                raise AdapterError("approval_binding_mismatch", 409)
            request_id = identity.get("request_id")
            if type(request_id) is not type(request["request_id"]) or request_id != request["request_id"]:
                raise AdapterError("approval_binding_mismatch", 409)
            if request["ownership"] != "owned":
                raise AdapterError("owned_session_required", 403)
            if request["state"] != "pending":
                raise AdapterError("approval_already_processed", 409)
            if request["expires_at"] <= time.time():
                request["state"], request["expired_reason"] = "expired", "timeout"
                raise AdapterError("approval_expired", 409)
            if (self.connection_status != "connected" or request["generation"] != self.generation
                    or request["connection"] != self.connection_id):
                request["state"], request["expired_reason"] = "expired", "connection_changed"
                raise AdapterError("approval_connection_stale", 409)
            task = self.tasks.get(request["task_id"])
            if (not task or task.get("ownership") != "owned" or task.get("thread_id") != request["thread_id"]
                    or task.get("connection_generation") != self.generation
                    or task.get("current_turn") != request["turn_id"]
                    or task.get("status") not in ("running", "waiting_approval", "waiting_input")
                    or task.get("turns", {}).get(request["turn_id"], {}).get("generation") != self.generation):
                request["state"], request["expired_reason"] = "expired", "turn_or_ownership_changed"
                raise AdapterError("approval_turn_stale", 409)
            if not request.get("response_enabled"):
                raise AdapterError("approval_risk_incomplete", 409)
            result = self._make_approval_response(request, action, answers)
            request["state"] = "responding"
            generation = request["generation"]
        try:
            self._write_server_response(request, result, generation)
        except AdapterError:
            with self.lock:
                if request.get("state") == "responding":
                    request["state"], request["expired_reason"] = "expired", "response_transport_failed"
            raise
        with self.lock:
            if request.get("state") == "responding":
                request["state"] = "responded"
                request["decision"] = "answer_submitted" if request["method"] == "item/tool/requestUserInput" else action
                request["responded_at"] = time.time()
            return {"ok": True, "state": request["state"]}

    @staticmethod
    def _make_approval_response(request, action, answers):
        method = request["method"]
        if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
            if action not in ("accept", "decline"):
                raise AdapterError("approval_decision_invalid", 400)
            return {"decision": action}
        if method == "item/tool/requestUserInput":
            if action != "answer" or not isinstance(answers, dict):
                raise AdapterError("user_answers_invalid", 400)
            questions = request["risk"]["questions"]
            if set(answers) != {q["id"] for q in questions}:
                raise AdapterError("user_answers_invalid", 400)
            normalized = {}
            for question in questions:
                answer = answers[question["id"]]
                if isinstance(answer, str):
                    answer = [answer]
                options = question.get("options") or []
                allowed = {o["label"] for o in options}
                if (not isinstance(answer, list) or not answer
                        or any(not isinstance(value, str) or len(value) > 8192 for value in answer)
                        or allowed and any(value not in allowed for value in answer)
                        or len(answer) > len(allowed) and allowed and not question.get("isOther")):
                    raise AdapterError("user_answers_invalid", 400)
                normalized[question["id"]] = {"answers": answer}
            return {"answers": normalized}
        raise AdapterError("approval_method_unsupported", 409)

    def _write_server_response(self, request, result, generation):
        """Only the validated one-shot respond path may answer App Server requests."""
        if (request.get("method") not in ("item/commandExecution/requestApproval",
                                           "item/fileChange/requestApproval",
                                           "item/tool/requestUserInput")
                or not isinstance(result, dict)):
            raise AdapterError("server_response_disabled", 403)
        with self.write_lock:
            with self.lock:
                task = self.tasks.get(request.get("task_id"))
                if (request.get("state") != "responding" or generation != self.generation
                        or self.connection_status != "connected" or not self.process
                        or self.process.poll() is not None or not task
                        or task.get("current_turn") != request.get("turn_id")
                        or task.get("thread_id") != request.get("thread_id")
                        or task.get("connection_generation") != generation
                        or task.get("status") not in ("running", "waiting_approval", "waiting_input")):
                    raise AdapterError("approval_connection_stale", 409)
                message = {"id": request["request_id"], "result": result}
                try:
                    self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
                    self.process.stdin.flush()
                    self.transport_counts["server_responses"] += 1
                except (OSError, ValueError):
                    raise AdapterError("transport_unavailable", 503) from None

    @staticmethod
    def _apply_history(task, thread):
        task["status"], task["source"], task["live"] = "unknown", "history", False
        task["current_operation"] = None
        task["turn_request_generation"] = None
        task["history_read_at"] = time.time()
        for turn in thread.get("turns", []):
            task.setdefault("turns", {})[turn["id"]] = {"id": turn["id"], "status": turn.get("status", "unknown"), "source": "history", "generation": None}

    def observe(self, thread_id=None):
        if self.connection_status != "connected":
            self.connect()
        if thread_id:
            # Reading a registered thread through this path still grants no new rights.
            records = [self._rpc("thread/read", {"threadId": thread_id, "includeTurns": True})["thread"]]
            cursor = None
        else:
            result = self._rpc("thread/list", {"limit": 30, "useStateDbOnly": True, "sourceKinds": ["appServer", "cli", "vscode", "unknown"]})
            records, cursor = result.get("data", []), result.get("nextCursor")
        output = []
        with self.lock:
            owned_ids = {t["thread_id"] for t in self.tasks.values()}
            for thread in records:
                if thread["id"] in owned_ids:
                    continue
                record = {"thread_id": thread["id"], "ownership": "observed", "status": "unknown", "live": False, "source": "history", "history_read_at": time.time(), "turns": {}}
                self._apply_history(record, thread)
                self.observed[thread["id"]] = record
                output.append(copy.deepcopy(record))
        return {"items": output, "next_cursor": cursor, "source": "history", "live": False}

    def get_task(self, task_id):
        with self.lock:
            return self._public_task(self._owned(task_id))

    def _public_task(self, task):
        out = copy.deepcopy(task)
        out["event_key"] = self._label(task["id"])
        out["turn_key"] = self._label(task.get("current_turn"))
        if not task.get("live") or task.get("status") in TERMINAL:
            out["current_operation"] = None
        return out

    def snapshot(self):
        with self.lock:
            return {"owned": [self._public_task(t) for t in self.tasks.values()], "observed": copy.deepcopy(list(self.observed.values()))}

    def status(self):
        with self.lock:
            return {"connection_status": self.connection_status, "connection_id": self.connection_id,
                    "generation": self.generation, "last_error": self.last_error,
                    "models": sorted(self.models), "default_model": self.default_model,
                    "approval_response_enabled": True,
                    "transport_counts": dict(self.transport_counts)}

    def event_log(self, after=0):
        with self.lock:
            earliest = self.events[0]["seq"] if self.events else self.seq + 1
            return {"items": copy.deepcopy([e for e in self.events if e["seq"] > after]),
                    "cursor": self.seq, "gap": after > self.seq or after < earliest - 1,
                    "connection_id": self.connection_id}

    def _terminate_current(self, generation):
        with self.lock:
            proc = self.process if generation == self.generation else None
        if proc and proc.poll() is None:
            close_job(proc)
            proc.terminate()
            try:
                proc.wait(timeout=4)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
        if proc:
            for worker in (getattr(proc, "_codex_reader", None), getattr(proc, "_codex_stderr_reader", None)):
                if worker and worker is not threading.current_thread():
                    worker.join(timeout=3)

    def disconnect(self):
        with self.lock:
            self.enabled = False
            generation = self.generation
        self._disconnect_generation(generation, "explicit_disconnect")
        self._terminate_current(generation)
        return self.status()

    def reconnect(self):
        self.disconnect()
        return self.connect()

    def close(self):
        with self.connect_lock:
            with self.lock:
                self.closed = True
                self.enabled = False
            self.disconnect()


_adapter = None
_singleton_lock = threading.Lock()


def get_adapter():
    global _adapter
    with _singleton_lock:
        if _adapter is None:
            from paths import AGENT_CENTER_DIR
            _adapter = CodexAdapter(AGENT_CENTER_DIR)
            atexit.register(_adapter.close)
        return _adapter


def handle_http(method, path, body=None, query=None, *, is_admin=False, is_local=False,
                adapter=None):
    """Existing Brain handlers call this after authentication; no new server/framework."""
    prefix = "/api/agent-center/"
    if not path.startswith(prefix):
        return None
    if not is_local or not is_admin:
        return 403, {"ok": False, "err": "local_admin_required"}
    route = path[len(prefix):].strip("/")
    if not isinstance(body or {}, dict):
        return 400, {"ok": False, "err": "invalid_body"}
    body, query = body or {}, query or {}
    if method == "POST" and route.startswith("approvals"):
        import re
        match = re.fullmatch(r"approvals/([0-9a-f]{64})/respond", route)
        if not match:
            return 404, {"ok": False, "err": "agent_center_route_missing"}
        center = adapter or get_adapter()
        identity = {key: body.get(key) for key in
                    ("connection", "connection_generation", "thread_id", "turn_id", "request_id", "item_id")}
        try:
            return 200, {"ok": True, **center.respond(match.group(1), identity,
                                                       body.get("action"), body.get("answers"))}
        except AdapterError as exc:
            return exc.status, {"ok": False, "err": exc.code}
    center = adapter or get_adapter()
    try:
        if method == "GET" and route == "status":
            out = center.status()
        elif method == "GET" and route == "tasks":
            out = center.snapshot()
        elif method == "GET" and route == "approvals":
            out = {"items": center.approvals(),
                   "response_enabled": center.status().get("approval_response_enabled", False)}
        elif method == "GET" and route == "events":
            out = center.event_log(max(0, int(query.get("after", 0))))
        elif method == "GET" and route == "observed":
            out = center.observe(query.get("thread_id"))
        elif method == "POST" and route in ("connect", "disconnect", "reconnect"):
            out = getattr(center, route)()
        elif method == "POST" and route == "tasks":
            out = center.create_task(body.get("model"))
        elif route.startswith("tasks/"):
            parts = route.split("/")
            if method == "GET" and len(parts) == 2:
                out = center.get_task(parts[1])
            elif method == "POST" and len(parts) == 3 and parts[2] == "turns":
                out = center.start_turn(parts[1], body.get("text"))
            elif method == "POST" and len(parts) == 3 and parts[2] == "interrupt":
                out = center.interrupt(parts[1])
            else:
                return 404, {"ok": False, "err": "agent_center_route_missing"}
        else:
            return 404, {"ok": False, "err": "agent_center_route_missing"}
        return 200, {"ok": True, **out}
    except AdapterError as exc:
        return exc.status, {"ok": False, "err": exc.code}
    except (ValueError, TypeError):
        return 400, {"ok": False, "err": "invalid_request"}
    except (OSError, KeyError):
        return 503, {"ok": False, "err": "agent_center_unavailable"}
