# -*- coding: utf-8 -*-
# Deploy wxauto_bridge.py (sticker auto-collect) to the bridge box.
# Backup -> upload -> py_compile -> marker check -> restart -> verify.
# Password from ZX_PW env only. ASCII logs.
import os
import sys
import time

import paramiko

PW = os.environ.get("ZX_PW")
HOST = "203.0.113.10"
LOCAL = r"F:\Vigil\wechat_bridge\wxauto_bridge.py"
REMOTE = "C:/zx/wxauto_bridge.py"
BAK = "C:\\zx\\wxauto_bridge.py.bak_sticker_20261009"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", password=PW, timeout=20,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=120):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


rc, o, e = run("copy /Y C:\\zx\\wxauto_bridge.py %s" % BAK)
print("backup:", rc, (o or e)[:80])
if rc != 0:
    cli.close()
    sys.exit(1)

sftp = cli.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.close()
print("uploaded")

rc, o, e = run("C:\\zx\\.venv\\Scripts\\python.exe -m py_compile C:\\zx\\wxauto_bridge.py")
if rc != 0:
    rc, o, e = run('"C:\\Program Files\\Python312\\python.exe" -m py_compile C:\\zx\\wxauto_bridge.py')
print("py_compile:", rc, (o or e)[:120])
if rc != 0:
    cli.close()
    sys.exit(1)

rc, o, e = run('findstr /C:"_collect_pending_img" C:\\zx\\wxauto_bridge.py | find /C /V ""')
print("collect marker lines:", o or "0")
if int(o or "0") < 2:
    print("FAIL: marker missing")
    cli.close()
    sys.exit(1)


def bridge_pids():
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
        if "wxauto_bridge.py" in cur.get("CommandLine", "") \
                and "zx_launcher" not in cur.get("CommandLine", ""):
            pids.append(cur["ProcessId"])
    return pids


before = bridge_pids()
print("bridge pids before:", before)
for p in before:
    rc, o, e = run("taskkill /F /PID %s" % p)
    print("kill %s: %s" % (p, (o or e)[:50]))
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
    print("WARN: no rebirth in 75s")
    cli.close()
    sys.exit(1)
rc, o, e = run("dir C:\\zx\\hb.stamp | findstr hb.stamp")
print("hb.stamp:", o[:100])
cli.close()
print("BRIDGE STICKER DEPLOY DONE")
