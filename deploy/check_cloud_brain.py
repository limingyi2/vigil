# -*- coding: utf-8 -*-
"""检查云端brain启动状态和日志"""
import os
import paramiko
import time

KEY = os.environ.get("VIGIL_DEPLOY_KEY", "").strip()
if not KEY:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_KEY")
HOST = os.environ.get("VIGIL_DEPLOY_HOST", "").strip()
if not HOST:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_HOST")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", timeout=25, key_filename=KEY,
            look_for_keys=False, allow_agent=False)


def run(cmd, timeout=30):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 等久一点再检查
print("等待10秒让brain启动...")
time.sleep(10)

# 检查端口
print("\n=== 8788端口状态 ===")
rc, o, e = run("netstat -ano | findstr :8788 | findstr LISTENING")
print(o if o else "未监听")

# 检查python进程
print("\n=== python进程 ===")
rc, o, e = run('wmic process where "name=\'python.exe\'" get processid,commandline /format:list')
for line in o.splitlines():
    if "server.py" in line or "vigil" in line.lower():
        print(line[:200])

# 检查计划任务状态
print("\n=== 计划任务状态 ===")
rc, o, e = run("schtasks /query /tn VigilBrain /v /fo list")
for line in o.splitlines():
    if any(k in line for k in ["状态", "上次运行", "下次运行", "结果"]):
        print(line.strip())

# 看最新日志
print("\n=== 最新日志（vigil_agent.log最后30行）===")
rc, o, e = run('powershell -Command "Get-Content \'C:\\linzhixia\\logs\\vigil_agent.log\' -Tail 30"')
print(o[:2000] if o else "无日志")

# 直接手动启动试试
print("\n=== 手动启动brain ===")
rc, o, e = run("cd C:\\linzhixia && start /B .venv\\Scripts\\python.exe server.py")
time.sleep(8)
rc, o, e = run("netstat -ano | findstr :8788 | findstr LISTENING")
print(f"手动启动后端口: {'正常' if o else '还是没监听'}")

cli.close()
