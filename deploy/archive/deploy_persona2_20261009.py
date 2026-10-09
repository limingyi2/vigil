# -*- coding: utf-8 -*-
"""Cloud fix #2 + bridge deploy completion.

Cloud:
  - persona_by_src -> {wechat: linzhixia, wechat_group: linzhixia,
                       pc: pc_assistant}   (user expects zhi Xia everywhere)
  - label the cat persona default.json so the App dropdown stops trapping
  - restart VigilBrain, verify config twice
Bridge:
  - finish phase B: py_compile (fallback python path), markers, kill,
    rebirth, hb.stamp
"""
import os
import sys
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"

FIX = """# -*- coding: utf-8 -*-
import json, io, shutil
P = r"C:\\Vigil\\config\\config.json"
BAK = r"C:\\Vigil\\config\\config.json.bak_persona2_20261009"
shutil.copyfile(P, BAK)
cfg = json.load(io.open(P, encoding="utf-8"))
cfg["persona_by_src"] = {"wechat": "linzhixia",
                         "wechat_group": "linzhixia",
                         "pc": "pc_assistant"}
with io.open(P, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
PP = r"C:\\Vigil\\config\\personas\\default.json"
shutil.copyfile(PP, PP + ".bak_catlabel_20261009")
cat = json.load(io.open(PP, encoding="utf-8"))
cat["label"] = "test-cat"
cat.setdefault("desc", "")
cat["desc"] = str(cat.get("desc") or "")
with io.open(PP, "w", encoding="utf-8") as f:
    json.dump(cat, f, ensure_ascii=False, indent=2)
print("persona_by_src:", json.dumps(cfg["persona_by_src"], ensure_ascii=True))
print("cat label set")
"""

VERIFY = """# -*- coding: utf-8 -*-
import json, io
cfg = json.load(io.open(r"C:\\Vigil\\config\\config.json", encoding="utf-8"))
cat = json.load(io.open(r"C:\\Vigil\\config\\personas\\default.json",
                        encoding="utf-8"))
print(json.dumps({"by_src": cfg.get("persona_by_src"),
                  "plan": (cfg.get("plan_and_execute") or {}).get("enabled"),
                  "cat_label": cat.get("label")}, ensure_ascii=True))
"""


def run(ssh, cmd, timeout=120):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def cloud():
    print("==== CLOUD FIX #2 ====")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()
    with sftp.open("C:/Vigil/_fix2.py", "w") as f:
        f.write(FIX)
    with sftp.open("C:/Vigil/_ver2.py", "w") as f:
        f.write(VERIFY)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_fix2.py" % PYC, timeout=60)
    print("fix2 rc=%s" % rc, (o or e)[:500])
    if rc != 0:
        sys.exit(1)
    run(ssh, "schtasks /End /TN VigilBrain")
    time.sleep(3)
    run(ssh, "schtasks /Run /TN VigilBrain")
    for i in range(16):
        time.sleep(5)
        rc, o, e = run(ssh, "netstat -ano | findstr :8788 | findstr LISTENING")
        if rc == 0 and o.strip():
            print("8788 up ~%ds" % ((i + 1) * 5))
            break
    else:
        print("FATAL: 8788 not up")
        sys.exit(1)
    time.sleep(45)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_ver2.py" % PYC)
    print("verify1:", (o or e)[:400])
    time.sleep(50)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_ver2.py" % PYC)
    print("verify2:", (o or e)[:400])
    ssh.exec_command("del /q C:\\Vigil\\_fix2.py C:\\Vigil\\_ver2.py")
    sftp.close()
    ssh.close()
    print("CLOUD FIX #2 DONE")


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


def bridge():
    print("==== BRIDGE FINISH ====")
    pw = os.environ.get("ZX_PW")
    if not pw:
        print("FATAL: ZX_PW not set")
        sys.exit(1)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect("203.0.113.10", username="Administrator", password=pw,
                timeout=20, look_for_keys=False, allow_agent=False)

    # py_compile: try venv, fallback system python
    rc, o, e = run(cli, "C:\\zx\\.venv\\Scripts\\python.exe -m py_compile "
                        "C:\\zx\\wxauto_bridge.py")
    if rc != 0:
        print("venv python failed (%s), fallback" % (o or e)[:80])
        rc, o, e = run(cli, '"C:\\Program Files\\Python312\\python.exe" '
                            '-m py_compile C:\\zx\\wxauto_bridge.py')
        if rc != 0:
            rc, o, e = run(cli, 'py -3 -m py_compile C:\\zx\\wxauto_bridge.py')
    print("py_compile:", rc, (o or e)[:120])
    if rc != 0:
        cli.close()
        sys.exit(1)

    for marker, want in (("TIME_LABEL_RE", 2), ("_last_self_img_ts", 4)):
        rc, o, e = run(cli, 'findstr /C:"%s" C:\\zx\\wxauto_bridge.py '
                            '| find /C /V ""' % marker)
        n = int(o or "0")
        print("marker %s=%d" % (marker, n))
        if n < want:
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
            print("reborn:", after, "~%ds" % int(75 - (deadline - time.time())))
            break
        after = []
    if not after:
        print("WARN: no rebirth")
        cli.close()
        sys.exit(1)
    rc, o, e = run(cli, "dir C:\\zx\\hb.stamp | findstr hb.stamp")
    print("hb.stamp:", o[:100])
    cli.close()
    print("BRIDGE DONE")


if __name__ == "__main__":
    cloud()
    bridge()
    print("ALL DONE")
