# -*- coding: utf-8 -*-
"""Deploy 2026-10-09 evening fixes.

Phase A (cloud, key auth):
  - backup config, set persona_by_src.wechat=linzhixia,
    plan_and_execute.enabled=false (latency + persona tone)
  - restart VigilBrain, wait 8788, re-read config twice (+45s/+100s)
    to catch any /api/models/apply style overwrite.
Phase B (bridge, ZX_PW env):
  - backup -> upload wxauto_bridge.py (self-image guard + time label
    filter) -> py_compile -> marker check -> kill -> rebirth -> hb.stamp
"""
import os
import sys
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"

CFGFIX = """# -*- coding: utf-8 -*-
import json, io, shutil
P = r"C:\\Vigil\\config\\config.json"
BAK = r"C:\\Vigil\\config\\config.json.bak_personafix_20261009"
shutil.copyfile(P, BAK)
cfg = json.load(io.open(P, encoding="utf-8"))
by = cfg.get("persona_by_src") or {}
print("before persona_by_src:", json.dumps(by, ensure_ascii=True))
by["wechat"] = "linzhixia"
cfg["persona_by_src"] = by
pa = cfg.get("plan_and_execute") or {}
print("plan_and_execute.enabled before:", pa.get("enabled"))
pa["enabled"] = False
cfg["plan_and_execute"] = pa
with io.open(P, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
cfg2 = json.load(io.open(P, encoding="utf-8"))
print("after persona_by_src:",
      json.dumps(cfg2.get("persona_by_src"), ensure_ascii=True))
print("after plan enabled:",
      (cfg2.get("plan_and_execute") or {}).get("enabled"))
"""

VERIFY = """# -*- coding: utf-8 -*-
import json, io
cfg = json.load(io.open(r"C:\\Vigil\\config\\config.json", encoding="utf-8"))
print(json.dumps({
    "persona_by_src": cfg.get("persona_by_src"),
    "plan_enabled": (cfg.get("plan_and_execute") or {}).get("enabled"),
}, ensure_ascii=True))
"""


def run(ssh, cmd, timeout=120):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def phase_cloud():
    print("==== PHASE A: CLOUD ====")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()
    with sftp.open("C:/Vigil/_cfgfix.py", "w") as f:
        f.write(CFGFIX)
    with sftp.open("C:/Vigil/_cfgver.py", "w") as f:
        f.write(VERIFY)

    rc, o, e = run(ssh, "%s C:\\Vigil\\_cfgfix.py" % PYC, timeout=60)
    print("cfgfix rc=%s" % rc)
    print((o or e)[:800])
    if rc != 0:
        sftp.close()
        ssh.close()
        sys.exit(1)

    # restart brain
    rc, o, e = run(ssh, "schtasks /End /TN VigilBrain", timeout=60)
    print("schtasks end:", rc, (o or e)[:80])
    time.sleep(3)
    rc, o, e = run(ssh, "schtasks /Run /TN VigilBrain", timeout=60)
    print("schtasks run:", rc, (o or e)[:80])

    up = False
    for i in range(16):
        time.sleep(5)
        rc, o, e = run(
            ssh, "netstat -ano | findstr :8788 | findstr LISTENING",
            timeout=30)
        if rc == 0 and o.strip():
            print("8788 LISTENING after ~%ds" % ((i + 1) * 5))
            up = True
            break
    if not up:
        print("FATAL: 8788 not listening in 80s")
        sftp.close()
        ssh.close()
        sys.exit(1)

    for wait_s in (45, 55):
        time.sleep(wait_s)
        rc, o, e = run(ssh, "%s C:\\Vigil\\_cfgver.py" % PYC, timeout=60)
        print("verify(+):", (o or e)[:400])

    ssh.exec_command("del /q C:\\Vigil\\_cfgfix.py C:\\Vigil\\_cfgver.py")
    sftp.close()
    ssh.close()
    print("PHASE A DONE")


def bridge_pids(cli):
    rc, o, e = run(cli, 'wmic process where "name=\'python.exe\'" get '
                       'processid,commandline /format:list', timeout=90)
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


def phase_bridge():
    print("==== PHASE B: BRIDGE ====")
    pw = os.environ.get("ZX_PW")
    if not pw:
        print("FATAL: ZX_PW not set")
        sys.exit(1)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect("203.0.113.10", username="Administrator", password=pw,
                timeout=20, look_for_keys=False, allow_agent=False)

    rc, o, e = run(cli, "copy /Y C:\\zx\\wxauto_bridge.py "
                        "C:\\zx\\wxauto_bridge.py.bak_selfimg_20261009")
    print("backup:", rc, (o or e)[:80])
    if rc != 0:
        cli.close()
        sys.exit(1)

    sftp = cli.open_sftp()
    sftp.put(r"F:\Vigil\wechat_bridge\wxauto_bridge.py", "C:/zx/wxauto_bridge.py")
    sftp.close()
    print("uploaded")

    rc, o, e = run(cli, "C:\\zx\\.venv\\Scripts\\python.exe -m py_compile "
                        "C:\\zx\\wxauto_bridge.py")
    print("py_compile:", rc, (o or e)[:120])
    if rc != 0:
        cli.close()
        sys.exit(1)

    rc, o, e = run(cli, 'findstr /C:"TIME_LABEL_RE" C:\\zx\\wxauto_bridge.py '
                        '| find /C /V ""')
    n1 = int(o or "0")
    rc, o, e = run(cli, 'findstr /C:"_last_self_img_ts" C:\\zx\\wxauto_bridge.py '
                        '| find /C /V ""')
    n2 = int(o or "0")
    print("markers: TIME_LABEL_RE=%d _last_self_img_ts=%d" % (n1, n2))
    if n1 < 2 or n2 < 4:
        print("FAIL: marker missing")
        cli.close()
        sys.exit(1)

    before = bridge_pids(cli)
    print("bridge pids before:", before)
    for p in before:
        rc, o, e = run(cli, "taskkill /F /PID %s" % p)
        print("kill %s: %s" % (p, (o or e)[:50]))
    deadline = time.time() + 75
    after = []
    while time.time() < deadline:
        time.sleep(5)
        after = bridge_pids(cli)
        if after and all(p not in before for p in after):
            print("reborn pids:", after, "after ~%ds"
                  % int(75 - (deadline - time.time())))
            break
        after = []
    if not after:
        print("WARN: no rebirth in 75s")
        cli.close()
        sys.exit(1)
    rc, o, e = run(cli, "dir C:\\zx\\hb.stamp | findstr hb.stamp")
    print("hb.stamp:", o[:100])
    cli.close()
    print("PHASE B DONE")


if __name__ == "__main__":
    phase_cloud()
    phase_bridge()
    print("ALL DEPLOY DONE")
