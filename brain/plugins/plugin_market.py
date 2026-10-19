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

# 市场仓库由 config/config.json 中的 market_repo 指定。安装版从其自身 CONFIG_DIR 读取。
DEFAULT_REPO = ""


def _repo_path():
    """市场仓库本地路径。优先读 config，没有用默认。"""
    try:
        from paths import CONFIG_DIR
        cfg_path = os.path.join(str(CONFIG_DIR), "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        p = cfg.get("market_repo")
        if not isinstance(p, str) or not p.strip():
            return DEFAULT_REPO
        p = os.path.expanduser(os.path.expandvars(p.strip()))
        if not os.path.isabs(p):
            p = os.path.join(str(CONFIG_DIR), p)
        return os.path.abspath(p)
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
    """读取市场仓库的 market.json，并区分未配置、读取失败和空市场。"""
    repo = _repo_path()
    if not repo:
        return {"state": "not_configured", "plugins": []}
    if not os.path.isdir(repo):
        return {"state": "read_failed", "plugins": [], "err": "配置的市场仓库目录不存在"}
    mpath = os.path.join(repo, "market.json")
    if not os.path.isfile(mpath):
        return {"state": "read_failed", "plugins": [], "err": "市场仓库中缺少 market.json"}
    try:
        with open(mpath, "r", encoding="utf-8") as f:
            market = json.load(f)
        if not isinstance(market, dict) or not isinstance(market.get("plugins"), list):
            return {"state": "read_failed", "plugins": [], "err": "market.json 格式无效：plugins 必须是数组"}
        if any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
               for item in market["plugins"]):
            return {"state": "read_failed", "plugins": [], "err": "market.json 格式无效：插件条目缺少名称"}
        market["state"] = "empty" if not market["plugins"] else "ready"
        return market
    except Exception as e:
        return {"state": "read_failed", "plugins": [], "err": "market.json 无法读取：%s" % str(e)[:80]}


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
        "state": market.get("state", "read_failed"),
        "state_message": {
            "not_configured": "尚未配置本地插件市场仓库",
            "read_failed": market.get("err") or "市场仓库读取失败",
            "empty": "市场仓库可用，但没有已发布插件",
            "ready": "插件市场已就绪",
        }.get(market.get("state"), "市场仓库读取失败"),
        "repo_configured": bool(_repo_path()),
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
    import re
    from plugin_structure import _filename_issue
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or _filename_issue(name):
        return {"ok": False, "err": "插件名不合法或为 Windows 保留名称"}

    # 在市场清单里找
    market = _read_market()
    meta = None
    for p in market.get("plugins", []):
        if p.get("name") == name:
            meta = p
            break
    if not meta:
        return {"ok": False, "err": "市场里找不到插件 %s" % name}

    import plugin_ops
    # Shared validator/transaction. Never import a new plugin during install.
    ok, result = plugin_ops.install_market_directory(
        _repo_path(), meta.get("path", "plugins/" + name), name)
    if ok:
        result["msg"] = "文件安装成功，请重启大脑生效"
    else:
        result["ok"] = False
    return result


def api_uninstall(body, query):
    """POST /api/market/uninstall —— 卸载插件（删除 brain/plugins/ 下的目录）。

    body: {"name": "插件名"}
    """
    name = str((body or {}).get("name") or "").strip()
    import re
    from plugin_structure import _filename_issue
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or _filename_issue(name):
        return {"ok": False, "err": "插件名不合法或为 Windows 保留名称"}

    # 只允许卸载目录型插件（市场里的都是目录型）
    dst = os.path.join(_plugins_dir(), name)
    import plugin_transaction
    try:
        plugin_transaction.reject_links(dst)
    except ValueError as exc:
        return {"ok": False, "err": str(exc)}
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
    if not repo:
        return {"ok": False, "state": "not_configured", "err": "尚未配置本地插件市场仓库"}
    if not os.path.isdir(repo):
        return {"ok": False, "state": "read_failed", "err": "配置的市场仓库目录不存在"}
    try:
        check = subprocess.run(
            ["git", "-C", repo, "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=10,
        )
        if check.returncode or check.stdout.strip() != "true":
            return {"ok": False, "state": "read_failed", "err": "配置的市场目录不是 Git 仓库"}
        result = subprocess.run(
            ["git", "-C", repo, "pull", "--ff-only"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = (result.stdout or "") + (result.stderr or "")
        refreshed = _read_market() if result.returncode == 0 else None
        return {"ok": result.returncode == 0,
                "state": refreshed.get("state") if refreshed else "read_failed",
                "state_message": refreshed.get("err") if refreshed else None,
                "output": output[:300]}
    except Exception as e:
        return {"ok": False, "state": "read_failed", "err": str(e)[:80]}


ROUTES = [
    ("GET", "/api/market/list", api_list),
    ("POST", "/api/market/install", api_install),
    ("POST", "/api/market/uninstall", api_uninstall),
    ("POST", "/api/market/refresh", api_refresh),
]
