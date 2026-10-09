# -*- coding: utf-8 -*-
"""Deploy part 2: model_hub.py (adopt_current_key infers provider from base)."""
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

    rc, o, e = run(ssh, r"copy /Y C:\Vigil\model_hub.py "
                        r"C:\Vigil\model_hub.py.bak_models_20261009")
    print("backup model_hub:", rc, (o or e)[:50])
    sftp.put(r"F:\Vigil\brain\model_hub.py", "C:/Vigil/model_hub.py")
    rc, o, e = run(ssh, PYC + r" -m py_compile C:\Vigil\model_hub.py")
    print("py_compile:", rc, (o or e)[:120])
    if rc != 0:
        raise SystemExit("compile failed")

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
    rc, o, e = run(ssh, r'findstr /C:"provider" C:\Vigil\model_hub.py '
                        r'| find /C "cfg.set(\"provider\", prov)"')
    print("provider writeback present:", (o or e)[:20])
    time.sleep(55)
    rc, o, e = run(ssh, "netstat -ano | findstr :8788 | findstr LISTENING")
    print("still up:", rc == 0 and bool(o.strip()))
    sftp.close()
    ssh.close()
    print("CLOUD DONE")


if __name__ == "__main__":
    main()
