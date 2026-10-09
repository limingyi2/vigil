# -*- coding: utf-8 -*-
"""Final check: SFTP-download bridge.log + launcher.log, read tails locally
(confirms brain reachable, wechat attached). Env: ZX_PW, ZX_BRIDGE_HOST -
real values never committed. ASCII only."""
import os
import sys
import tempfile

import paramiko

PW = os.environ.get("ZX_PW")
HOST = os.environ.get("ZX_BRIDGE_HOST", "")
if not PW or not HOST:
    print("ZX_PW / ZX_BRIDGE_HOST not set")
    sys.exit(2)

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", password=PW, timeout=25,
            look_for_keys=False, allow_agent=False)
sftp = cli.open_sftp()
for remote in ("C:/zx/bridge.log", "C:/zx/launcher.log"):
    local = os.path.join(tempfile.gettempdir(),
                         "zx_" + os.path.basename(remote))
    try:
        sftp.get(remote, local)
        with open(local, encoding="utf-8", errors="replace") as f:
            text = f.read()
        print("== %s (total %d) tail ==" % (remote, len(text)))
        print(text[-1800:])
    except IOError as e:
        print("%s: %s" % (remote, e))
sftp.close()
cli.close()
print("LOG CHECK DONE")
