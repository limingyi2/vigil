# -*- coding: utf-8 -*-
"""M2 验收 e2e：起两个隔离的本地家（VIGIL_HOME），A 当权威源（云端），
B 当本地大脑（sync 指向 A），跑完整的拉取/推送/幂等/删除闭环。

用法：venv\\Scripts\\python.exe tools\\e2e_sync.py
退出码 0 = 全绿。全程只碰临时目录与本机回环端口，不碰生产。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import httpx

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     ".."))
PY = os.path.join(ROOT, "venv", "Scripts", "python.exe")
SERVER = os.path.join(ROOT, "brain", "server.py")

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("%s %s%s" % ("✅" if ok else "❌", name,
                       ("  — " + str(detail)[:160]) if detail else ""),
          flush=True)
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def write_cfg(home, cfg):
    os.makedirs(os.path.join(home, "config"), exist_ok=True)
    with open(os.path.join(home, "config", "config.json"), "w",
              encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def api(port, tok, method, path, body=None, params=None):
    with httpx.Client(timeout=10) as client:
        r = client.request(method, "http://127.0.0.1:%d%s" % (port, path),
                           json=body, params=params or {},
                           headers={"Authorization": "Bearer " + tok})
    return r.status_code, (r.json() if r.content else {})


def wait_health(port, tok, timeout=40):
    dead = time.time() + timeout
    last_err = ""
    while time.time() < dead:
        try:
            code, d = api(port, tok, "GET", "/api/health")
            if code == 200 and d.get("ok"):
                return True
            last_err = "code=%s body=%s" % (code, str(d)[:80])
        except Exception as e:
            last_err = "%s: %s" % (type(e).__name__, str(e)[:80])
        time.sleep(0.6)
    print("   [wait_health %s 失败] %s" % (port, last_err), flush=True)
    return False


def wait_until(fn, timeout=20, step=0.8, trace=None):
    dead = time.time() + timeout
    last = None
    n = 0
    while time.time() < dead:
        try:
            last = fn()
            if last:
                return last
        except Exception as e:
            last = e
        n += 1
        if trace and n % 8 == 0:
            try:
                print("   [trace] %s" % trace(), flush=True)
            except Exception:
                pass
        time.sleep(step)
    return last


def main():
    pa, pb = free_port(), free_port()
    home_a = tempfile.mkdtemp(prefix="syncA_")
    home_b = tempfile.mkdtemp(prefix="syncB_")
    tok = "e2e-token-a"
    write_cfg(home_a, {"brain_token": tok, "bind_host": "127.0.0.1"})
    write_cfg(home_b, {"brain_token": "e2e-token-b", "bind_host": "127.0.0.1",
                       "sync": {"enabled": True,
                                "cloud_url": "http://127.0.0.1:%d" % pa,
                                "cloud_token": tok, "interval_sec": 2}})
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    pa_proc = subprocess.Popen(
        [PY, SERVER], cwd=os.path.join(ROOT, "brain"),
        env=dict(env, VIGIL_HOME=home_a, VIGIL_PORT=str(pa),
                 BRAIN_MUTEX="Global\\VigilSyncE2E_A"),
        stdout=open(os.path.join(ROOT, "logs", "e2e_sync_a.log"), "w",
                    encoding="utf-8"), stderr=subprocess.STDOUT)
    pb_proc = subprocess.Popen(
        [PY, SERVER], cwd=os.path.join(ROOT, "brain"),
        env=dict(env, VIGIL_HOME=home_b, VIGIL_PORT=str(pb),
                 BRAIN_MUTEX="Global\\VigilSyncE2E_B"),
        stdout=open(os.path.join(ROOT, "logs", "e2e_sync_b.log"), "w",
                    encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        if not check("A（权威源）就绪", wait_health(pa, tok)):
            return 1
        if not check("B（本地大脑）就绪", wait_health(pb, "e2e-token-b")):
            return 1

        # 1) 云端写一条记忆（走同步推送接口，等于"云端改了记忆"）
        c, d = api(pa, tok, "POST", "/api/sync/memories",
                   {"store": "memory",
                    "items": [{"id": "m_e2e_cloud", "type": "event",
                               "text": "云端新增的记忆",
                               "time": "2026-10-09 12:00"}]})
        check("权威源写入记忆", c == 200 and d.get("added") == 1, d)

        # 2) B 自动拉到增量
        got = wait_until(lambda: _store_total(pb, "e2e-token-b",
                                              "memory") >= 1)
        check("本地拉到云端记忆增量", bool(got), got)
        mem_file = os.path.join(home_b, "data", "memory", "memory.json")
        ok = False
        try:
            with open(mem_file, encoding="utf-8") as f:
                ok = any(i["id"] == "m_e2e_cloud"
                         for i in json.load(f)["items"])
        except Exception:
            pass
        check("本地落盘含该记忆", ok)

        # 3) 本地新增 → 走真实链路：本地写 outbox → 后台 flusher 推上云。
        #    （生产里 outbox 条目由记忆写入钩子产生，见 tests/test_sync.py；
        #    e2e 里直接喂一条等价条目，验的是"outbox → 云端"这一段。）
        item = {"id": "m_e2e_local", "type": "event",
                "text": "本地新增的记忆", "time": "2026-10-09 12:01"}
        entry = json.dumps({"kind": "memory_add", "ts": time.time(),
                            "tries": 0,
                            "payload": {"store": "memory",
                                        "item": item}},
                           ensure_ascii=False) + "\n"
        ob = os.path.join(home_b, "data", "sync")
        os.makedirs(ob, exist_ok=True)
        with open(os.path.join(ob, "outbox.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(entry * 2)          # 同条目写两遍 = 重推
        #  wait_until 等的是 truthy：total=1 也是 truthy 会立刻返回。
        # 目标条件必须写进 lambda（==2），不能"返回值、比较放 check"。
        atot_ok = wait_until(lambda: _store_total(pa, tok, "memory") == 2,
                             timeout=25, trace=lambda: "A.man=%s"
                             % _a_manifest(pa, tok))
        check("outbox 推送上云（重推幂等）", atot_ok is True,
              _a_manifest(pa, tok))

        # 4) 云端改人设 → 本地拉到
        c, d = api(pa, tok, "POST", "/api/sync/personas",
                   {"key": "default", "version": 1,
                    "fields": {"name": "云端改名", "background": "来自云端"}})
        check("权威源改人设", c == 200 and d.get("status") == "applied", d)
        got = wait_until(lambda: _b_has_persona(pb, "e2e-token-b",
                                                "云端改名"))
        check("本地拉到人设增量", bool(got), got)
        c, d = api(pb, "e2e-token-b", "POST", "/api/sync/personas",
                   {"key": "default", "version": 1,
                    "fields": {"name": "云端改名", "background": "来自云端"}})
        check("人设重推幂等", c == 200 and d.get("status") == "same", d)

        # 5) 云端改行为配置 → 本地拉到
        c, d = api(pa, tok, "POST", "/api/sync/behavior",
                   {"data": {"features": {"weather": False},
                             "features_by_src": {}, "chatty": {"default": 3}}})
        check("权威源改行为配置", c == 200 and d.get("status") == "applied", d)
        got = wait_until(lambda: _b_behavior(pb, "e2e-token-b") ==
                         {"weather": False})
        check("本地拉到行为配置", got is True, got)

        # 6) 本地改行为配置 → 走真实链路：/api/features 落盘并记 outbox，
        #    后台 flusher 推上云；再喂一条重复的 behavior outbox 条目验幂等
        c, d = api(pb, "e2e-token-b", "POST", "/api/features",
                   {"weather": True})
        check("本地行为配置落盘", c == 200
              and d.get("features", {}).get("weather") is True, d)
        got = wait_until(lambda: (_a_behavior(pa, tok) or {}).get("weather")
                         is True, timeout=25)
        check("权威源收到行为配置", got is True, got)
        with open(os.path.join(ob, "outbox.jsonl"), "a",
                  encoding="utf-8") as f:
            f.write(json.dumps({"kind": "behavior", "ts": time.time(),
                                "tries": 0, "payload": {"seg": "features"}},
                               ensure_ascii=False) + "\n")
        c, d = api(pa, tok, "GET", "/api/sync/behavior")
        check("行为配置重推幂等", c == 200
              and (d.get("data") or {}).get("features", {}).get("weather")
              is True, d.get("version"))

        # 7) 云端删记忆 → 本地拉到墓碑
        c, d = api(pa, tok, "POST", "/api/sync/memories",
                   {"store": "memory", "deletes": ["m_e2e_cloud"]})
        check("权威源删记忆", c == 200 and d.get("deleted") == 1, d)
        # 同上：等的是"删完之后只剩 1 条"这个条件本身
        got = wait_until(lambda: _store_total(pb, "e2e-token-b", "memory") == 1,
                         timeout=20)
        check("本地拉到删除（墓碑生效）", got is True, got)

        # 8) 状态接口可用
        c, d = api(pb, "e2e-token-b", "GET", "/api/sync/status")
        check("同步状态接口", c == 200 and d.get("ok")
              and d.get("enabled") is True, d)
    finally:
        for p in (pb_proc, pa_proc):
            try:
                p.terminate()
            except Exception:
                pass
    bad = [r for r in results if not r[1]]
    print("\n== e2e_sync: %d/%d 通过 ==" % (len(results) - len(bad),
                                           len(results)))
    return 1 if bad else 0


def _store_total(port, tok, store):
    c, d = api(port, tok, "GET", "/api/sync/manifest")
    if c != 200:
        return -1
    for s in d.get("stores") or []:
        if s.get("store") == store:
            return int(s.get("total") or 0)
    return 0


def _a_manifest(port, tok):
    c, d = api(port, tok, "GET", "/api/sync/manifest")
    if c != 200:
        return "manifest %d" % c
    return [(s.get("store"), s.get("total"), s.get("last_seq"))
            for s in d.get("stores") or []]


def _b_has_persona(port, tok, name):
    c, d = api(port, tok, "GET", "/api/sync/personas")
    if c != 200:
        return False
    return any((p.get("fields") or {}).get("name") == name
               for p in d.get("personas") or [])


def _b_behavior(port, tok):
    c, d = api(port, tok, "GET", "/api/sync/behavior")
    if c != 200:
        return None
    return (d.get("data") or {}).get("features") or {}


def _a_behavior(port, tok):
    return _b_behavior(port, tok)


if __name__ == "__main__":
    sys.exit(main())
