# -*- coding: utf-8 -*-
"""一键修复云端brain模型配置：硅基流动 -> 小米MiMo"""
import paramiko
import json
import tempfile
import os
import time
import urllib.request

KEY = os.environ.get("VIGIL_DEPLOY_KEY", "").strip()
if not KEY:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_KEY")
HOST = os.environ.get("VIGIL_DEPLOY_HOST", "").strip()
if not HOST:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_HOST")
TOKEN = os.environ.get("VIGIL_BRAIN_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("Set required environment variable VIGIL_BRAIN_TOKEN")
API_KEY = os.environ.get("VIGIL_MIMO_API_KEY", "").strip()
if not API_KEY:
    raise SystemExit("Set required environment variable VIGIL_MIMO_API_KEY")

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect(HOST, username="Administrator", timeout=25, key_filename=KEY,
            look_for_keys=False, allow_agent=False)
print("[OK] SSH连接成功")


def run(cmd, timeout=60):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 1) 备份
print("\n[1/4] 备份云端配置...")
rc, o, e = run("copy /Y C:\\linzhixia\\config\\config.json C:\\linzhixia\\config\\config.json.bak_mimo_fix")
print("  备份:", "成功" if rc == 0 else f"失败 {e[:100]}")

# 2) 读取并修改配置
print("\n[2/4] 修改云端模型配置为小米MiMo...")
rc, o, e = run("type C:\\linzhixia\\config\\config.json")
cfg = json.loads(o)

old_provider = cfg.get("provider")
old_base = cfg.get("api_base")
old_model = cfg.get("model")
print(f"  旧配置: provider={old_provider}, base={old_base}, model={old_model}")

cfg["api_base"] = "https://api.xiaomimimo.com/v1"
cfg["api_key"] = API_KEY
cfg["model"] = "mimo-v2.6-flash"
cfg["provider"] = "xiaomi"

if "api_keys" not in cfg or not isinstance(cfg["api_keys"], dict):
    cfg["api_keys"] = {}
cfg["api_keys"]["xiaomi"] = API_KEY

tmp = os.path.join(tempfile.gettempdir(), "zx_cloud_config.json")
with open(tmp, "w", encoding="utf-8") as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)

try:
    sftp = cli.open_sftp()
    try:
        sftp.put(tmp, "C:/linzhixia/config/config.json")
    finally:
        sftp.close()
finally:
    if os.path.exists(tmp):
        os.remove(tmp)
print(f"  新配置: provider=xiaomi, base=https://api.xiaomimimo.com/v1, model=mimo-v2.6-flash")

# 3) 重启云端brain
print("\n[3/4] 重启云端brain...")
rc, o, e = run("netstat -ano | findstr :8788 | findstr LISTENING")
pid = ""
if o:
    parts = o.strip().split()
    pid = parts[-1] if parts else ""
print(f"  当前brain PID: {pid or '未找到'}")

if pid:
    run(f"taskkill /F /PID {pid}")
    time.sleep(3)

rc, o, e = run("schtasks /run /tn VigilBrain")
print(f"  计划任务启动: {o[:100] if o else e[:100]}")
time.sleep(6)

rc, o, e = run("netstat -ano | findstr :8788 | findstr LISTENING")
print(f"  重启后端口: {'正常监听' if o else '未监听!'}")

# 4) 验证
print("\n[4/4] 验证云端模型配置...")
try:
    req = urllib.request.Request(
        f"http://{HOST}:8788/api/models",
        headers={"Authorization": f"Bearer {TOKEN}"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
        cur = data.get("current", {})
        print(f"  当前provider: {cur.get('provider')}")
        print(f"  当前api_base: {cur.get('api_base')}")
        print(f"  当前model: {cur.get('model')}")
        print(f"  api_key已设置: {cur.get('api_key_set')}")
except Exception as ex:
    print(f"  验证失败: {ex}")

cli.close()
print("\n[完成] 云端配置已修复，去微信发句话测试")
