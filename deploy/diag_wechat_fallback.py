# -*- coding: utf-8 -*-
"""诊断云端 brain 为什么一直返回兜底消息"""
import os
import paramiko
import sys

KEY = os.environ.get("VIGIL_DEPLOY_KEY", "").strip()
if not KEY:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_KEY")
HOST = os.environ.get("VIGIL_DEPLOY_HOST", "").strip()
if not HOST:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_HOST")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    cli.connect(HOST, username="Administrator", timeout=25, key_filename=KEY,
                look_for_keys=False, allow_agent=False)
    print("[OK] SSH 连接成功")
except Exception as e:
    print("[FAIL] SSH 连接失败:", e)
    sys.exit(1)


def run(cmd, timeout=60):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 1) 找 python 路径
rc, o, e = run('wmic process where "name=\'python.exe\'" get commandline /format:list')
PY = "python"
for line in o.splitlines():
    line = line.strip()
    if line.startswith("CommandLine=") and "server.py" in line:
        exe = line.split("CommandLine=", 1)[1].strip().strip('"')
        if exe.lower().endswith(".exe"):
            PY = exe
        else:
            PY = exe.split("server.py")[0].strip().strip('"')
        break
print("python:", PY)

# 2) 看 brain 进程是否在跑
rc, o, e = run('tasklist /FI "IMAGENAME eq python.exe" /FO TABLE')
print("\n=== python 进程 ===")
print(o[:500])

# 3) 看 8788 端口是否在监听
rc, o, e = run('netstat -ano | findstr :8788 | findstr LISTENING')
print("\n=== 8788 端口 ===")
print(o[:300] if o else "未监听！")

# 4) 看云端 config.json 的模型配置
print("\n=== 云端 config.json 模型配置 ===")
rc, o, e = run('type C:\\linzhixia\\config\\config.json')
if rc == 0:
    # 只提取关键行
    for line in o.splitlines():
        if any(k in line for k in ['api_base', 'api_key', '"model"', 'provider', 'mimo', 'deepseek', 'siliconflow']):
            if 'api_key' in line.lower():
                print("api_key: configured")
            else:
                print(line.strip()[:200])
else:
    print("读取失败:", e[:200])

# 5) 找日志文件
print("\n=== 日志文件 ===")
rc, o, e = run('dir /B /O-D C:\\linzhixia\\logs\\*.log 2>nul')
if o:
    logs = o.splitlines()[:5]
    for log in logs:
        print(" -", log)
    # 读最新日志的最后 100 行
    if logs:
        latest = logs[0]
        print(f"\n=== 最新日志 {latest} 最后 80 行 ===")
        rc, o, e = run(f'powershell -Command "Get-Content \'C:\\linzhixia\\logs\\{latest}\' -Tail 80"')
        print(o[:3000])
else:
    print("没有 logs 目录，找其他日志位置...")
    rc, o, e = run('dir /B /S C:\\linzhixia\\*.log 2>nul')
    print(o[:500])

cli.close()
print("\n[完成]")
