# -*- coding: utf-8 -*-
"""Upload updated wxauto_bridge.py to the bridge box (backward compatible:
sender_id is an optional kwarg). Password comes from ZX_PW env var only,
never written anywhere. ASCII only."""
import os
from pathlib import Path
import subprocess
import sys

import paramiko

PW = os.environ.get("ZX_PW")
HOST = os.environ.get("ZX_BRIDGE_HOST", "")
LOCAL = Path(os.environ.get("ZX_BRIDGE_SOURCE", "")).resolve()
EXPECTED_REVISION = os.environ.get("ZX_BRIDGE_SOURCE_REVISION", "")
if not LOCAL.is_file() or not EXPECTED_REVISION:
    print("Set ZX_BRIDGE_SOURCE and ZX_BRIDGE_SOURCE_REVISION from a reviewed upstream checkout")
    sys.exit(2)
try:
    revision = subprocess.check_output(
        ["git", "-C", str(LOCAL.parent), "rev-parse", "HEAD"], stderr=subprocess.DEVNULL
    ).decode().strip()
    remote = subprocess.check_output(
        ["git", "-C", str(LOCAL.parent), "remote", "get-url", "origin"], stderr=subprocess.DEVNULL
    ).decode().strip().lower().removesuffix(".git")
    source_root = Path(subprocess.check_output(
        ["git", "-C", str(LOCAL.parent), "rev-parse", "--show-toplevel"], stderr=subprocess.DEVNULL
    ).decode().strip()).resolve()
except (OSError, subprocess.CalledProcessError):
    print("Bridge source must be a checked-out upstream Git repository")
    sys.exit(2)
if (not (remote.endswith("/limingyi2/zhixia-wechat-bridge")
         or remote.endswith(":limingyi2/zhixia-wechat-bridge"))
        or revision != EXPECTED_REVISION or LOCAL != source_root / "wxauto_bridge.py"):
    print("Bridge source repository or revision does not match the reviewed upstream pin")
    sys.exit(2)
if not PW or not HOST:
    print("ZX_PW / ZX_BRIDGE_HOST not set")
    sys.exit(2)
REMOTE = "C:/zx/wxauto_bridge.py"
BAK = "C:/zx/wxauto_bridge.py.bak_m4_20261009"

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", password=PW, timeout=20,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=60):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    o = out.read().decode("utf-8", "replace").strip()
    e = err.read().decode("utf-8", "replace").strip()
    return rc, o, e


# 1) sanity: what is running right now (before any change)
rc, o, e = run('wmic process where "name=\'python.exe\'" get processid,'
               'commandline /format:list | findstr /I "wxauto_bridge launcher"')
print("== running before ==")
print(o or "(none)")

# 2) backup
rc, o, e = run('copy /Y C:\\zx\\wxauto_bridge.py %s' % BAK.replace("/", "\\"))
print("backup:", rc, o[:120], e[:120])
if rc != 0:
    cli.close()
    sys.exit(1)

# 3) upload
sftp = cli.open_sftp()
sftp.put(LOCAL, REMOTE)
sftp.close()
print("uploaded:", LOCAL, "->", REMOTE)

# 4) py_compile on the box (use whatever python lives there)
rc, o, e = run("C:\\zx\\.venv\\Scripts\\python.exe -m py_compile C:\\zx\\wxauto_bridge.py")
if rc != 0:
    rc2, o2, e2 = run("python -m py_compile C:\\zx\\wxauto_bridge.py")
    print("py_compile fallback:", rc2, o2[:200], e2[:200])
    if rc2 != 0:
        cli.close()
        sys.exit(1)
else:
    print("py_compile: OK (venv python)")

# 5) confirm the new kwarg is on the box
rc, o, e = run('findstr /C:"sender_id" C:\\zx\\wxauto_bridge.py | find /C /V ""')
print("sender_id lines on box:", o or "0")

cli.close()
print("UPLOAD PHASE OK - restart separately")
