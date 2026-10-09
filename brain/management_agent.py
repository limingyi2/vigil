# -*- coding: utf-8 -*-
"""服务器/电脑监控 Agent。

两种用法：
1) 独立运行（服务器 B 等没有大脑的节点）：
   python management_agent.py
   必填：VIGIL_MANAGEMENT_URL / VIGIL_MANAGEMENT_NODE_ID / VIGIL_MANAGEMENT_TOKEN
   可选：VIGIL_MANAGEMENT_NAME / VIGIL_MANAGEMENT_INTERVAL
2) 内嵌运行（本地电脑随大脑自动上报，无需单独开脚本）：
   由 server.py 调 start_from_config(api_config)，
   读取 config.json 的 management 段 + sync.cloud_url，起一个后台守护线程。

建议每台节点使用不同的随机 Token。
"""
import json
import os
import platform
import socket
import threading
import time
import urllib.error
import urllib.request

_STARTED = False
_LOCK = threading.Lock()


def metrics():
    result = {"cpu_percent": None, "memory_percent": None, "disk_percent": None}
    try:
        import psutil
        result["cpu_percent"] = psutil.cpu_percent(interval=0.2)
        result["memory_percent"] = psutil.virtual_memory().percent
        result["disk_percent"] = psutil.disk_usage(os.path.abspath(os.sep)).percent
    except Exception:
        pass
    return result


def _resolve_from_config(cfg):
    """从 config 字典解析节点参数；缺关键项返回 None。"""
    mg = (cfg or {}).get("management") or {}
    sync = (cfg or {}).get("sync") or {}
    if mg.get("enabled") is False:
        return None
    base = (mg.get("cloud_url") or sync.get("cloud_url") or "").rstrip("/")
    node_id = str(mg.get("node_id") or "").strip()
    token = str(mg.get("token") or "").strip()
    if not base or not node_id or not token:
        return None
    return {
        "base": base,
        "node_id": node_id,
        "token": token,
        "name": mg.get("name") or node_id,
        "interval": max(10, int(mg.get("interval", 15) or 15)),
    }


def _loop(node_id, name, url, token, interval):
    first = True
    while True:
        payload = {"node_id": node_id, "name": name, "hostname": socket.gethostname(),
                   "platform": platform.platform(), "metrics": metrics(),
                   "agents": [], "logs": []}
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer " + token}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                if response.status != 200:
                    print("[管理]心跳失败：HTTP", response.status, flush=True)
                elif first:
                    print("[管理]心跳已连通：", node_id, "→", url, flush=True)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print("[管理]心跳发送失败：", str(exc)[:200], flush=True)
        first = False
        time.sleep(interval)


def start_from_config(cfg):
    """内嵌用：读 config 起守护线程上报。返回 True/False，绝不抛异常，不清出聊天。"""
    global _STARTED
    try:
        p = _resolve_from_config(cfg)
        if not p:
            return False
        with _LOCK:
            if _STARTED:
                return True
            _STARTED = True
        url = p["base"] + "/api/management/heartbeat"
        t = threading.Thread(
            target=_loop,
            args=(p["node_id"], p["name"], url, p["token"], p["interval"]),
            name="vigil-management-heartbeat", daemon=True)
        t.start()
        print("[管理]心跳线程已启动：节点 %s → %s（每 %ds）"
              % (p["node_id"], p["base"], p["interval"]), flush=True)
        return True
    except Exception as exc:  # 任何异常都不能影响大脑启动
        print("[管理]心跳启动失败（不影响聊天）：", str(exc)[:200], flush=True)
        return False


def main():
    base = os.environ.get("VIGIL_MANAGEMENT_URL", "").rstrip("/")
    node_id = os.environ.get("VIGIL_MANAGEMENT_NODE_ID", "").strip()
    token = os.environ.get("VIGIL_MANAGEMENT_TOKEN", "").strip()
    if not base or not node_id or not token:
        raise SystemExit("请设置 VIGIL_MANAGEMENT_URL、VIGIL_MANAGEMENT_NODE_ID、VIGIL_MANAGEMENT_TOKEN")
    url = base + "/api/management/heartbeat"
    interval = max(10, int(os.environ.get("VIGIL_MANAGEMENT_INTERVAL", "15")))
    name = os.environ.get("VIGIL_MANAGEMENT_NAME", node_id)
    _loop(node_id, name, url, token, interval)


if __name__ == "__main__":
    main()
