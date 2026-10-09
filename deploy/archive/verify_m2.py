# -*- coding: utf-8 -*- (ASCII only)
"""M2 deploy verify: read token, call read-only sync APIs. ASCII only."""
import json
import urllib.request

BASE = "http://127.0.0.1:8788"


def get(path, tok):
    r = urllib.request.Request(BASE + path,
                               headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(r, timeout=10))


def main():
    tok = json.load(open(r"C:\Vigil\config\config.json",
                         encoding="utf-8"))["brain_token"]
    h = json.load(urllib.request.urlopen(BASE + "/api/health", timeout=8))
    print("health:", h)
    d = get("/api/sync/manifest", tok)
    print("stores:", [(s["store"], s["last_seq"], s["total"])
                      for s in d.get("stores") or []])
    pl = (d.get("personas") or {}).get("personas") or []
    print("personas:", [(p.get("key"), p.get("version")) for p in pl])
    print("deleted:", (d.get("personas") or {}).get("deleted"))
    bv = ((d.get("behavior") or {}).get("version") or "")[:12]
    print("behavior_version:", bv)
    st = get("/api/sync/status", tok)
    print("sync status (cloud self):", st.get("enabled"),
          "outbox:", st.get("outbox_pending"))
    print("VERIFY_OK")


if __name__ == "__main__":
    main()
