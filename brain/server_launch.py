# -*- coding: utf-8 -*-
"""sidecar 薄启动壳 —— PyInstaller 的入口（也支持源码直跑）。

职责只有四件：
1. 把打包时带进来的 plugins/ 种到 exe 旁边的家目录（首次启动）
2. 启动技能宿主（skill_host，端口 8790），跟云端 server_run.bat 保持一致
3. 选一个空闲端口，经 VIGIL_PORT 环境变量交给 server.py
4. /api/health 探活通过后往 stdout 打一行就绪标记（Electron 靠它拿端口）

大脑本体一律不碰：import server 之后 main() 会像云端一样自己转起来。
"""
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

FROZEN = bool(getattr(sys, "frozen", False))
CODE_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(
    os.path.abspath(__file__))
HOME_DIR = (os.path.dirname(os.path.abspath(sys.executable))
            if FROZEN else CODE_DIR)

READY_TAG = "VIGIL_BRAIN_READY"


def _seed_plugins():
    """把随包分发的插件铺到家目录 plugins/。

    逐个插件补齐：已存在的插件不覆盖，避免用户后来装/改的内容被
    每次启动回滚；目录型插件缺失时复制整个目录（包括前端资源）。
    复制失败不致命：少几个功能，不影响聊天。
    """
    if not FROZEN:
        return
    src = os.path.join(CODE_DIR, "plugins")
    dst = os.path.join(HOME_DIR, "plugins")
    if not os.path.isdir(src):
        return
    try:
        os.makedirs(dst, exist_ok=True)
        for name in os.listdir(src):
            source = os.path.join(src, name)
            target = os.path.join(dst, name)
            if os.path.exists(target):
                continue
            if os.path.isdir(source):
                shutil.copytree(source, target)
            elif name.endswith(".py") and not name.startswith("_"):
                shutil.copy2(source, target)
        print("[sidecar] 已铺开随包插件 -> %s" % dst, flush=True)
    except Exception as e:
        print("[sidecar] 铺插件失败（不影响聊天）：%s" % e, flush=True)


def _start_skill_host():
    """启动技能宿主（Node.js，端口 8790）。

    跟云端 server_run.bat 保持一致：先起宿主，再起大脑，
    这样大脑加载插件时技能已经可用。
    start_host.bat 本身是幂等的（先查 8790 端口是否被占用），重复调用安全。
    启动失败不致命：少了技能功能，不影响聊天。
    """
    # 开发环境：CODE_DIR/skill_host/start_host.bat
    # 打包环境：HOME_DIR/skill_host/start_host.bat
    candidates = [
        os.path.join(HOME_DIR, "skill_host", "start_host.bat"),
        os.path.join(CODE_DIR, "skill_host", "start_host.bat"),
    ]
    bat = None
    for p in candidates:
        if os.path.exists(p):
            bat = p
            break
    if not bat:
        print("[sidecar] 未找到 skill_host/start_host.bat，技能宿主未启动", flush=True)
        return
    try:
        creationflags = 0
        if sys.platform == "win32":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        subprocess.Popen(
            ["cmd", "/c", bat],
            cwd=os.path.dirname(bat),
            creationflags=creationflags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print("[sidecar] 已启动技能宿主 -> %s" % bat, flush=True)
    except Exception as e:
        print("[sidecar] 技能宿主启动失败（不影响聊天）：%s" % e, flush=True)


def _pick_port():
    """--port 参数 > VIGIL_PORT 环境变量 > 让系统挑一个空闲端口。"""
    if "--port" in sys.argv:
        try:
            return int(sys.argv[sys.argv.index("--port") + 1])
        except (ValueError, IndexError):
            pass
    try:
        env_port = int(os.environ.get("VIGIL_PORT") or 0)
        if env_port > 0:
            return env_port
    except ValueError:
        pass
    # 没给端口（或给了 0/非法值）就让系统挑一个空闲的
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def _wait_health(port, timeout=180):
    """轮询 /api/health 直到 200。首次启动可能要下载嵌入模型，放宽到 3 分钟。"""
    url = "http://127.0.0.1:%d/api/health" % port
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main():
    os.chdir(HOME_DIR)          # 大脑里所有相对路径的家都在这儿
    _seed_plugins()
    _start_skill_host()         # 先起技能宿主，大脑加载插件时技能已可用
    port = _pick_port()
    os.environ["VIGIL_PORT"] = str(port)

    import server               # 大脑本体（导入即完成全部初始化的准备工作）

    # 探活放后台线程：main() 会永远阻塞，等它先跑起来才有得探
    import threading

    def _announce():
        if _wait_health(port):
            print("%s port=%d pid=%d" % (READY_TAG, port, os.getpid()),
                  flush=True)
        else:
            print("%s FAILED port=%d（健康检查超时，看上面的报错）"
                  % (READY_TAG, port), flush=True)

    threading.Thread(target=_announce, daemon=True).start()
    server.main()


if __name__ == "__main__":
    main()
