# -*- coding: utf-8 -*-
"""Deploy: gate chatty-knob hard gate (talk-level 1 = mention-only).

User request: bring back the talk-frequency slider, lowest setting (1)
must mean "only reply when @'ed". gate.py now reads chatty.level(src):
  1  -> veto everything not @'ed / not called by name (no model call)
  >=2 -> model judge as before + rescue PASS with probability (lvl-1)/9*0.45

Also: set cloud chatty.wechat_group = 1 (user wants lowest NOW),
show gate config (enabled/sources/bot_nicknames) as evidence,
restart VigilBrain, verify config twice (models/apply-style overwrite guard).
"""
import json
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"

FIX = """# -*- coding: utf-8 -*-
import json, io
P = r"C:\\Vigil\\config\\config.json"
cfg = json.load(io.open(P, encoding="utf-8"))
ch = cfg.get("chatty")
if not isinstance(ch, dict):
    ch = {}
ch["wechat_group"] = 1
cfg["chatty"] = ch
g = cfg.get("gate")
if not isinstance(g, dict):
    g = {}
if not g.get("enabled", False):
    g["enabled"] = True
cfg["gate"] = g
with io.open(P, "w", encoding="utf-8", newline="\\n") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
print("chatty:", json.dumps(cfg["chatty"], ensure_ascii=True, sort_keys=True))
print("gate:", json.dumps({k: g.get(k) for k in
      ("enabled", "sources", "bot_nicknames", "nicknames", "model")},
      ensure_ascii=True))
"""

VERIFY = """# -*- coding: utf-8 -*-
import json, io
cfg = json.load(io.open(r"C:\\Vigil\\config\\config.json", encoding="utf-8"))
print(json.dumps({"chatty": cfg.get("chatty"),
                  "gate_enabled": (cfg.get("gate") or {}).get("enabled"),
                  "gate_sources": (cfg.get("gate") or {}).get("sources"),
                  "by_src": cfg.get("persona_by_src")},
                 ensure_ascii=True, sort_keys=True))
"""


def run(ssh, cmd, timeout=120):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()

    # 0. evidence: current gate.py marker + config gate section
    with sftp.open("C:/Vigil/plugins/gate.py", "r") as f:
        old = f.read().decode("utf-8", "replace")
    print("cloud gate BEFORE: has_chatty_knob=%s len=%d"
          % ("_chatty_level" in old, len(old)))
    with sftp.open("C:/Vigil/config/config.json", "r") as f:
        cfg = json.loads(f.read().decode("utf-8", "replace"))
    print("config BEFORE: chatty=%s gate=%s"
          % (json.dumps(cfg.get("chatty"), ensure_ascii=True),
             json.dumps({k: (cfg.get("gate") or {}).get(k)
                         for k in ("enabled", "sources", "bot_nicknames")},
                        ensure_ascii=True)))

    # 1. backup + upload new gate.py
    rc, o, e = run(ssh, "copy /Y C:\\Vigil\\plugins\\gate.py "
                        "C:\\Vigil\\plugins\\gate.py.bak_chatty_20261009")
    print("backup:", rc, (o or e)[:60])
    sftp.put(r"F:\Vigil\brain\plugins\gate.py",
             "C:/Vigil/plugins/gate.py")
    rc, o, e = run(ssh, "%s -m py_compile C:\\Vigil\\plugins\\gate.py" % PYC)
    print("gate py_compile:", rc, (o or e)[:120])
    if rc != 0:
        raise SystemExit("gate.py compile failed, abort")

    # 2. config: chatty.wechat_group=1, gate.enabled on
    with sftp.open("C:/Vigil/_fixg.py", "w") as f:
        f.write(FIX)
    with sftp.open("C:/Vigil/_verg.py", "w") as f:
        f.write(VERIFY)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_fixg.py" % PYC, timeout=60)
    print("fix:", (o or e)[:400])
    if rc != 0:
        raise SystemExit("fix failed, abort")

    # 3. restart + wait 8788
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
        raise SystemExit("FATAL: 8788 not up")
    time.sleep(45)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_verg.py" % PYC)
    print("verify1:", (o or e)[:400])
    time.sleep(55)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_verg.py" % PYC)
    print("verify2:", (o or e)[:400])
    ssh.exec_command("del /q C:\\Vigil\\_fixg.py C:\\Vigil\\_verg.py")
    sftp.close()
    ssh.close()
    print("CLOUD DONE")


if __name__ == "__main__":
    main()
