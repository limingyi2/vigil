# -*- coding: utf-8 -*-
"""M5 e2e：记忆管理 API（stores/list/edit/delete）跑在本机、临时家。

预置一份 memory.json（2 条），起真 server.py，打真接口：
  stores 列分区 / list 新→旧 / edit 改文本 / delete 删条目 /
  白名单拒怪名 / 坏 token 401。删除后墓碑在文件里可见（同步账本）。
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAIN = os.path.join(ROOT, "brain")
SERVER = os.path.join(BRAIN, "server.py")
PY = sys.executable

PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("  [PASS] " if ok else "  [FAIL] ") + name +
          ("" if ok else "  ← %s" % str(detail)[:200]), flush=True)
    return ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def api(method, path, body=None):
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path),
                               method=method)
    r.add_header("Authorization", "Bearer " + TOK)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data, timeout=15) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.load(e)
        except Exception:
            return e.code, {}


def main():
    global PORT, TOK
    print("[1] 临时家 + 预置记忆", flush=True)
    PORT = free_port()
    home = tempfile.mkdtemp(prefix="memM5_")
    os.makedirs(os.path.join(home, "config"), exist_ok=True)
    os.makedirs(os.path.join(home, "data", "memory"), exist_ok=True)
    TOK = "e2e-token-m5"
    with open(os.path.join(home, "config", "config.json"), "w",
              encoding="utf-8") as f:
        json.dump({"brain_token": TOK, "bind_host": "127.0.0.1"}, f)
    mem_json = os.path.join(home, "data", "memory", "memory.json")
    seed = {"items": [
        {"id": "m_seed_old", "type": "fact", "text": "她喜欢深夜的电台",
         "time": "2026-10-01 23:00", "seq": 1},
        {"id": "m_seed_new", "type": "event", "text": "他给她寄了伞",
         "time": "2026-10-08 09:30", "seq": 2},
    ], "meta": {"created": "2026-10-01"}}
    with open(mem_json, "w", encoding="utf-8") as f:
        json.dump(seed, f, ensure_ascii=False)

    print("[2] 起 server", flush=True)
    env = dict(os.environ, VIGIL_HOME=home, VIGIL_PORT=str(PORT),
               BRAIN_MUTEX="Global\\VigilMemE2E")
    os.makedirs(os.path.join(ROOT, "logs"), exist_ok=True)
    proc = subprocess.Popen(
        [PY, SERVER], cwd=BRAIN, env=env,
        stdout=open(os.path.join(ROOT, "logs", "e2e_mem.log"), "w",
                    encoding="utf-8"), stderr=subprocess.STDOUT)
    try:
        dead = time.time() + 40
        ready = False
        while time.time() < dead:
            try:
                with urllib.request.urlopen(
                        "http://127.0.0.1:%d/api/health" % PORT,
                        timeout=3) as r:
                    if json.load(r).get("ok"):
                        ready = True
                        break
            except Exception:
                pass
            time.sleep(0.6)
        if not check("server 就绪", ready):
            return 1

        print("[3] stores / list", flush=True)
        st, d = api("GET", "/api/memory/stores")
        check("stores 200 + 含主库", st == 200 and "memory" in d.get("items", []),
              d)
        st, d = api("GET", "/api/memory/list?store=memory")
        ids = [i["id"] for i in d.get("items", [])]
        check("list 200 + 2 条", st == 200 and d.get("total") == 2, d)
        check("list 新→旧", ids[:1] == ["m_seed_new"], ids)
        st, d = api("GET", "/api/memory/list?store=../evil")
        check("怪分区名 400", st == 400, (st, d))

        print("[4] edit", flush=True)
        st, d = api("POST", "/api/memory/edit",
                    {"store": "memory", "id": "m_seed_new",
                     "text": "他给她寄了伞，还附了张纸条"})
        check("edit 200 + 新文本", st == 200 and
              d.get("item", {}).get("text", "").endswith("纸条"), d)
        st, d = api("GET", "/api/memory/list?store=memory")
        got = [i for i in d.get("items", []) if i["id"] == "m_seed_new"]
        check("list 里读到改后文本", got and got[0]["text"].endswith("纸条"))
        st, d = api("POST", "/api/memory/edit",
                    {"store": "memory", "id": "m_seed_old", "text": "  "})
        check("空文本 404", st == 404, (st, d))

        print("[5] delete", flush=True)
        st, d = api("POST", "/api/memory/delete",
                    {"store": "memory", "id": "m_seed_old"})
        check("delete 200", st == 200, (st, d))
        st, d = api("GET", "/api/memory/list?store=memory")
        ids = [i["id"] for i in d.get("items", [])]
        check("删完 list 只剩 1 条", st == 200 and ids == ["m_seed_new"], ids)
        with open(mem_json, encoding="utf-8") as f:
            disk = json.load(f)
        check("墓碑进账本（同步可对账）",
              any(t["id"] == "m_seed_old"
                  for t in disk.get("meta", {}).get("del", [])))

        print("[6] 鉴权", flush=True)
        r = urllib.request.Request(
            "http://127.0.0.1:%d/api/memory/stores" % PORT)
        r.add_header("Authorization", "Bearer wrong-token")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                st = resp.status
        except urllib.error.HTTPError as e:
            st = e.code
        check("坏 token 401", st == 401, st)

        print("=" * 46)
        print("PASS %d / FAIL %d" % (len(PASS), len(FAIL)))
        return 1 if FAIL else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
