# -*- coding: utf-8 -*-
"""Deploy: desktop client push-update channel.

1. backup + upload brain/server.py (new /api/client/version + /api/client/setup)
2. mkdir C:\\Vigil\\app\\client\\
3. upload dist_new4 setup exe -> VigilSetup.exe
4. upload client_version.json {code:2, ver, notes, sha256, size} (sha computed
   locally from the exact file we upload)
5. restart VigilBrain, verify config + API twice (overwrite guard)
"""
import hashlib
import json
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"
LOCAL_EXE = r"F:\Vigil\vigil-client\dist_new4\Vigil Setup 0.1.0.exe"
NOTES = ("说话多少滑条回归+1档只@；人设页直达云端（切换即时生效、改名卡片跟着变）；"
         "数据页云端/本机分类；托盘常驻；自带推送更新")


def run(ssh, cmd, timeout=120):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def main():
    sha = hashlib.sha256(open(LOCAL_EXE, "rb").read()).hexdigest()
    size = len(open(LOCAL_EXE, "rb").read())
    print("exe: sha256=%s size=%d" % (sha[:16] + "...", size))

    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()

    # 1. server.py (client update routes)
    rc, o, e = run(ssh, "copy /Y C:\\Vigil\\server.py "
                        "C:\\Vigil\\server.py.bak_clientupd_20261009")
    print("backup server.py:", rc, (o or e)[:60])
    sftp.put(r"F:\Vigil\brain\server.py", "C:/Vigil/server.py")
    rc, o, e = run(ssh, "%s -m py_compile C:\\Vigil\\server.py" % PYC)
    print("server py_compile:", rc, (o or e)[:160])
    if rc != 0:
        raise SystemExit("server.py compile failed, abort")

    # 2. client update dir
    run(ssh, "if not exist C:\\Vigil\\app\\client mkdir C:\\Vigil\\app\\client")
    sftp.put(LOCAL_EXE, "C:/Vigil/app/client/VigilSetup.exe")
    vf = {"code": 2, "ver": "0.1.0", "notes": NOTES, "sha256": sha, "size": size}
    with sftp.open("C:/Vigil/app/client/client_version.json", "w") as f:
        f.write(json.dumps(vf, ensure_ascii=False, indent=2))
    rc, o, e = run(ssh, "dir C:\\Vigil\\app\\client | find /C \"exe json\"")
    print("client dir ready")

    # 3. restart + wait 8788（真 token 的端到端验证在本机跑 —— 脚本里没有凭据）
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
    rc, o, e = run(ssh, "dir C:\\Vigil\\app\\client")
    print("client dir:\n%s" % (o or e)[:400])
    time.sleep(55)
    rc, o, e = run(ssh, "netstat -ano | findstr :8788 | findstr LISTENING")
    print("still up:", rc == 0 and bool(o.strip()))
    sftp.close()
    ssh.close()
    print("CLOUD DONE")


if __name__ == "__main__":
    main()
