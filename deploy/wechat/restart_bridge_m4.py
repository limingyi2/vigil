# -*- coding: utf-8 -*-
"""Restart phase: kill wxauto_bridge.py, launcher revives it with new code
within ~20s (proven 21s rebirth). Then verify: process back + hb.stamp fresh.
Password from ZX_PW env only. ASCII only."""
import os
import sys
import time

import paramiko

PW = os.environ.get("ZX_PW")
HOST = os.environ.get("ZX_BRIDGE_HOST", "")
if not PW or not HOST:
    print("ZX_PW / ZX_BRIDGE_HOST not set")
    sys.exit(2)
cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", password=PW, timeout=20,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=60):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def bridge_pid():
    # verified pattern (deploy script): list all python command lines,
    # parse locally - wmic where-clauses get mangled by cmd quoting over ssh
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


before = bridge_pid()
print("bridge pids before:", before)

for p in before:
    rc, o, e = run("taskkill /F /PID %s" % p)
    print("kill %s:" % p, o[:80] or e[:80])

# launcher rebirth loop picks it up
print("waiting for rebirth ...")
deadline = time.time() + 60
after = []
while time.time() < deadline:
    time.sleep(5)
    after = bridge_pid()
    if after and all(p not in before for p in after):
        print("reborn pids:", after, "after ~%ds" % (60 - (deadline - time.time())))
        break
    after = []

if not after:
    print("WARN: no new bridge process within 60s")
    cli.close()
    sys.exit(1)

# hb.stamp freshness (coroner heartbeat)
rc, o, e = run("dir C:\\zx\\hb.stamp | findstr hb.stamp")
print("hb.stamp:", o[:120])

# log tails: use bridge_log_tail.py (SFTP) - powershell over ssh is unreliable
cli.close()
print("RESTART PHASE DONE - run bridge_log_tail.py for log tails")
