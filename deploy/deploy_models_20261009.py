# -*- coding: utf-8 -*-
"""Deploy: /api/models provider-aware key fallback.

1. backup + upload brain/server.py (provider query param: preview pull uses
   the *target provider's own stored key* before falling back to current key)
2. restart VigilBrain, verify 8788 twice (overwrite guard)
Client-side half ships as code5 (model page rewrite) via push update.
"""
import time

import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
PYC = r"C:\Vigil\.venv\Scripts\python.exe"


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

    rc, o, e = run(ssh, "copy /Y C:\\Vigil\\server.py "
                        "C:\\Vigil\\server.py.bak_models_20261009")
    print("backup server.py:", rc, (o or e)[:60])
    sftp.put(r"F:\Vigil\brain\server.py", "C:/Vigil/server.py")
    rc, o, e = run(ssh, "%s -m py_compile C:\\Vigil\\server.py" % PYC)
    print("server py_compile:", rc, (o or e)[:160])
    if rc != 0:
        raise SystemExit("server.py compile failed, abort")

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
    rc, o, e = run(ssh, "findstr /C:\"provider\" C:\\Vigil\\server.py | find /C \"p_prov\"")
    print("p_prov occurrences on cloud:", (o or e)[:40])
    time.sleep(55)
    rc, o, e = run(ssh, "netstat -ano | findstr :8788 | findstr LISTENING")
    print("still up:", rc == 0 and bool(o.strip()))
    sftp.close()
    ssh.close()
    print("CLOUD DONE")


if __name__ == "__main__":
    main()
