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

def chat(text, src="wechat"):
    req = urllib.request.Request(
        f"http://{HOST}:8788/api/chat",
        data=json.dumps({"text": text, "src": src}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data.get("reply", ""), data.get("mode", "")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode('utf-8')[:200]}", "error"
    except Exception as e:
        return f"错误: {type(e).__name__}: {str(e)[:100]}", "error"

print("=== 测试1: 简单数学题 ===")
reply, mode = chat("1+1等于几")
print(f"回复: {reply[:200]}")
print(f"模式: {mode}")

print("\n=== 测试2: 简单问候 ===")
reply, mode = chat("在吗")
print(f"回复: {reply[:200]}")
print(f"模式: {mode}")

print("\n=== 测试3: 不带src（默认渠道）===")
reply, mode = chat("你好", src="app")
print(f"回复: {reply[:200]}")
print(f"模式: {mode}")
