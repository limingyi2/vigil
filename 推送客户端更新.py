# -*- coding: utf-8 -*-
"""Vigil 客户端更新推送（只推送，不重新打包）。

适用：已经 `npm run dist` 打好包，只需把**新安装包**推到云端。

修掉的两个坑：
  1) 老脚本按字母序选包，误选了**便携版** "Vigil 0.1.2.exe"；
     云端大脑 /api/client/setup 只认 NSIS 安装版，必须上传 "Vigil Setup x.y.z.exe"。
  2) 老脚本依赖 paramiko；本脚本优先用 Windows 自带的 scp/ssh，**无需 pip 安装**。
     （只有系统没有 scp 时才回退 paramiko）

用法：
    cd /d F:\\Vigil
    python 推送客户端更新.py "本次更新说明"
"""

import os
import sys
import glob
import json
import hashlib
import subprocess

ROOT = r"F:\Vigil"
CDIST = os.path.join(ROOT, "client", "dist")
MANIFEST = os.path.join(ROOT, "dist", "client_version.json")
KEY = os.path.join(ROOT, "deploy", "sshkeys", "linzhixia_deploy")
HOST = "203.0.113.10"
USER = "Administrator"
RDIR = "C:/Vigil/app/client"          # 远端目录（scp 用正斜杠）
RREMOTE = "C:\\Vigil\\app\\client"    # 远端目录（cmd 用反斜杠）
NOTES = sys.argv[1] if len(sys.argv) > 1 else "修复统一管理页未走云端（节点一直显示 0）"


def pick_setup():
    """挑 NSIS 安装版：文件名含 setup，排除 win-unpacked；取最新修改的一个。"""
    exes = [p for p in glob.glob(os.path.join(CDIST, "*.exe"))
            if "unpacked" not in p.lower()]
    nsis = [p for p in exes if "setup" in os.path.basename(p).lower()]
    pool = nsis or exes
    if not pool:
        raise SystemExit("没在 client/dist 找到 .exe，请先在 client\\ 目录执行 npm run dist")
    if not nsis:
        print("!! 警告：没找到名字含 Setup 的 NSIS 安装包，退而选用：",
              [os.path.basename(p) for p in pool])
    return max(pool, key=os.path.getmtime)


setup = pick_setup()
name = os.path.basename(setup)
size = os.path.getsize(setup)

v = json.load(open(os.path.join(ROOT, "client", "version.json"), encoding="utf-8"))
code = int(v.get("code", 0))
ver = v.get("ver")

print("计算 sha256（%s，%.0f MB）..." % (name, size / 1048576.0), flush=True)
h = hashlib.sha256()
with open(setup, "rb") as f:
    for b in iter(lambda: f.read(1 << 20), b""):
        h.update(b)
sha = h.hexdigest()

man = {"code": code, "ver": ver, "sha256": sha, "size": size, "notes": NOTES}
os.makedirs(os.path.dirname(MANIFEST), exist_ok=True)
json.dump(man, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("安装包 :", name, size, "字节")
print("sha256 :", sha)
print("清单   :", MANIFEST, "=>", json.dumps(man, ensure_ascii=False))


def find_tool(n):
    p = os.path.join(r"C:\Windows\System32\OpenSSH", n)
    if os.path.exists(p):
        return p
    from shutil import which
    return which(n)


scp, ssh = find_tool("scp.exe"), find_tool("ssh.exe")
remote = "%s@%s" % (USER, HOST)
opts = ["-i", KEY, "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL"]


def upload_via_scp():
    if ssh:
        print("备份云端旧文件 ...", flush=True)
        subprocess.run([ssh] + opts + [remote,
            'copy /Y "%s\\VigilSetup.exe" "%s\\VigilSetup.exe.bak"' % (RREMOTE, RREMOTE)])
        subprocess.run([ssh] + opts + [remote,
            'copy /Y "%s\\client_version.json" "%s\\client_version.json.bak"' % (RREMOTE, RREMOTE)])
    print("上传安装包（约 %.0f MB，家宽可能几分钟）..." % (size / 1048576.0), flush=True)
    r1 = subprocess.run([scp] + opts + [setup, remote + ":" + RDIR + "/VigilSetup.exe"])
    if r1.returncode != 0:
        raise SystemExit("上传安装包失败（scp 返回 %d）" % r1.returncode)
    r2 = subprocess.run([scp] + opts + [MANIFEST, remote + ":" + RDIR + "/client_version.json"])
    if r2.returncode != 0:
        raise SystemExit("上传清单失败（scp 返回 %d）" % r2.returncode)
    # 校验远端大小
    if ssh:
        subprocess.run([ssh] + opts + [remote,
            'powershell -NoProfile -Command "(Get-Item \'%s\\VigilSetup.exe\').Length"' % RREMOTE])
    print("已通过 scp 上传完成")


def upload_via_paramiko():
    try:
        import paramiko
    except ImportError:
        raise SystemExit(
            "系统里既没有 scp，也没装 paramiko。\n"
            "任选其一：\n"
            "  A. 启用 Windows OpenSSH 客户端（设置→应用→可选功能→添加 OpenSSH 客户端）\n"
            "  B. pip install paramiko   然后重跑本脚本"
        )
    print("用 paramiko 上传 ...", flush=True)
    sshc = paramiko.SSHClient()
    sshc.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    sshc.connect(HOST, username=USER, key_filename=KEY, timeout=30, banner_timeout=30)

    def rr(c, tmo=600):
        _, o, _ = sshc.exec_command(c, timeout=tmo)
        return o.channel.recv_exit_status()

    rr('copy /Y "%s\\VigilSetup.exe" "%s\\VigilSetup.exe.bak"' % (RREMOTE, RREMOTE))
    rr('copy /Y "%s\\client_version.json" "%s\\client_version.json.bak"' % (RREMOTE, RREMOTE))
    sftp = sshc.open_sftp()
    sftp.put(setup, RDIR + "/VigilSetup.exe")
    sftp.put(MANIFEST, RDIR + "/client_version.json")
    sftp.close()
    sshc.close()
    print("已通过 paramiko 上传完成")


if scp:
    upload_via_scp()
else:
    upload_via_paramiko()

print()
print("完成！云端已发布 code=%d（ver %s）。" % (code, ver))
print("旧版客户端下次启动，或到 关于页 点「检查更新」，即可收到推送并覆盖安装（配置不丢）。")
