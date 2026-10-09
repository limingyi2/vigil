# -*- coding: utf-8 -*-
"""Vigil 桌面客户端「改代码/改配置 → 重新打包 → 推送更新」一键脚本。

为什么需要它：
  你装的是**打包好的客户端**。界面代码、以及**随包分发的大脑配置**
  （client/../dist/vigil-brain/config/config.json）都已固化进安装包。
  改 F:\\Vigil 里的源码或那份大脑配置，都不会影响正在运行的安装版。
  必须重新打包，再把新安装包推到云端，旧客户端才会通过「检查更新」收到。

脚本做的事（在 Windows 上、F:\\Vigil 目录里运行）：
  1) 把 client/version.json 的 code 加 1（"我是谁"的版本号，会打进包里）
  2) 在 client/ 下执行 npm run dist 重新打包
     —— electron-builder 会把 ../dist/vigil-brain（含 config/）整体塞进安装包，
        所以只要事先改好 dist/vigil-brain/config/config.json，新包就会带上
  3) 找打好的 **NSIS 安装版**，算 sha256 + 大小，写入 dist/client_version.json
  4) 把 VigilSetup.exe + client_version.json 上传到云端 A 的
     C:\\Vigil\\app\\client\\（上传前把云端旧文件改名 .bak）

  上传优先用 Windows 自带的 scp/ssh（无需 pip 装 paramiko）；
  没有 scp 才回退 paramiko。

用法：
    cd /d F:\\Vigil
    python 发布客户端更新.py "本次更新说明"
前置：
    - 已安装 Node/npm 且 client/ 下 npm install 过
    - deploy\\sshkeys\\linzhixia_deploy 私钥可用
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = r"F:\Vigil"
CLIENT = os.path.join(ROOT, "client")
DIST = os.path.join(CLIENT, "dist")
KEY = os.path.join(ROOT, "deploy", "sshkeys", "linzhixia_deploy")
CLOUD_HOST = "203.0.113.10"
CLOUD_USER = "Administrator"
REMOTE_DIR = "C:/Vigil/app/client"          # scp 用正斜杠
REMOTE_WIN = "C:\\Vigil\\app\\client"        # cmd 用反斜杠
NOTES = sys.argv[1] if len(sys.argv) > 1 else "修复统一管理页未走云端（节点一直显示 0）"


def run(cmd, cwd):
    print("+", cmd, flush=True)
    r = subprocess.run(cmd, cwd=cwd, shell=True)
    if r.returncode != 0:
        raise SystemExit("命令失败：" + cmd)


# 1) 递增版本号（务必在打包前，版本号会被打进包里）
vf = os.path.join(CLIENT, "version.json")
v = json.load(open(vf, encoding="utf-8"))
old = int(v.get("code", 0))
v["code"] = old + 1
json.dump(v, open(vf, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("版本 code：%d -> %d（ver %s）" % (old, v["code"], v.get("ver")), flush=True)

# 2) 打包
run("npm run dist", CLIENT)

# 3) 找安装包（优先 NSIS 安装版）+ 生成发布清单
cands = [fn for fn in os.listdir(DIST)
         if fn.lower().endswith(".exe") and "unpacked" not in fn.lower()]
setup = None
for fn in sorted(cands, key=lambda n: (0 if "setup" in n.lower() else 1, n)):
    setup = os.path.join(DIST, fn)
    break
if not setup:
    raise SystemExit("没在 client/dist/ 找到安装包，检查打包是否成功")
size = os.path.getsize(setup)
h = hashlib.sha256()
with open(setup, "rb") as f:
    for b in iter(lambda: f.read(1 << 20), b""):
        h.update(b)
sha = h.hexdigest()
print("安装包：%s（%d 字节，sha256=%s…）" % (os.path.basename(setup), size, sha[:16]), flush=True)

manifest = {"code": v["code"], "ver": v.get("ver"), "sha256": sha, "size": size, "notes": NOTES}
mf = os.path.join(ROOT, "dist", "client_version.json")
json.dump(manifest, open(mf, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("发布清单 ->", mf, "=>", json.dumps(manifest, ensure_ascii=False), flush=True)

# 4) 上传到云端 A
def find_tool(n):
    p = os.path.join(r"C:\Windows\System32\OpenSSH", n)
    if os.path.exists(p):
        return p
    return shutil.which(n)


scp, ssh = find_tool("scp.exe"), find_tool("ssh.exe")
remote = "%s@%s" % (CLOUD_USER, CLOUD_HOST)
opts = ["-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL"]


def upload_via_scp():
    if ssh:
        print("备份云端旧文件 ...", flush=True)
        subprocess.run([ssh] + opts + [remote,
            'copy /Y "%s\\VigilSetup.exe" "%s\\VigilSetup.exe.bak"' % (REMOTE_WIN, REMOTE_WIN)])
        subprocess.run([ssh] + opts + [remote,
            'copy /Y "%s\\client_version.json" "%s\\client_version.json.bak"' % (REMOTE_WIN, REMOTE_WIN)])
    print("上传安装包（约 %.0f MB，家宽可能几分钟）..." % (size / 1048576.0), flush=True)
    r1 = subprocess.run([scp] + opts + [setup, remote + ":" + REMOTE_DIR + "/VigilSetup.exe"])
    if r1.returncode != 0:
        raise SystemExit("上传安装包失败（scp 返回 %d）" % r1.returncode)
    r2 = subprocess.run([scp] + opts + [mf, remote + ":" + REMOTE_DIR + "/client_version.json"])
    if r2.returncode != 0:
        raise SystemExit("上传清单失败（scp 返回 %d）" % r2.returncode)
    if ssh:
        subprocess.run([ssh] + opts + [remote,
            'powershell -NoProfile -Command "(Get-Item \'%s\\VigilSetup.exe\').Length"' % REMOTE_WIN])


def upload_via_paramiko():
    try:
        import paramiko
    except ImportError:
        raise SystemExit("系统里既没有 scp，也没装 paramiko；启用 Windows OpenSSH 或 pip install paramiko")
    print("用 paramiko 上传 ...", flush=True)
    sshc = paramiko.SSHClient()
    sshc.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sshc.connect(CLOUD_HOST, username=CLOUD_USER, key_filename=KEY, timeout=30, banner_timeout=30)
    for c in ('copy /Y "%s\\VigilSetup.exe" "%s\\VigilSetup.exe.bak"' % (REMOTE_WIN, REMOTE_WIN),
              'copy /Y "%s\\client_version.json" "%s\\client_version.json.bak"' % (REMOTE_WIN, REMOTE_WIN)):
        _, o, _ = sshc.exec_command(c, timeout=180)
        o.channel.recv_exit_status()
    sftp = sshc.open_sftp()
    sftp.put(setup, REMOTE_DIR + "/VigilSetup.exe")
    sftp.put(mf, REMOTE_DIR + "/client_version.json")
    sftp.close()
    sshc.close()


if scp:
    upload_via_scp()
else:
    upload_via_paramiko()

print("\n完成！云端已发布 code=%d。旧版客户端下次启动会自动收到更新。" % v["code"], flush=True)
