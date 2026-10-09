# -*- coding: utf-8 -*-
"""第一阶段服务器 / Agent 统一管理存储与接口辅助函数。

Agent 凭证只从环境变量读取，不写入 management.json：
  VIGIL_MANAGEMENT_AGENT_TOKENS='{"server-b":"随机长令牌","local-pc":"另一个令牌"}'
可选显示名称：
  VIGIL_MANAGEMENT_AGENT_NAMES='{"server-b":"云端服务器 B","local-pc":"本地电脑"}'
"""
import hmac
import json
import os
import tempfile
import threading
import time
from urllib.parse import unquote

try:
    from paths import MANAGEMENT_PATH, ensure
except ImportError:
    from .paths import MANAGEMENT_PATH, ensure

_LOCK = threading.RLock()
_ONLINE_SECONDS = 45
_DELAYED_SECONDS = 90


def _env_json(name):
    try:
        value = json.loads(os.environ.get(name, "{}"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _tokens():
    value = _env_json("VIGIL_MANAGEMENT_AGENT_TOKENS")
    return {str(k): str(v) for k, v in value.items() if str(k).strip() and str(v).strip()}


def _names():
    value = _env_json("VIGIL_MANAGEMENT_AGENT_NAMES")
    return {str(k): str(v)[:100] for k, v in value.items()}


def _path():
    ensure()
    return MANAGEMENT_PATH


def _read():
    path = _path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {"nodes": {}}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"nodes": {}}


def _write(data):
    path = _path()
    directory = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix=".management-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except OSError:
            pass


def _status(last_seen, now=None):
    if not last_seen:
        return "offline"
    age = (now or time.time()) - float(last_seen)
    if age <= _ONLINE_SECONDS:
        return "online"
    if age <= _DELAYED_SECONDS:
        return "delayed"
    return "offline"


def _public_node(node_id, record, now=None):
    names = _names()
    item = dict(record or {})
    item["id"] = node_id
    item["name"] = names.get(node_id) or item.get("name") or node_id
    item["status"] = _status(item.get("last_seen"), now)
    item["last_seen"] = item.get("last_seen")
    item.pop("token", None)
    return item


def _clean_metrics(value):
    value = value if isinstance(value, dict) else {}
    out = {}
    for key in ("cpu_percent", "memory_percent", "disk_percent"):
        v = value.get(key)
        try:
            n = float(v)
            out[key] = max(0.0, min(100.0, round(n, 1))) if n == n else None
        except (TypeError, ValueError):
            out[key] = None
    return out


def _clean_agents(value):
    out = []
    if not isinstance(value, list):
        return out
    for row in value[:50]:
        if not isinstance(row, dict):
            continue
        aid = str(row.get("id") or "")[:80]
        if not aid:
            continue
        status = str(row.get("status") or "unknown")
        if status not in ("online", "offline", "starting", "error", "unknown"):
            status = "unknown"
        out.append({"id": aid, "name": str(row.get("name") or aid)[:100],
                    "status": status, "details": str(row.get("details") or "")[:200]})
    return out


def heartbeat(body, auth_header):
    """验证独立 Agent Token 并记录心跳。返回 (HTTP 状态码, JSON)。"""
    if not isinstance(body, dict):
        return 400, {"ok": False, "err": "请求内容必须是 JSON 对象"}
    node_id = str(body.get("node_id") or "").strip()[:80]
    token = str(auth_header or "")
    if token.startswith("Bearer "):
        token = token[7:]
    configured = _tokens()
    expected = configured.get(node_id, "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        return 401, {"ok": False, "err": "Agent 身份验证失败"}
    now = time.time()
    name = str(body.get("name") or _names().get(node_id) or node_id)[:100]
    record = {
        "name": name,
        "hostname": str(body.get("hostname") or "")[:120],
        "platform": str(body.get("platform") or "")[:120],
        "last_seen": now,
        "metrics": _clean_metrics(body.get("metrics")),
        "agents": _clean_agents(body.get("agents")),
        "logs": [str(x)[:300] for x in body.get("logs", [])[:30]] if isinstance(body.get("logs"), list) else [],
    }
    with _LOCK:
        data = _read()
        nodes = data.setdefault("nodes", {})
        nodes[node_id] = record
        _write(data)
    return 200, {"ok": True, "server_time": now, "interval_seconds": 15}


def _all_nodes():
    with _LOCK:
        data = _read()
    records = data.get("nodes", {}) if isinstance(data.get("nodes"), dict) else {}
    known = _tokens()
    now = time.time()
    result = []
    for node_id in sorted(set(known) | set(records)):
        result.append(_public_node(node_id, records.get(node_id, {}), now))
    return result


def get(path):
    """读取管理 API。未匹配的路由返回 None。"""
    nodes = _all_nodes()
    if path == "/api/management/overview":
        online = sum(1 for n in nodes if n["status"] == "online")
        hub = {"id": "server-a", "name": "云端服务器 A（管理中心）", "kind": "management-hub",
               "status": "online", "last_seen": time.time(), "metrics": {}, "agents": []}
        servers = [hub] + [{**n, "kind": "managed-node"} for n in nodes]
        return 200, {"ok": True, "server_time": time.time(), "nodes_total": len(nodes),
                     "nodes_online": online, "nodes_offline": len(nodes) - online,
                     "nodes": nodes, "servers": servers,
                     "thresholds": {"online_seconds": _ONLINE_SECONDS,
                     "offline_seconds": _DELAYED_SECONDS}}
    if path == "/api/management/servers":
        # A 是管理中心自身；具体 CPU 指标后续由本机 Agent 上报补齐。
        hub = {"id": "server-a", "name": "云端服务器 A（管理中心）", "kind": "management-hub",
               "status": "online", "last_seen": time.time(), "metrics": {}, "agents": []}
        servers = [hub] + [{**n, "kind": "managed-node"} for n in nodes]
        return 200, {"ok": True, "items": servers}
    if path == "/api/management/agents":
        agents = []
        for node in nodes:
            for agent in node.get("agents", []):
                agents.append({**agent, "node_id": node["id"], "node_name": node["name"]})
        return 200, {"ok": True, "items": agents, "nodes": nodes}
    prefix = "/api/management/agents/"
    suffix = "/logs"
    if path.startswith(prefix) and path.endswith(suffix):
        node_id = unquote(path[len(prefix):-len(suffix)]).strip("/")
        with _LOCK:
            record = _read().get("nodes", {}).get(node_id, {})
        if node_id not in _tokens() and not record:
            return 404, {"ok": False, "err": "找不到该节点"}
        return 200, {"ok": True, "node_id": node_id, "items": record.get("logs", [])[-30:]}
    return None
