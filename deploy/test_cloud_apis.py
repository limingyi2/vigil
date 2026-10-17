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


def run(cmd, timeout=30):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


# 在云端服务器上直接测试小米API
print("=== 在云端服务器上测试小米MiMo API ===")
test_script = '''
import urllib.request, json, os
API_KEY = os.environ.get("VIGIL_MIMO_API_KEY", "").strip()
if not API_KEY: raise SystemExit("Set required environment variable VIGIL_MIMO_API_KEY on the target host")
req = urllib.request.Request(
    "https://api.xiaomimimo.com/v1/chat/completions",
    data=json.dumps({
        "model": "mimo-v2.6-flash",
        "messages": [{"role": "user", "content": "1+1等于几"}],
        "max_tokens": 20
    }).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + API_KEY
    }
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("状态码:", r.status)
        print("响应:", r.read().decode("utf-8")[:500])
except urllib.error.HTTPError as e:
    print("HTTP错误:", e.code)
    print("响应:", e.read().decode("utf-8")[:500])
except Exception as e:
    print("其他错误:", type(e).__name__, str(e)[:200])
'''

# 写到云端临时文件
import tempfile, os
tmp = os.path.join(tempfile.gettempdir(), "test_mimo.py")
with open(tmp, "w", encoding="utf-8") as f:
    f.write(test_script)

sftp = cli.open_sftp()
sftp.put(tmp, "C:/linzhixia/test_mimo.py")
sftp.close()

rc, o, e = run("C:\\linzhixia\\.venv\\Scripts\\python.exe C:\\linzhixia\\test_mimo.py")
print(o if o else e)

# 测试硅基流动API（确认网络是通的）
print("\n=== 在云端服务器上测试硅基流动API（对比）===")
test_script2 = '''
import urllib.request, json, os
API_KEY = os.environ.get("VIGIL_SILICONFLOW_API_KEY", "").strip()
if not API_KEY: raise SystemExit("Set required environment variable VIGIL_SILICONFLOW_API_KEY on the target host")
req = urllib.request.Request(
    "https://api.siliconflow.cn/v1/chat/completions",
    data=json.dumps({
        "model": "deepseek-ai/DeepSeek-V4-Flash",
        "messages": [{"role": "user", "content": "1+1等于几"}],
        "max_tokens": 20
    }).encode("utf-8"),
    headers={
        "Content-Type": "application/json",
        "Authorization": "Bearer " + API_KEY
    }
)
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print("状态码:", r.status)
        data = json.loads(r.read().decode("utf-8"))
        print("回复:", data["choices"][0]["message"]["content"][:100])
except urllib.error.HTTPError as e:
    print("HTTP错误:", e.code, e.read().decode("utf-8")[:300])
except Exception as e:
    print("其他错误:", type(e).__name__, str(e)[:200])
'''

tmp2 = os.path.join(tempfile.gettempdir(), "test_sf.py")
with open(tmp2, "w", encoding="utf-8") as f:
    f.write(test_script2)

sftp = cli.open_sftp()
sftp.put(tmp2, "C:/linzhixia/test_sf.py")
sftp.close()

rc, o, e = run("C:\\linzhixia\\.venv\\Scripts\\python.exe C:\\linzhixia\\test_sf.py")
print(o if o else e)

cli.close()
