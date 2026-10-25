# -*- coding: utf-8 -*-
"""sidecar 薄启动壳 —— PyInstaller 的入口（也支持源码直跑）。

职责只有四件：
1. 把打包时带进来的 core_features/ 种到 exe 旁边的家目录（首次启动）
2. 启动技能宿主（skill_host，端口 8790），跟云端 server_run.bat 保持一致
3. 选一个空闲端口，经 VIGIL_PORT 环境变量交给 server.py
4. 把候选端口和进程身份交给 Electron，由 Electron 验证 API 后确认就绪

大脑本体一律不碰：import server 之后 main() 会像云端一样自己转起来。
"""
import os
import shutil
import socket
import subprocess
import sys

# Frozen Windows processes can inherit a legacy code page (for example cp1252).
# Migration diagnostics include Chinese text and must never crash startup while
# writing stdout/stderr. runtime.py also configures these streams, but it loads
# only after _seed_core_features(), so configure them here first.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

FROZEN = bool(getattr(sys, "frozen", False))
CODE_DIR = getattr(sys, "_MEIPASS", None) or os.path.dirname(
    os.path.abspath(__file__))
HOME_DIR = os.path.abspath(os.environ.get('VIGIL_HOME') or
    (os.path.dirname(os.path.abspath(sys.executable)) if FROZEN else CODE_DIR))



def _seed_core_features():
    """把随包的能力适配器与内置插件铺到 exe 旁边；wallpaper 等外部插件不由安装包带。"""
    if not FROZEN:
        return
    src = os.path.join(CODE_DIR, "core_features")
    if not os.path.isdir(src):
        raise RuntimeError("Required core feature payload is missing: " + src)
    import json
    from home_migration import (seed_core_features, seed_plugins,
                                migrate_legacy_plugins, retire_superseded_installs)
    legacy_roots = []
    if os.environ.get("VIGIL_MANAGED") == "1":
        legacy_roots = json.loads(os.environ.get("VIGIL_LEGACY_HOMES", "[]"))
    try:
        print("[sidecar] phase=plugin_migration:start", flush=True)
        migrated = migrate_legacy_plugins(HOME_DIR, legacy_roots)
        print("[sidecar] phase=plugin_migration:done", flush=True)
        if migrated.get("copied"):
            print("[sidecar] 已迁移旧版用户插件 %d 个 -> data/installed_plugins" %
                  migrated["copied"], flush=True)
        for error in migrated.get("errors", []):
            print("[sidecar] 旧版插件迁移未完成，原文件已保留：%s" % error, flush=True)
    except Exception as exc:
        # Plugin migration is best-effort. A full disk or an unreadable legacy
        # directory must not prevent the Brain from starting with user data intact.
        print("[sidecar] 旧版插件迁移失败，Brain 继续启动；原文件已保留：%s" %
              str(exc)[:180], flush=True)
    # 顺序要紧：先迁移旧 home 的 plugins/，再停用 P21.0 遗留的旧随包副本，
    # 最后铺新版内置；反过来会把旧文件当成"用户改动"复制进 installed，
    # 从而覆盖掉刚铺好的新版。
    try:
        print("[sidecar] phase=retire_superseded_installs:start", flush=True)
        retired = retire_superseded_installs(HOME_DIR)
        print("[sidecar] phase=retire_superseded_installs:done", flush=True)
        if retired.get("retired"):
            print("[sidecar] 已停用 %d 个旧版随包插件副本（保留在 installed_plugins/_retired）" %
                  retired["retired"], flush=True)
        for error in retired.get("errors", []):
            print("[sidecar] 旧副本停用未完成，原文件已保留：%s" % error, flush=True)
    except Exception as exc:
        print("[sidecar] 旧副本停用失败，Brain 继续启动：%s" % str(exc)[:180], flush=True)
    print("[sidecar] phase=seed_core_features_payload:start", flush=True)
    seed_core_features(src, HOME_DIR, legacy_roots)
    print("[sidecar] phase=seed_core_features_payload:done", flush=True)
    bundled = os.path.join(CODE_DIR, "plugins")
    if os.path.isdir(bundled):
        try:
            print("[sidecar] phase=seed_plugins:start", flush=True)
            seeded = seed_plugins(bundled, HOME_DIR, legacy_roots)
            print("[sidecar] phase=seed_plugins:done", flush=True)
            print("[sidecar] 内置插件已就位 %d 个（跟踪 %d 个文件）" %
                  (seeded["seeded"], seeded["tracked"]), flush=True)
        except Exception as exc:
            print("[sidecar] 内置插件铺设失败，Brain 继续启动：%s" % str(exc)[:180], flush=True)


