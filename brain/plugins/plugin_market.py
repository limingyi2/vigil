# -*- coding: utf-8 -*-
"""插件市场：浏览、安装、卸载远程插件。

市场源是本地 git 仓库（默认 F:\\vigil-plugins），market.json 列插件清单。
安装 = 从本地仓库复制插件目录到 brain/plugins/；卸载 = 删除目录。
"""
import hashlib
import io
import json
import os
import re
import subprocess
import tempfile
import zipfile
from urllib.parse import urlparse
from urllib.request import Request, urlopen

NAME = "plugin_market"
DESC = "插件市场：浏览/安装/卸载插件"
FEATURE = "plugin_market"

# 显式配置 market_repo 时使用本地 Git 仓库；默认从公开 GitHub 市场读取。
DEFAULT_REPO = ""
REMOTE_REPO = "limingyi2/vigil-plugins"
REMOTE_REF = "main"
REMOTE_MARKET_URL = "https://raw.githubusercontent.com/%s/%s/market.json" % (REMOTE_REPO, REMOTE_REF)
REMOTE_PACKAGE_LIMIT = 4 * 1024 * 1024
REMOTE_PACKAGE_FILES_LIMIT = 64
PLUGIN_PROTOCOL = {"name": "vigil-brain-plugin", "major": 1}
ALLOWED_PERMISSIONS = {"wallpaper.workshop.read", "wallpaper.config.write", "wallpaper.media.read"}

# GitHub Raw 裸连在国内基本不可达。下载走镜像优先、逐源回退；安全不靠域名靠
# SHA-256——清单与制品内容都做哈希强校验，镜像被篡改会在校验步被拦下。
RAW_HOST = "raw.githubusercontent.com"
MIRROR_HOSTS = ("cdn.jsdelivr.net", "fastly.jsdelivr.net", "testingcf.jsdelivr.net", "ghfast.top")
ALLOWED_HOSTS = {RAW_HOST} | set(MIRROR_HOSTS)
FETCH_TIMEOUT = 8
# 进程内记住最近一次成功的源，后续下载（含插件包）直接排最前，不再逐个试错。
_preferred_host = {"host": ""}


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


def _market_cache_path():
    """Validated remote listing cache. Its timestamp is kept separate from source metadata."""
    try:
        from paths import CONFIG_DIR
        return os.path.join(str(CONFIG_DIR), "plugin_market_cache.json")
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".vigil", "plugin_market_cache.json")


def _read_cached_market():
    try:
        with open(_market_cache_path(), "r", encoding="utf-8") as stream:
            cache = json.load(stream)
        market = cache.get("market")
        fetched_at = cache.get("fetched_at")
        if not isinstance(fetched_at, str) or not isinstance(market, dict):
            return None
        checked = _validate_market(market, "github-cache")
        if checked.get("state") not in ("ready", "empty"):
            return None
        checked["state"] = "cached_stale"
        checked["fetched_at"] = fetched_at
        return checked
    except Exception:
        return None


def _write_market_cache(market):
    """Persist only a freshly downloaded and fully validated listing."""
    try:
        from datetime import datetime, timezone
        path = _market_cache_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = json.dumps({
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "market": {k: v for k, v in market.items() if k not in ("state", "source_kind")},
        }, ensure_ascii=False, indent=2)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    except Exception as exc:
        print("[插件市场] 已验证市场缓存写入失败：%s" % str(exc)[:80], flush=True)


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


def _candidate_urls(url):
    """GitHub Raw 地址 → 候选下载地址列表。

    ghfast.top 排最前：它是 raw 的实时反代，读到的永远是最新提交 ——
    jsdelivr 对 @main 分支引用有最长 12 小时的缓存，market.json 刚更新时
    三家 jsdelivr 会一直吐旧清单，把实时源排在前面避免"市场更新了客户端
    半天看不见"。下载安全不靠域名靠 SHA-256，镜像被篡改会在校验步被拦下。
    """
    parsed = urlparse(url)
    urls = [url]
    if parsed.scheme == "https" and parsed.hostname == RAW_HOST:
        segs = parsed.path.lstrip("/").split("/", 3)
        if len(segs) == 4 and all(segs[:3]):
            user, repo, ref, path = segs
            urls = (["https://%s/%s" % (MIRROR_HOSTS[3], url)]
                    + ["https://%s/gh/%s/%s@%s/%s" % (m, user, repo, ref, path)
                       for m in MIRROR_HOSTS[:3]]
                    + [url])
    if _preferred_host["host"]:
        urls.sort(key=lambda u: 0 if urlparse(u).hostname == _preferred_host["host"] else 1)
    return urls


