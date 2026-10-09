# -*- coding: utf-8 -*-
"""Deploy #3: bind cat persona everywhere (user intent), push local cat
persona file to cloud, label it, sync_client update, restart, verify.

User clarified: they WANT the "default" persona (laowu cat) for private
chat AND the group. Earlier linzhixia binding was my wrong guess.
"""
import json
import os
import sys
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"
LOCAL_CAT = r"F:\Vigil\dist\vigil-brain\config\personas\default.json"
LOCAL_CFG = r"F:\Vigil\dist\vigil-brain\config\config.json"

FIX = """# -*- coding: utf-8 -*-
import json, io
P = r"C:\\Vigil\\config\\config.json"
cfg = json.load(io.open(P, encoding="utf-8"))
cfg["persona_by_src"] = {"wechat": "default",
                         "wechat_group": "default",
                         "pc": "pc_assistant"}
with io.open(P, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
PP = r"C:\\Vigil\\config\\personas\\default.json"
cat = json.load(io.open(PP, encoding="utf-8"))
cat["label"] = "\\u8001\\u5434\\u732b"
with io.open(PP, "w", encoding="utf-8") as f:
    json.dump(cat, f, ensure_ascii=False, indent=2)
print("by_src:", json.dumps(cfg["persona_by_src"], ensure_ascii=True))
print("label:", cat["label"].encode("unicode_escape").decode())
print("notes_len:", len(str(cat.get("notes") or "")))
"""

VERIFY = """# -*- coding: utf-8 -*-
import json, io
cfg = json.load(io.open(r"C:\\Vigil\\config\\config.json", encoding="utf-8"))
cat = json.load(io.open(r"C:\\Vigil\\config\\personas\\default.json",
                        encoding="utf-8"))
print(json.dumps({"by_src": cfg.get("persona_by_src"),
                  "plan": (cfg.get("plan_and_execute") or {}).get("enabled"),
                  "cat_name": cat.get("name"),
                  "cat_label": cat.get("label"),
                  "cat_notes_len": len(str(cat.get("notes") or ""))},
                 ensure_ascii=True))
"""


def run(ssh, cmd, timeout=120):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def cloud():
    print("==== CLOUD: bind cat everywhere ====")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()

    # 0. look at what the cloud cat currently is (evidence before overwrite)
    with sftp.open("C:/Vigil/config/personas/default.json", "r") as f:
        old = json.loads(f.read().decode("utf-8", "replace"))
    print("cloud cat BEFORE: name=%r notes_len=%s label=%r"
          % (old.get("name"), len(str(old.get("notes") or "")),
             old.get("label")))

    # 1. backup cloud cat, push local (user's App version) up
    rc, o, e = run(ssh, "copy /Y C:\\Vigil\\config\\personas\\default.json "
                        "C:\\Vigil\\config\\personas\\default.json.bak_catpush_20261009")
    print("backup:", rc, (o or e)[:60])
    sftp.put(LOCAL_CAT, "C:/Vigil/config/personas/default.json")
    print("cat persona uploaded (user App version)")

    # 2. sync_client.py (persona_by_src now in behavior sync)
    sftp.put(r"F:\Vigil\brain\sync_client.py", "C:/Vigil/sync_client.py")
    rc, o, e = run(ssh, "%s -m py_compile C:\\Vigil\\sync_client.py" % PYC)
    print("sync_client py_compile:", rc, (o or e)[:120])
    if rc != 0:
        sys.exit(1)

    # 3. config binding + label
    with sftp.open("C:/Vigil/_fix3.py", "w") as f:
        f.write(FIX)
    with sftp.open("C:/Vigil/_ver3.py", "w") as f:
        f.write(VERIFY)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_fix3.py" % PYC, timeout=60)
    print("fix3:", (o or e)[:400])
    if rc != 0:
        sys.exit(1)

    # 4. restart + verify x2
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
    rc, o, e = run(ssh, "%s C:\\Vigil\\_ver3.py" % PYC)
    print("verify1:", (o or e)[:400])
    time.sleep(50)
    rc, o, e = run(ssh, "%s C:\\Vigil\\_ver3.py" % PYC)
    print("verify2:", (o or e)[:400])
    ssh.exec_command("del /q C:\\Vigil\\_fix3.py C:\\Vigil\\_ver3.py")
    sftp.close()
    ssh.close()
    print("CLOUD DONE")


def local_mirror():
    print("==== LOCAL sidecar mirror ====")
    cfg = json.load(open(LOCAL_CFG, encoding="utf-8"))
    cfg["persona_by_src"] = {"wechat": "default",
                             "wechat_group": "default",
                             "pc": "pc_assistant"}
    tmp = LOCAL_CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOCAL_CFG)
    cat = json.load(open(LOCAL_CAT, encoding="utf-8"))
    cat["label"] = "老吴猫"
    tmp = LOCAL_CAT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(cat, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOCAL_CAT)
    print("local by_src:", json.dumps(cfg["persona_by_src"], ensure_ascii=True))
    print("local cat label set")
    print("LOCAL DONE")


if __name__ == "__main__":
    cloud()
    local_mirror()
    print("ALL DONE")
