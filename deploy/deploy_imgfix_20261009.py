# -*- coding: ascii -*-
"""Deploy wxauto_bridge.py (img-mark fix) to the bridge box.

Flow: backup -> upload -> remote py_compile -> verify new code on box
      -> kill bridge -> launcher rebirth (~20s) -> verify new pid + heartbeat.
Password comes from ZX_PW env var only, never written anywhere.
Host from ZX_BRIDGE_HOST. ASCII only (see ops rule #6)."""
import os
import sys
import time

import paramiko

PW = os.environ.get("ZX_PW")
HOST = os.environ.get("ZX_BRIDGE_HOST", "")
if not PW or not HOST:
    print("ZX_PW / ZX_BRIDGE_HOST not set")
    sys.exit(2)

LOCAL = r"F:\Vigil\wechat_bridge\wxauto_bridge.py"
REMOTE = "C:/zx/wxauto_bridge.py"
BAK = "C:\\zx\\wxauto_bridge.py.bak_imgfix_20261009"
MARKER = "fetch_img"   # new code contains Brain.fetch_img + _fetch_img_to

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", password=PW, timeout=20,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=90):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 1) what is running right now (before any change)
rc, o, e = run('wmic process where "name=\'python.exe\'" get processid,'
               'commandline /format:list')
print("== python cmdlines before (bridge-related) ==")
for line in o.splitlines():
    if "wxauto_bridge" in line or "zx_launcher" in line:
        print("  ", line.strip())

# 2) backup current file
rc, o, e = run("copy /Y C:\\zx\\wxauto_bridge.py %s" % BAK)
print("backup:", rc, o[:120] or e[:120])
if rc != 0:
    cli.close()
    sys.exit(1)

# 3) upload
sftp = cli.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.close()
print("uploaded:", LOCAL, "->", REMOTE)

# 4) remote py_compile (venv python preferred, fallback system python)
rc, o, e = run("C:\\zx\\.venv\\Scripts\\python.exe -m py_compile C:\\zx\\wxauto_bridge.py")
if rc != 0:
    rc2, o2, e2 = run("python -m py_compile C:\\zx\\wxauto_bridge.py")
    print("py_compile fallback rc:", rc2, o2[:200], e2[:200])
    if rc2 != 0:
        cli.close()
        sys.exit(1)
else:
    print("py_compile: OK (venv python)")

# 5) confirm the new code is really on the box
rc, o, e = run('findstr /C:"fetch_img" C:\\zx\\wxauto_bridge.py | find /C /V ""')
n = o.strip() or "0"
print("fetch_img lines on box:", n)
if int(n) < 3:
    print("FAIL: new code not fully present on box, aborting before restart")
    cli.close()
    sys.exit(1)


def bridge_pids():
    # wmic where-clauses get mangled over ssh -> list all, parse locally
    rc, o, e = run('wmic process where "name=\'python.exe\'" get processid,'
                   'commandline /format:list')
    pids, cur = [], {}
    for line in o.splitlines():
        line = line.strip()
        if "=" in line and line.split("=", 1)[0] in ("ProcessId", "CommandLine"):
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


# 6) restart: kill bridge, launcher rebirth loop brings it back with new code
before = bridge_pids()
print("bridge pids before:", before)
for p in before:
    rc, o, e = run("taskkill /F /PID %s" % p)
    print("kill %s:" % p, o[:80] or e[:80])

print("waiting for rebirth ...")
deadline = time.time() + 75
after = []
while time.time() < deadline:
    time.sleep(5)
    after = bridge_pids()
    if after and all(p not in before for p in after):
        print("reborn pids:", after, "after ~%ds" % int(75 - (deadline - time.time())))
        break
    after = []

if not after:
    print("WARN: no new bridge process within 75s - check launcher on the box")
    cli.close()
    sys.exit(1)

# 7) heartbeat freshness
rc, o, e = run("dir C:\\zx\\hb.stamp | findstr hb.stamp")
print("hb.stamp:", o[:120])

cli.close()
print("DEPLOY DONE - test from WeChat: ask her something that triggers a sticker")