def _fetch_bytes(url, limit):
    """按候选源依次尝试下载，任一源成功即返回并记位。限制下载体积。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("插件市场只允许从 HTTPS GitHub Raw 或其镜像下载")
    errors = []
    for candidate in _candidate_urls(url):
        request = Request(candidate, headers={"User-Agent": "Vigil-Plugin-Market/1.0"})
        try:
            with urlopen(request, timeout=FETCH_TIMEOUT) as response:
                data = response.read(limit + 1)
        except Exception as exc:
            errors.append("%s：%s" % (urlparse(candidate).hostname, str(exc)[:60]))
            continue
        if len(data) > limit:
            raise ValueError("远程文件超过大小限制")
        _preferred_host["host"] = urlparse(candidate).hostname
        return data
    raise ValueError("全部下载源不可达（%s）" % "; ".join(errors[:3]))


def _validate_market(market, source_kind):
    if not isinstance(market, dict) or not isinstance(market.get("plugins"), list):
        return {"state": "read_failed", "plugins": [], "err": "market.json 格式无效"}
    for item in market["plugins"]:
        if not isinstance(item, dict) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", str(item.get("name") or "")):
            return {"state": "read_failed", "plugins": [], "err": "market.json 插件条目无效"}
        artifact = item.get("artifact")
        if not isinstance(artifact, dict) or not isinstance(artifact.get("url"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", str(artifact.get("sha256") or "")):
            return {"state": "read_failed", "plugins": [], "err": "市场条目缺少有效的 SHA-256 制品信息"}
        parsed = urlparse(artifact["url"])
        if parsed.scheme != "https" or parsed.hostname != "raw.githubusercontent.com":
            return {"state": "read_failed", "plugins": [], "err": "市场制品地址必须使用 HTTPS GitHub Raw"}
        if item.get("protocol") != PLUGIN_PROTOCOL:
            return {"state": "read_failed", "plugins": [], "err": "市场插件协议不兼容"}
        perms = item.get("permissions")
        if not isinstance(perms, list) or any(p not in ALLOWED_PERMISSIONS for p in perms):
            return {"state": "read_failed", "plugins": [], "err": "市场插件权限清单无效或未获支持"}
    market["state"] = "empty" if not market["plugins"] else "ready"
    market["source_kind"] = source_kind
    return market


def _read_remote_market():
    """默认公开目录；安装版不需要配置开发机上的本地 clone。"""
    try:
        market = json.loads(_fetch_bytes(REMOTE_MARKET_URL, 512 * 1024).decode("utf-8-sig"))
        market = _validate_market(market, "github")
        if market.get("state") in ("ready", "empty"):
            _write_market_cache(market)
        return market
    except Exception as exc:
        cached = _read_cached_market()
        if cached:
            cached["err"] = "在线读取失败，显示于 %s 验证的缓存：%s" % (
                cached["fetched_at"], str(exc)[:100])
            return cached
        return {"state": "read_failed", "plugins": [], "source_kind": "github",
                "err": "GitHub 插件市场读取失败：%s" % str(exc)[:100]}


def _install_remote_directory(meta, name):
    """Download a published package, verify artifact and Manifest hashes, then install transactionally."""
    artifact = meta.get("artifact")
    if not isinstance(artifact, dict):
        return False, {"err": "市场条目没有制品信息"}
    url, expected_sha = artifact.get("url"), artifact.get("sha256")
    if (not isinstance(url, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", str(expected_sha or ""))):
        return False, {"err": "市场制品 URL 或 SHA-256 无效"}
    try:
        import plugin_ops
        with tempfile.TemporaryDirectory(prefix="vigil-market-source-") as temp_root:
            package = _fetch_bytes(url, REMOTE_PACKAGE_LIMIT)
            if hashlib.sha256(package).hexdigest().lower() != expected_sha.lower():
                raise ValueError("插件制品 SHA-256 校验失败")
            archive_path = os.path.join(temp_root, name + ".zip")
            with open(archive_path, "wb") as stream:
                stream.write(package)
            with zipfile.ZipFile(io.BytesIO(package)) as archive:
                infos = archive.infolist()
                if not infos or len(infos) > REMOTE_PACKAGE_FILES_LIMIT:
                    raise ValueError("插件制品文件数量无效")
                files = {}
                unpacked_size = 0
                for info in infos:
                    path = info.filename
                    if (info.is_dir() or not path.startswith(name + "/")
                            or path.startswith(("/", "\\")) or "\\" in path
                            or any(p in ("", ".", "..") for p in path.split("/"))
                            or ((info.external_attr >> 16) & 0o170000) == 0o120000):
                        raise ValueError("插件制品包含无效路径")
                    path = path[len(name) + 1:]
                    if not path or path in files or ":" in path:
                        raise ValueError("插件制品包含重复或无效文件名")
                    if info.file_size > 2 * 1024 * 1024:
                        raise ValueError("插件制品单文件超过大小限制")
                    unpacked_size += info.file_size
                    if unpacked_size > 16 * 1024 * 1024:
                        raise ValueError("插件制品解压后超过大小限制")
                    files[path] = archive.read(info)
            manifest = json.loads(files.pop("manifest.json").decode("utf-8"))
            if (manifest.get("schema") != "vigil.brain-plugin/v1" or manifest.get("name") != name
                    or manifest.get("version") != meta.get("version") or manifest.get("protocol") != PLUGIN_PROTOCOL
                    or manifest.get("protocol") != meta.get("protocol")
                    or manifest.get("permissions") != meta.get("permissions")):
                raise ValueError("Manifest 名称、版本、协议或权限与市场清单不一致")
            entry = manifest.get("entry")
            if entry != "__init__.py" or manifest.get("frontend") != {"script": "frontend.js", "style": "frontend.css"}:
                raise ValueError("Wallpaper Manifest 入口或前端资源不受支持")
            expected_files = manifest.get("files")
            if not isinstance(expected_files, list) or {x.get("path") for x in expected_files if isinstance(x, dict)} != set(files):
                raise ValueError("Manifest 文件清单与制品内容不一致")
            for item in expected_files:
                if not isinstance(item, dict) or not re.fullmatch(r"[a-fA-F0-9]{64}", str(item.get("sha256") or "")):
                    raise ValueError("Manifest 文件 SHA-256 无效")
                payload = files[item["path"]]
                if hashlib.sha256(payload).hexdigest().lower() != item["sha256"].lower():
                    raise ValueError("Manifest 文件 SHA-256 校验失败：" + item["path"])
            # The generic installer performs static plugin structure checks and the normal atomic commit.
            ok, result = plugin_ops.install(archive_path, note="market:%s@%s" % (name, meta["version"]))
            if ok:
                result["manifest"] = manifest
            return ok, result
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
        return _validate_market(market, "local")
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
            "cached_stale": market.get("err") or "当前显示离线缓存，尚未验证在线市场状态",
            "empty": "市场仓库可用，但没有已发布插件",
            "ready": "插件市场已就绪",
        }.get(market.get("state"), "市场仓库读取失败"),
        "repo_configured": bool(_repo_path()) or market.get("source_kind") == "github",
        "source": market.get("source_kind", "local"),
        "market_version": market.get("version", ""),
        "updated": market.get("updated", ""),
        "fetched_at": market.get("fetched_at", ""),
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

    # Never import newly downloaded code during the install transaction.
    repo = _repo_path()
    if repo:
        ok, result = _install_remote_directory(meta, name) if meta.get("artifact") else (False, {"err": "本地市场条目缺少可校验制品"})
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
    import plugin_ops
    ok, result = plugin_ops.uninstall(name, purge_data=False)
    if ok:
        result.update(ok=True, name=name, msg="插件已卸载；用户配置和素材保留")
    else:
        result["ok"] = False
    return result


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
