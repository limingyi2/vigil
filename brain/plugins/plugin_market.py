# -*- coding: utf-8 -*-
"""插件市场：浏览、安装、卸载远程插件。

市场源是本地 git 仓库（默认 F:\\vigil-plugins），market.json 列插件清单。
安装 = 从本地仓库复制插件目录到 brain/plugins/；卸载 = 删除目录。
"""
import json
import os
import shutil
import subprocess

NAME = "plugin_market"
DESC = "插件市场：浏览/安装/卸载插件"
FEATURE = "plugin_market"

# 市场仓库本地路径（可通过 config 的 market_repo 覆盖）
DEFAULT_REPO = r"F:\vigil-plugins"


def _repo_path():
    """市场仓库本地路径。优先读 config，没有用默认。"""
    try:
        from paths import CONFIG_DIR
        cfg_path = os.path.join(str(CONFIG_DIR), "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        p = cfg.get("market_repo") or DEFAULT_REPO
        return str(p)
    except Exception:
        return DEFAULT_REPO


def _plugins_dir():
    """本地插件目录（brain/plugins/）。"""
    try:
        from paths import PLUGINS_DIR
        return str(PLUGINS_DIR)
    except Exception:
        return r"C:\vigil\plugins"


def _list_local():
    """已安装的插件名集合。"""
    installed = set()
    pdir = _plugins_dir()
    try:
        for fn in os.listdir(pdir):
            fpath = os.path.join(pdir, fn)
            # 单文件插件
            if fn.endswith(".py") and not fn.startswith("_"):
                installed.add(os.path.splitext(fn)[0])
            # 目录型插件
            elif os.path.isdir(fpath) and os.path.isfile(os.path.join(fpath, "__init__.py")):
                installed.add(fn)
    except OSError:
        pass
    return installed


def _read_market():
    """读取市场仓库的 market.json。"""
    repo = _repo_path()
    mpath = os.path.join(repo, "market.json")
    if not os.path.isfile(mpath):
        return {"version": "0", "updated": "", "plugins": []}
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {"version": "0", "updated": "", "plugins": [], "err": str(e)[:80]}


def api_list(body, query):
    """GET /api/market/list —— 市场插件列表 + 已安装标记。"""
    market = _read_market()
    installed = _list_local()
    plugins = []
    for p in market.get("plugins", []):
        item = dict(p)
        item["installed"] = p.get("name") in installed
        plugins.append(item)
    return {
        "ok": True,
        "repo": _repo_path(),
        "market_version": market.get("version", ""),
        "updated": market.get("updated", ""),
        "plugins": plugins,
        "installed_count": len(installed),
    }


def api_install(body, query):
    """POST /api/market/install —— 安装插件（从本地仓库复制到 brain/plugins/）。

    body: {"name": "插件名"}
    """
    name = str((body or {}).get("name") or "").strip()
    if not name:
        return {"ok": False, "err": "缺少插件名"}

    # 在市场清单里找
    market = _read_market()
    meta = None
    for p in market.get("plugins", []):
        if p.get("name") == name:
            meta = p
            break
    if not meta:
        return {"ok": False, "err": "市场里找不到插件 %s" % name}

    # 源目录
    repo = _repo_path()
    src = os.path.join(repo, meta.get("path", "plugins/" + name))
    if not os.path.isdir(src):
        return {"ok": False, "err": "插件源目录不存在：%s" % src}

    # 目标目录
    dst = os.path.join(_plugins_dir(), name)

    # 如果已存在，先删（覆盖安装）
    if os.path.exists(dst):
        try:
            shutil.rmtree(dst)
        except Exception as e:
            return {"ok": False, "err": "删除旧版本失败：%s" % str(e)[:80]}

    # 复制
    try:
        shutil.copytree(src, dst)
    except Exception as e:
        return {"ok": False, "err": "复制失败：%s" % str(e)[:80]}

    # 热重载：重新扫描插件目录，新插件立即生效（路由/工具/钩子全动态）
    _reload_plugins()

    return {"ok": True, "name": name, "msg": "安装成功，已热重载生效"}


def api_uninstall(body, query):
    """POST /api/market/uninstall —— 卸载插件（删除 brain/plugins/ 下的目录）。

    body: {"name": "插件名"}
    """
    name = str((body or {}).get("name") or "").strip()
    if not name:
        return {"ok": False, "err": "缺少插件名"}

    # 只允许卸载目录型插件（市场里的都是目录型）
    dst = os.path.join(_plugins_dir(), name)
    if not os.path.isdir(dst):
        # 可能是单文件插件
        fpath = os.path.join(_plugins_dir(), name + ".py")
        if os.path.isfile(fpath):
            try:
                os.remove(fpath)
            except Exception as e:
                return {"ok": False, "err": "删除失败：%s" % str(e)[:80]}
            _reload_plugins()
            return {"ok": True, "name": name, "msg": "卸载成功，已热重载"}
        return {"ok": False, "err": "插件未安装：%s" % name}

    try:
        shutil.rmtree(dst)
    except Exception as e:
        return {"ok": False, "err": "删除失败：%s" % str(e)[:80]}

    _reload_plugins()
    return {"ok": True, "name": name, "msg": "卸载成功，已热重载"}


def _reload_plugins():
    """热重载插件目录。失败静默，重启后仍会生效。"""
    try:
        import plugin_host
        plugin_host.load(force=True)
    except Exception as e:
        print("[插件市场] 热重载失败（不影响卸载，重启后生效）：%s" % str(e)[:80], flush=True)


def api_refresh(body, query):
    """POST /api/market/refresh —— git pull 更新本地市场仓库。"""
    repo = _repo_path()
    if not os.path.isdir(os.path.join(repo, ".git")):
        return {"ok": False, "err": "市场仓库不是 git 仓库：%s" % repo}
    try:
        result = subprocess.run(
            ["git", "pull"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        return {"ok": result.returncode == 0, "output": output[:300]}
    except Exception as e:
        return {"ok": False, "err": str(e)[:80]}


ROUTES = [
    ("GET", "/api/market/list", api_list),
    ("POST", "/api/market/install", api_install),
    ("POST", "/api/market/uninstall", api_uninstall),
    ("POST", "/api/market/refresh", api_refresh),
]
