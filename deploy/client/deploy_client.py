# -*- coding: utf-8 -*-
"""
推送客户端更新到云端。

用法:
    python deploy/client/deploy_client.py --code 9 --ver 0.1.3 --notes "修复bug"

参数:
    --code    版本号（整数，客户端用它判断是否有更新）
    --ver     语义化版本号（如 0.1.3，仅展示用）
    --notes   更新说明（可选，默认"常规更新"）
"""
import argparse
import hashlib
import json
import os
import time
import paramiko

KEY = r"F:\Vigil\deploy\sshkeys\linzhixia_deploy"
CLOUD = "203.0.113.10"
LOCAL_EXE = r"F:\Vigil\dist\VigilSetup.exe"
REMOTE_DIR = r"C:\Vigil\app\client"


def run(ssh, cmd, timeout=120):
    _c, out, err = ssh.exec_command(cmd, timeout=timeout)
    rc = out.channel.recv_exit_status()
    return (rc, out.read().decode("utf-8", "replace").strip(),
            err.read().decode("utf-8", "replace").strip())


def main():
    parser = argparse.ArgumentParser(description="推送客户端更新到云端")
    parser.add_argument("--code", type=int, required=True, help="版本号（整数）")
    parser.add_argument("--ver", type=str, default=None, help="语义化版本号（如 0.1.3）")
    parser.add_argument("--notes", type=str, default="常规更新", help="更新说明")
    args = parser.parse_args()

    code = args.code
    ver = args.ver or f"0.1.{code}"
    notes = args.notes

    # 检查本地文件
    if not os.path.exists(LOCAL_EXE):
        print(f"错误: 找不到安装包 {LOCAL_EXE}")
        print("请先运行 npm run dist 打包客户端，然后复制到 dist/VigilSetup.exe")
        return

    # 1. 计算本地文件 sha256
    print("计算安装包 sha256...")
    h = hashlib.sha256()
    with open(LOCAL_EXE, "rb") as f:
        while True:
            chunk = f.read(1024 * 1024)
            if not chunk:
                break
            h.update(chunk)
    sha = h.hexdigest()
    size = os.path.getsize(LOCAL_EXE)
    print(f"  版本: {ver} (code={code})")
    print(f"  说明: {notes}")
    print(f"  sha256: {sha[:16]}...")
    print(f"  size: {size} bytes ({size / 1024 / 1024:.1f} MB)")

    # 2. 连接服务器
    print(f"\n连接服务器 {CLOUD} ...")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(CLOUD, username="Administrator", key_filename=KEY,
                timeout=30, banner_timeout=30, auth_timeout=30)
    sftp = ssh.open_sftp()
    print("  已连接")

    # 3. 备份旧版本
    print("\n备份旧版本...")
    run(ssh, f"if exist {REMOTE_DIR}\\VigilSetup.exe "
             f"copy /Y {REMOTE_DIR}\\VigilSetup.exe {REMOTE_DIR}\\VigilSetup.exe.bak")
    run(ssh, f"if exist {REMOTE_DIR}\\client_version.json "
             f"copy /Y {REMOTE_DIR}\\client_version.json {REMOTE_DIR}\\client_version.json.bak")
    print("  备份完成")

    # 4. 上传安装包
    print(f"\n上传安装包 ({size / 1024 / 1024:.1f} MB)...")
    start = time.time()
    sftp.put(LOCAL_EXE, REMOTE_DIR.replace("\\", "/") + "/VigilSetup.exe")
    elapsed = time.time() - start
    print(f"  上传完成，用时 {elapsed:.1f} 秒")

    # 5. 验证远端文件 sha256
    print("\n验证远端文件...")
    rc, out, err = run(ssh, f"powershell -Command \"(Get-FileHash '{REMOTE_DIR}\\VigilSetup.exe' -Algorithm SHA256).Hash.ToLower()\"", timeout=120)
    remote_sha = (out or "").strip().lower()
    print(f"  本地 sha: {sha[:16]}...")
    print(f"  远端 sha: {remote_sha[:16]}...")
    if remote_sha != sha:
        print("  WARNING: sha256 不一致！")
    else:
        print("  sha256 一致 ✓")

    # 6. 上传 client_version.json
    print("\n更新 client_version.json...")
    vf = {"code": code, "ver": ver, "notes": notes, "sha256": sha, "size": size}
    with sftp.open(REMOTE_DIR.replace("\\", "/") + "/client_version.json", "w") as f:
        f.write(json.dumps(vf, ensure_ascii=False, indent=2))
    print(f"  已写入 code={code}, ver={ver}")

    # 7. 验证目录
    print("\n验证远端目录...")
    rc, out, err = run(ssh, f"dir {REMOTE_DIR}")
    print(out or err)

    sftp.close()
    ssh.close()
    print(f"\n✅ 推送完成！客户端重启后会提示更新到 {ver} (code={code})")


if __name__ == "__main__":
    main()
