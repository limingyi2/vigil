# -*- coding: utf-8 -*- (ASCII only)
"""Verify memory write path on real cloud after mem_reset.
1) POST one probe memory  2) manifest shows memory store  3) delete it
4) confirm gone  5) config memory_dir still fixed (rule 2 recheck). ASCII only."""
import json
import urllib.request

BASE = "http://127.0.0.1:8788"
CFG = r"C:\Vigil\config\config.json"
PID = "reset_probe_20261009"


def req(path, tok=None, method="GET", body=None):
    r = urllib.request.Request(BASE + path, method=method)
    if tok:
        r.add_header("Authorization", "Bearer " + tok)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, data, timeout=15) as resp:
        return json.load(resp)


def mem_store(manifest):
    for s in manifest.get("stores") or []:
        if s.get("store") == "memory":
            return s
    return None


def main():
    tok = json.load(open(CFG, encoding="utf-8"))["brain_token"]

    h = req("/api/health")
    print("health:", h)

    # 1) write probe memory
    r1 = req("/api/sync/memories", tok, "POST",
             {"store": "memory",
              "items": [{"id": PID, "type": "event",
                         "text": "memory write probe after reset",
                         "time": "2026-10-09 12:00"}]})
    print("write:", r1)
    assert r1.get("added") == 1, "write failed"

    # 2) manifest shows it
    m1 = req("/api/sync/manifest", tok)
    st = mem_store(m1)
    print("memory store after write:", st)
    assert st and st.get("total") == 1, "manifest missing memory store"

    # 3) delete it
    r2 = req("/api/sync/memories", tok, "POST",
             {"store": "memory", "deletes": [PID]})
    print("delete:", r2)

    # 4) confirm gone
    m2 = req("/api/sync/manifest", tok)
    st2 = mem_store(m2)
    total = st2.get("total") if st2 else 0
    print("memory store after delete:", st2)
    assert total == 0, "probe still present"

    # 5) rule 2 recheck: running process must not have written old config back
    cfg = json.load(open(CFG, encoding="utf-8"))
    md = cfg.get("memory_dir")
    print("memory_dir now:", md)
    assert md == r"C:\Vigil\data\memory", "memory_dir was overwritten!"

    print("VERIFY_OK")


if __name__ == "__main__":
    main()
