# -*- coding: utf-8 -*-
import os
import paramiko
import json

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

_, o, _ = cli.exec_command("type C:\\linzhixia\\config\\config.json")
cfg = json.loads(o.read().decode("utf-8"))

print("=== 云端config.json 模型相关字段 ===")
print("provider:", cfg.get("provider"))
print("api_base:", cfg.get("api_base"))
print("model:", cfg.get("model"))
print("api_key_set:", bool(cfg.get("api_key")))
api_keys = cfg.get("api_keys")
print("api_key_names:", sorted(api_keys) if isinstance(api_keys, dict) else [])

# 检查model_hub怎么读model的
print("\n=== 检查model_hub.py的读取逻辑 ===")
_, o, _ = cli.exec_command("type C:\\linzhixia\\model_hub.py")
content = o.read().decode("utf-8")
for i, line in enumerate(content.splitlines()):
    if "model" in line.lower() and ("get" in line or "cfg" in line or "config" in line):
        print(f"{i+1}: {line.strip()[:120]}")

cli.close()
