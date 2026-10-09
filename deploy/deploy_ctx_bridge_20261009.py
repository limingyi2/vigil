# -*- coding: utf-8 -*-
# Deploy wxauto_bridge.py (group context fix) to the bridge box, then
# test the sticker download path end to end (no WeChat UI needed) and
# restart the bridge. Password from ZX_PW env only. ASCII logs.
import os
import sys
import time

import paramiko

PW = os.environ.get("ZX_PW")
HOST = "203.0.113.10"
LOCAL = r"F:\Vigil\wechat_bridge\wxauto_bridge.py"
REMOTE = "C:/zx/wxauto_bridge.py"
BAK = "C:\\zx\\wxauto_bridge.py.bak_ctx_20261009"
RTEST = "C:/zx/_test_fetch.py"
TEST_CODE = r'''
import json, os, sys
sys.path.insert(0, r"C:\zx")
os.chdir(r"C:\zx")
import wxauto_bridge as wb

cfg = json.load(open(r"C:\zx\wxbridge.local.json", encoding="utf-8"))
b = wb.Bridge(cfg)
print("[test] brain picked:", b.brain.pick())
url = b.brain.picked + "/api/stickers"
if b.brain.token:
    url += "?token=" + b.brain.token
import urllib.request
with b.brain._opener.open(url, timeout=20) as r:
    names = json.loads(r.read().decode("utf-8")).get("names") or []
print("[test] sticker count:", len(names))
if not names:
    print("[test] FAIL: sticker list empty")
    sys.exit(1)
name = names[0]
print("[test] testing with:", name)
p = b._fetch_img_to(name)
print("[test] downloaded to:", p)
if not (p and os.path.getsize(p) > 100):
    print("[test] FAIL: download too small or failed")
    sys.exit(1)
head = open(p, "rb").read(8)
print("[test] magic:", head[:4].hex(), "size:", os.path.getsize(p))
p2 = b._fetch_img_to("__no_such_sticker__.gif")
print("[test] missing name returned:", p2)
if p2 is not None:
    print("[test] FAIL: missing name should return None")
    sys.exit(1)
print("[test] FETCH CHECKS PASS")
b._ctx_push("T", "alice", "hi")
b._ctx_push("T", "bob", "hello there")
snap = b._ctx_snapshot("T")
if snap[-1] != "bob：hello there":
    print("[test] FAIL ctx:", snap)
    sys.exit(1)
snap2 = b._ctx_snapshot("T", exclude_last=True)
if "bob：hello there" in snap2:
    print("[test] FAIL ctx exclude_last:", snap2)
    sys.exit(1)
print("[test] CONTEXT BUFFER CHECKS PASS")
'''

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", password=PW, timeout=20,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=120):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 1) backup
rc, o, e = run("copy /Y C:\\zx\\wxauto_bridge.py %s" % BAK)
print("backup:", rc, (o or e)[:100])
if rc != 0:
    cli.close()
    sys.exit(1)

# 2) upload main file + test script
sftp = cli.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.open(RTEST, "wb").write(TEST_CODE.encode("utf-8"))
sftp.close()
print("uploaded wxauto_bridge.py + _test_fetch.py")

# 3) remote py_compile
rc, o, e = run("C:\\zx\\.venv\\Scripts\\python.exe -m py_compile C:\\zx\\wxauto_bridge.py")
if rc != 0:
    rc, o, e = run('"C:\\Program Files\\Python312\\python.exe" -m py_compile C:\\zx\\wxauto_bridge.py')
print("py_compile:", rc, (o or e)[:150])
if rc != 0:
    cli.close()
    sys.exit(1)

# 4) marker check
rc, o, e = run('findstr /C:"_ctx_push" C:\\zx\\wxauto_bridge.py | find /C /V ""')
print("_ctx_push lines on box:", o or "0")

# 5) sticker fetch + ctx buffer test (uses the running brain via tunnel)
rc, o, e = run('"C:\\Program Files\\Python312\\python.exe" C:\\zx\\_test_fetch.py',
               timeout=90)
print(o)
if rc != 0:
    print("TEST FAIL rc:", rc, e[:200])
    cli.close()
    sys.exit(1)


def bridge_pids():
    rc, o, e = run('wmic process where "name=\'python.exe\'" get processid,'
                   'commandline /format:list')
    pids, cur = [], {}
    for line in o.splitlines():
        line = line.strip()
        if "=" in line and line.split("=", 1)[0] in ("ProcessId",
                                                    "CommandLine"):
            k, v = line.split("=", 1)
            cur[k] = v
        if not line and cur.get("ProcessId"):
            cl = cur.get("CommandLine", "")
            if "wxauto_bridge.py" in cl and "zx_launcher" not in cl:
                pids.append(cur["ProcessId"])
            cur = {}
    if cur.get("ProcessId"):
        cl = cur.get("CommandLine", "")
        if "wxauto_bridge.py" in cl and "zx_launcher" not in cl:
            pids.append(cur["ProcessId"])
    return pids


# 6) restart bridge so the new code takes over
before = bridge_pids()
print("bridge pids before:", before)
for p in before:
    rc, o, e = run("taskkill /F /PID %s" % p)
    print("kill %s: %s" % (p, (o or e)[:60]))
rc, o, e = run('del C:\\zx\\_test_fetch.py')
print("cleanup test script:", rc)
deadline = time.time() + 75
after = []
while time.time() < deadline:
    time.sleep(5)
    after = bridge_pids()
    if after and all(p not in before for p in after):
        print("reborn pids:", after, "after ~%ds"
              % int(75 - (deadline - time.time())))
        break
    after = []
if not after:
    print("WARN: no new bridge process within 75s")
    cli.close()
    sys.exit(1)
rc, o, e = run("dir C:\\zx\\hb.stamp | findstr hb.stamp")
print("hb.stamp:", o[:120])
cli.close()
print("BRIDGE DEPLOY + TEST DONE")
