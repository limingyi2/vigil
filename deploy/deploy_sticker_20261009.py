# -*- coding: utf-8 -*-
# Sticker rework deploy: cloud side (stickers.py + server.py + sticker_pack.py),
# wipe the old sticker library (user asked: delete the 9 unknown images),
# restart VigilBrain, verify /api/stickers is empty. SSH key auth. ASCII logs.
import os
import sys
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
HOST = "203.0.113.10"
FILES = [
    (r"C:\Users\limingyi\AppData\Local\Temp\zx_stickers_lib.py",
     "C:/Vigil/stickers.py", "C:\\Vigil\\stickers.py.bak_sticker_20261009"),
    (r"C:\Users\limingyi\AppData\Local\Temp\zx_server.py",
     "C:/Vigil/server.py", "C:\\Vigil\\server.py.bak_sticker_20261009"),
    (r"C:\Users\limingyi\AppData\Local\Temp\zx_sticker.py",
     "C:/Vigil/plugins/sticker_pack.py",
     "C:\\Vigil\\plugins\\sticker_pack.py.bak_sticker_20261009"),
]

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", timeout=25, key_filename=KEY,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=120):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


PY = r"C:\Vigil\.venv\Scripts\python.exe"

# 0) list current sticker library (before wipe, for the record)
rc, o, e = run("cmd /c dir /b C:\\Vigil\\data\\stickers\\")
print("== sticker lib before wipe ==")
print(o[:800] or "(empty)")

# 1) backup + upload + py_compile
sftp = cli.open_sftp()
for local, remote, bak in FILES:
    rc, o, e = run("copy /Y %s %s" % (remote.replace("/", "\\"), bak))
    print("backup %s: rc=%d" % (remote, rc))
    if rc != 0:
        print(e[:150])
        cli.close()
        sys.exit(1)
    sftp.put(local, remote)
    print("uploaded", remote)
sftp.close()
for _, remote, _b in FILES:
    rc, o, e = run('"%s" -m py_compile %s' % (PY, remote.replace("/", "\\")))
    print("py_compile %s: rc=%d %s" % (remote, rc, (o or e)[:150]))
    if rc != 0:
        cli.close()
        sys.exit(1)

# 2) markers
rc, o, e = run('findstr /C:"stickers/collect" C:\\Vigil\\server.py | find /C /V ""')
print("server collect-route lines:", o or "0")
rc, o, e = run('findstr /C:"set_desc" C:\\Vigil\\stickers.py | find /C /V ""')
print("stickers set_desc lines:", o or "0")
rc, o, e = run('findstr /C:"wechat_group" C:\\Vigil\\plugins\\sticker_pack.py | find /C /V ""')
print("sticker_pack group lines:", o or "0")

# 3) WIPE the old sticker library (user explicitly asked: delete the 9 images)
rc, o, e = run("cmd /c del /Q C:\\Vigil\\data\\stickers\\*")
print("wipe stickers dir: rc=%d %s" % (rc, (o or e)[:80]))

# 4) kill + restart brain
def server_pids():
    rc, o, e = run('wmic process where "name=\'python.exe\'" get processid,'
                   'commandline /format:list')
    pids, cur = [], {}
    for line in o.splitlines():
        line = line.strip()
        if "=" in line and line.split("=", 1)[0] in ("ProcessId", "CommandLine"):
            k, v = line.split("=", 1)
            cur[k] = v
        if not line and cur.get("ProcessId"):
            if "server.py" in cur.get("CommandLine", ""):
                pids.append(cur["ProcessId"])
            cur = {}
    if cur.get("ProcessId"):
        if "server.py" in cur.get("CommandLine", ""):
            pids.append(cur["ProcessId"])
    return pids


before = server_pids()
print("server pids before:", before)
for p in before:
    rc, o, e = run("taskkill /F /PID %s" % p)
    print("kill %s: %s" % (p, (o or e)[:50]))
rc, o, e = run("schtasks /End /TN VigilBrain", timeout=30)
rc, o, e = run("schtasks /Run /TN VigilBrain", timeout=30)
print("schtasks run:", rc)
deadline = time.time() + 120
up = False
while time.time() < deadline:
    time.sleep(6)
    rc, o, e = run("netstat -ano | findstr :8788 | findstr LISTENING")
    if o:
        up = True
        print("8788 LISTENING after ~%ds" % int(120 - (deadline - time.time())))
        break
if not up:
    print("FAIL: 8788 not listening")
    cli.close()
    sys.exit(1)

# 5) verify: sticker list must be empty now
rc, o, e = run('curl -s "http://127.0.0.1:8788/api/stickers"')
print("stickers now:", o[:200])
cli.close()
print("CLOUD STICKER DEPLOY DONE")