def _start_skill_host():
    """启动技能宿主（Node.js，端口 8790）。

    跟云端 server_run.bat 保持一致：先起宿主，再起大脑，
    这样大脑加载插件时技能已经可用。
    start_host.bat 本身是幂等的（先查 8790 端口是否被占用），重复调用安全。
    启动失败不致命：少了技能功能，不影响聊天。
    """
    if os.environ.get('VIGIL_MANAGED') == '1':
        # Electron supplies its own Node runtime; a clean PC needs no Node install.
        import secrets
        os.environ['ZX_SKILL_AUTH_TOKEN'] = secrets.token_urlsafe(32)
        os.environ['ZX_SKILL_HOST'] = '127.0.0.1'
        os.environ['ZX_SKILL_PORT'] = str(_pick_port())
        os.environ['VIGIL_SKILL_HOST'] = 'http://127.0.0.1:' + os.environ['ZX_SKILL_PORT']
        skills = os.path.join(HOME_DIR, 'skill_host', 'skills')
        os.makedirs(skills, exist_ok=True)
        package = os.path.join(skills, 'package.json')
        if not os.path.exists(package):
            with open(package, 'w', encoding='utf-8') as f:
                f.write('{"type":"module"}')
        seeds = os.path.join(CODE_DIR, 'skill_host', 'skills')
        if os.path.isdir(seeds):
            for name in os.listdir(seeds):
                dst = os.path.join(skills, name)
                if not os.path.exists(dst):
                    src = os.path.join(seeds, name)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst)
                    else:
                        shutil.copy2(src, dst)
        os.environ['ZX_SKILL_DIR'] = skills
        env = dict(os.environ, ELECTRON_RUN_AS_NODE='1', VIGIL_PARENT_PID=str(os.getpid()))
        subprocess.Popen([os.environ['VIGIL_NODE_EXE'], os.path.join(CODE_DIR, 'skill_host', 'server.js')],
            cwd=HOME_DIR, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        return
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


def _pick_port(exclude=()):
    """--port 参数 > VIGIL_PORT 环境变量 > 让系统挑一个空闲端口。"""
    if "--port" in sys.argv:
        try:
            port = int(sys.argv[sys.argv.index("--port") + 1])
            if port not in exclude:
                return port
            raise ValueError("Brain port conflicts with the managed Skill Host")
        except (ValueError, IndexError):
            pass
    try:
        env_port = int(os.environ.get("VIGIL_PORT") or 0)
        if env_port > 0:
            if env_port in exclude:
                raise ValueError("Brain port conflicts with the managed Skill Host")
            return env_port
    except ValueError:
        pass
    # 没给端口（或给了 0/非法值）就让系统挑一个空闲的
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        for _ in range(20):
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            if port not in exclude:
                return port
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raise RuntimeError("Unable to select a port distinct from the managed Skill Host")
    finally:
        s.close()


def _write_candidate_ready(port):
    """Publish identity and a candidate port; Electron verifies API health."""
    ready = os.environ.get('VIGIL_READY_FILE')
    if not ready:
        return False
    import json
    import build_identity
    with open(ready + '.tmp', 'w', encoding='utf-8') as stream:
        json.dump(dict(build_identity.runtime(), port=port), stream)
    os.replace(ready + '.tmp', ready)
    return True


def main():
    if os.environ.get('VIGIL_MANAGED') == '1':
        import json
        import secrets
        from pathlib import Path
        from home_migration import migrate
        for legacy in json.loads(os.environ.get('VIGIL_LEGACY_HOMES', '[]')):
            if Path(legacy, 'config', 'config.json').is_file():
                migrate(legacy, HOME_DIR)
        cfg = Path(HOME_DIR, 'config', 'config.json')
        cfg.parent.mkdir(parents=True, exist_ok=True)
        value = json.loads(cfg.read_text(encoding='utf-8-sig')) if cfg.exists() else {}
        if not value.get('brain_token'):
            value['brain_token'] = secrets.token_urlsafe(32)
            tmp = cfg.with_suffix('.tmp')
            tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp, cfg)
        os.environ['VIGIL_BIND_HOST'] = '127.0.0.1'
    os.makedirs(HOME_DIR, exist_ok=True)
    os.chdir(HOME_DIR)          # 大脑里所有相对路径的家都在这儿
    print("[sidecar] phase=seed_core_features:start", flush=True)
    _seed_core_features()
    print("[sidecar] phase=seed_core_features:done", flush=True)
    print("[sidecar] phase=skill_host:start", flush=True)
    _start_skill_host()         # 先起技能宿主，大脑加载插件时技能已可用
    print("[sidecar] phase=skill_host:spawned", flush=True)
    managed = os.environ.get('VIGIL_MANAGED') == '1'
    skill_port = int(os.environ.get('ZX_SKILL_PORT') or 0) if managed else 0
    port = _pick_port(exclude={skill_port} if skill_port else ())
    os.environ["VIGIL_PORT"] = str(port)

    print("[sidecar] phase=server_import:start", flush=True)
    import server               # 大脑本体（导入即完成全部初始化的准备工作）
    print("[sidecar] phase=server_import:done", flush=True)

    # 先发布本进程的候选端口和身份。Electron sidecar 自己探测运行中的
    # /api/runtime 和关键路由后才会转为 ready；这样单一探活者拥有最终判断，
    # 不依赖 Brain 内部后台线程写入通知，也不会把文件存在误当成健康。
    _write_candidate_ready(port)
    print("[sidecar] phase=ready_file:published", flush=True)
    server.main()


if __name__ == "__main__":
    main()
