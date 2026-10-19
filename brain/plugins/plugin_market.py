# -*- coding: utf-8 -*-
"""插件市场：浏览、安装、卸载远程插件。

市场源是本地 git 仓库（默认 F:\\vigil-plugins），market.json 列插件清单。
安装 = 从本地仓库复制插件目录到 brain/plugins/；卸载 = 删除目录。
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

NAME = "plugin_market"
DESC = "插件市场：浏览/安装/卸载插件"
FEATURE = "plugin_market"

# 显式配置 market_repo 时使用本地 Git 仓库；默认从公开 GitHub 市场读取。
DEFAULT_REPO = ""
REMOTE_REPO = "limingyi2/vigil"
REMOTE_REF = "dev"
REMOTE_MARKET_URL = "https://raw.githubusercontent.com/%s/%s/market.json" % (REMOTE_REPO, REMOTE_REF)
REMOTE_FILE_LIMIT = 2 * 1024 * 1024


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
    """运行时安装目录；市场插件不写入跟踪源码的 brain/plugins/。"""
    try:
        from paths import PLUGIN_INSTALL_DIR
        return str(PLUGIN_INSTALL_DIR)
    except Exception:
        return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "installed_plugins"))


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


def _fetch_bytes(url, limit):
    """仅从 HTTPS raw.githubusercontent.com 读取文件，并限制下载体积。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
        raise ValueError("插件市场只允许从 HTTPS GitHub Raw 源下载")
    request = Request(url, headers={"User-Agent": "Vigil-Plugin-Market/1.0"})
    with urlopen(request, timeout=6) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("远程文件超过大小限制")
    return data


def _read_remote_market():
    """默认公开目录；安装版不需要配置开发机上的本地 clone。"""
    try:
        market = json.loads(_fetch_bytes(REMOTE_MARKET_URL, 512 * 1024).decode("utf-8-sig"))
        if not isinstance(market, dict) or not isinstance(market.get("plugins"), list):
            return {"state": "read_failed", "plugins": [], "err": "GitHub market.json 格式无效"}
        if any(not isinstance(item, dict) or not isinstance(item.get("name"), str)
               for item in market["plugins"]):
            return {"state": "read_failed", "plugins": [], "err": "GitHub market.json 插件条目无效"}
        market["state"] = "empty" if not market["plugins"] else "ready"
        market["source_kind"] = "github"
        return market
    except Exception as exc:
        return {"state": "read_failed", "plugins": [], "source_kind": "github",
                "err": "GitHub 插件市场读取失败：%s" % str(exc)[:100]}


def _install_remote_directory(meta, name):
    """下载 market.json 指定的 GitHub 文件，校验 Git blob SHA-1 后走现有安装事务。"""
    source = meta.get("source")
    if not isinstance(source, dict) or source.get("type") != "github":
        return False, {"err": "市场条目没有可信的 GitHub 源"}
    repo = str(source.get("repo") or "")
    ref = str(source.get("ref") or "")
    relative = str(source.get("path") or meta.get("path") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        return False, {"err": "市场源仓库名无效"}
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", ref):
        return False, {"err": "市场源版本无效"}
    if (not relative or relative.startswith(("/", "\\", "~")) or ":" in relative
            or "\\" in relative or any(part in ("", ".", "..") for part in relative.split("/"))):
        return False, {"err": "市场源路径无效"}
    files = source.get("files")
    if not isinstance(files, dict) or not files or "__init__.py" not in files or len(files) > 64:
        return False, {"err": "市场源文件清单无效"}
    try:
        import plugin_ops
        with tempfile.TemporaryDirectory(prefix="vigil-market-source-") as temp_root:
            staged_source = os.path.join(temp_root, name)
            os.makedirs(staged_source, exist_ok=True)
            for filename, expected_sha in files.items():
                if (not isinstance(filename, str)
                        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", filename)
                        or not filename.endswith((".py", ".js", ".css", ".json", ".md", ".txt"))
                        or not isinstance(expected_sha, str)
                        or not re.fullmatch(r"[a-fA-F0-9]{40}", expected_sha)):
                    raise ValueError("市场源包含无效文件名或校验值")
                url = "https://raw.githubusercontent.com/%s/%s/%s/%s" % (
                    repo, quote(ref, safe=""), quote(relative, safe="/"), quote(filename, safe=""))
                data = _fetch_bytes(url, REMOTE_FILE_LIMIT)
                header = b"blob " + str(len(data)).encode("ascii") + b"\0"
                actual_sha = hashlib.sha1(header + data).hexdigest()
                if actual_sha.lower() != expected_sha.lower():
                    raise ValueError("文件校验失败：" + filename)
                with open(os.path.join(staged_source, filename), "wb") as stream:
                    stream.write(data)
            return plugin_ops.install_market_directory(temp_root, name, name)
    except Exception as exc:
        return False, {"err": "远程插件下载或校验失败：%s" % str(exc)[:120]}


def _read_market():
    """读取市场仓库的 market.json，并区分未配置、读取失败和空市场。"""
    repo = _repo_path()
    if not repo:
        return _read_remote_market()
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
        market["source_kind"] = "local"
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
        "repo_configured": bool(_repo_path()) or market.get("source_kind") == "github",
        "source": market.get("source_kind", "local"),
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
    # Never import newly downloaded code during the install transaction.
    repo = _repo_path()
    if repo:
        ok, result = plugin_ops.install_market_directory(
            repo, meta.get("path", "plugins/" + name), name)
    else:
        ok, result = _install_remote_directory(meta, name)
    if ok:
        result["msg"] = "插件已安装到独立运行时目录；重启本地 Brain 后生效"
        result["restart_needed"] = True
        result["name"] = name
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
        market = _read_remote_market()
        state = market.get("state", "read_failed")
        message = market.get("err") or (
            "插件市场已就绪" if state == "ready" else
            "市场可用，但没有已发布插件" if state == "empty" else
            "GitHub 插件市场读取失败")
        return {"ok": state in ("ready", "empty"), "state": state,
                "state_message": message, "source": "github"}
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
