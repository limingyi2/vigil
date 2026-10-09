# -*- coding: utf-8 -*-
# Deploy server.py + gate.py (group context fix) to the cloud brain box.
# Flow: backup -> upload -> remote py_compile -> verify marker -> kill
#       server.py -> schtasks restart -> wait 8788 LISTENING.
# SSH key auth. Passwords never used here. ASCII only (ops rule #6).
import os
import sys
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
HOST = "203.0.113.10"
FILES = [
    (r"C:\Users\limingyi\AppData\Local\Temp\zx_server.py",
     "C:/Vigil/server.py",
     "C:\\Vigil\\server.py.bak_ctx_20261009"),
    (r"C:\Users\limingyi\AppData\Local\Temp\zx_gate.py",
     "C:/Vigil/plugins/gate.py",
     "C:\\Vigil\\plugins\\gate.py.bak_ctx_20261009"),
]

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", timeout=25, key_filename=KEY,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=90):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 0) show current gate config (was it even enabled?)
# python 不在云机 PATH —— 从在跑的 server.py 进程反查它的解释器路径
def _py_exe():
    rc, o, e = run('wmic process where "name=\'python.exe\'" get '
                   'commandline /format:list')
    for line in o.splitlines():
        line = line.strip()
        if line.startswith("CommandLine=") and "server.py" in line:
            exe = line.split("CommandLine=", 1)[1].strip().strip('"')
            if exe.lower().endswith(".exe"):
                return exe
            return exe.split("server.py")[0].strip().strip('"')
    return "python"


PY = _py_exe()
print("python on box:", PY)

# 1) backup
for local, remote, bak in FILES:
    rc, o, e = run("copy /Y %s %s" % (remote.replace("/", "\\"), bak))
    print("backup %s: rc=%d" % (remote, rc))
    if rc != 0:
        print(e[:150])
        cli.close()
        sys.exit(1)

# 2) upload + remote py_compile
sftp = cli.open_sftp()
for local, remote, bak in FILES:
    sftp.put(local, remote)
    print("uploaded", remote)
sftp.close()
for _, remote, _b in FILES:
    rc, o, e = run('"%s" -m py_compile %s' % (PY, remote.replace("/", "\\")))
    print("py_compile %s: rc=%d %s" % (remote, rc, (o or e)[:150]))
    if rc != 0:
        cli.close()
        sys.exit(1)

# 3) verify new code really on the box
rc, o, e = run('findstr /C:"_ctx_for_gate" C:\\Vigil\\server.py | find /C /V ""')
print("server.py marker lines:", o or "0")
if int(o or "0") < 2:
    print("FAIL: marker missing")
    cli.close()
    sys.exit(1)
rc, o, e = run('findstr /C:"ctx.context" C:\\Vigil\\plugins\\gate.py | find /C /V ""')
print("gate.py marker lines:", o or "0")


def server_pids():
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
            if "server.py" in cl:
                pids.append(cur["ProcessId"])
            cur = {}
    if cur.get("ProcessId"):
        cl = cur.get("CommandLine", "")
        if "server.py" in cl:
            pids.append(cur["ProcessId"])
    return pids


# 4) kill server.py, restart via scheduled task (cmd-only rule)
before = server_pids()
print("server pids before:", before)
for p in before:
    rc, o, e = run("taskkill /F /PID %s" % p)
    print("kill %s: %s" % (p, (o or e)[:60]))
rc, o, e = run("schtasks /End /TN VigilBrain", timeout=30)
print("schtasks end:", rc, (o or e)[:60])
rc, o, e = run("schtasks /Run /TN VigilBrain", timeout=30)
print("schtasks run:", rc, (o or e)[:80])

# 5) wait for 8788 LISTENING
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
    print("FAIL: 8788 not listening within 120s - check VigilBrain task")
    cli.close()
    sys.exit(1)

cli.close()
print("CLOUD DEPLOY DONE - now deploy the bridge side")
