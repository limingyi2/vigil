# -*- coding: utf-8 -*-
import os
import urllib.request
import json

TOKEN = os.environ.get("VIGIL_BRAIN_TOKEN", "").strip()
if not TOKEN:
    raise SystemExit("Set required environment variable VIGIL_BRAIN_TOKEN")
HOST = os.environ.get("VIGIL_DEPLOY_HOST", "").strip()
if not HOST:
    raise SystemExit("Set required environment variable VIGIL_DEPLOY_HOST")

# 1. 查当前模型配置
print("=== 云端模型配置 ===")
req = urllib.request.Request(
    f"http://{HOST}:8788/api/models",
    headers={"Authorization": f"Bearer {TOKEN}"}
)
with urllib.request.urlopen(req, timeout=10) as r:
    data = json.loads(r.read().decode("utf-8"))
    cur = data.get("current", {})
    print("provider:", cur.get("provider"))
    print("api_base:", cur.get("api_base"))
    print("model:", cur.get("model"))
    print("api_key_set:", cur.get("api_key_set"))

# 2. 实际发一条消息测试
print("\n=== 实际聊天测试 ===")
req = urllib.request.Request(
    f"http://{HOST}:8788/api/chat",
    data=json.dumps({"text": "你好，说一句话测试", "src": "wechat"}).encode("utf-8"),
    headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
)
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.loads(r.read().decode("utf-8"))
    print("回复:", data.get("reply", "")[:200])
    print("模式:", data.get("mode", ""))
