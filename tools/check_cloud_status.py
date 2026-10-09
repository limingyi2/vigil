# -*- coding: utf-8 -*-
"""检查云端 brain 服务状态和客户端版本配置。"""
import json
import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"

def run(ssh, cmd, timeout=30):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())

def main():
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)

    print("=== 1. 检查 brain 服务状态 ===")
    rc, out, err = run(ssh, "schtasks /Query /TN VigilBrain /FO LIST")
    print(out or err)

    print("\n=== 2. 检查 8788 端口 ===")
    rc, out, err = run(ssh, "netstat -ano | findstr :8788 | findstr LISTENING")
    print(out or "端口未监听" if rc != 0 else out)

    print("\n=== 3. client_version.json 内容 ===")
    rc, out, err = run(ssh, "type C:\\Vigil\\app\\client\\client_version.json")
    print(out or err)

    print("\n=== 4. 测试本地 API ===")
    rc, out, err = run(ssh, "curl -s http://127.0.0.1:8788/api/client/version", timeout=10)
    print(out or err)

    print("\n=== 5. brain 进程 ===")
    rc, out, err = run(ssh, "tasklist | findstr vigil")
    print(out or "没有 vigil 进程")

    print("\n=== 6. 最近的 brain 日志 ===")
    rc, out, err = run(ssh, "if exist C:\\Vigil\\logs\\brain.log (powershell -Command \"Get-Content C:\\Vigil\\logs\\brain.log -Tail 20\") else (echo 没有日志文件)")
    print(out or err)

    ssh.close()

if __name__ == "__main__":
    main()
