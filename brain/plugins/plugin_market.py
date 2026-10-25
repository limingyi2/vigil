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
from urllib.error import HTTPError, URLError
import socket
import ssl

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


def _remote_diagnostic(exc):
    """Return safe, structured network/read diagnostics without request headers."""
    reason = getattr(exc, "reason", exc)
    message = str(reason)[:180]
    if isinstance(exc, HTTPError):
        return {"stage": "remote_market", "kind": "http_error", "status": int(exc.code), "message": "远程市场返回 HTTP %s" % exc.code}
    if isinstance(reason, socket.timeout) or "timed out" in message.lower() or "timeout" in message.lower():
        kind = "timeout"
    elif isinstance(reason, ssl.SSLError) or "certificate" in message.lower() or "ssl" in message.lower() or "tls" in message.lower():
        kind = "tls"
    elif isinstance(reason, socket.gaierror) or "name resolution" in message.lower() or "getaddrinfo" in message.lower():
        kind = "dns"
    elif "proxy" in message.lower() or "tunnel" in message.lower():
        kind = "proxy"
    elif isinstance(exc, (json.JSONDecodeError, UnicodeDecodeError)):
        kind = "invalid_manifest"
    elif isinstance(exc, ValueError) and ("超过大小限制" in message or "格式无效" in message or "SHA-256" in message):
        kind = "invalid_market_content"
    elif isinstance(exc, ValueError):
        kind = "remote_source_error"
    elif isinstance(exc, URLError):
        kind = "network"
    else:
        kind = "remote_error"
    return {"stage": "remote_market", "kind": kind, "message": message}


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
    # A reachable artifact CDN may still cache an old mutable market index.
    if _preferred_host["host"] and not parsed.path.endswith('/market.json'):
        urls.sort(key=lambda u: 0 if urlparse(u).hostname == _preferred_host["host"] else 1)
    return urls


def _fetch_bytes(url, limit, expected_sha=None):
    """按候选源依次尝试下载，任一源成功即返回并记位。限制下载体积。"""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("插件市场只允许从 HTTPS GitHub Raw 或其镜像下载")
    errors = []
    for candidate in _candidate_urls(url):
        headers = {"User-Agent": "Vigil-Plugin-Market/1.0"}
        if parsed.path.endswith('/market.json'):
            headers['Cache-Control'] = 'no-cache'
        request = Request(candidate, headers=headers)
        try:
            with urlopen(request, timeout=FETCH_TIMEOUT) as response:
                data = response.read(limit + 1)
        except Exception as exc:
            errors.append("%s：%s" % (urlparse(candidate).hostname, str(exc)[:60]))
            continue
        if len(data) > limit:
            raise ValueError("远程文件超过大小限制")
        if expected_sha and hashlib.sha256(data).hexdigest().lower() != expected_sha.lower():
            errors.append("%s：制品 SHA-256 校验失败（可能为旧缓存）" % urlparse(candidate).hostname)
            continue
        _preferred_host["host"] = urlparse(candidate).hostname
        return data
    raise ValueError("全部下载源下载或校验失败（%s）" % "; ".join(errors[:3]))


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
        if market.get("state") == "read_failed":
            market["diagnostic"] = {"stage": "remote_market", "kind": "invalid_manifest",
                                    "message": str(market.get("err") or "market.json validation failed")[:180]}
        if market.get("state") in ("ready", "empty"):
            _write_market_cache(market)
        return market
    except Exception as exc:
        diagnostic = _remote_diagnostic(exc)
        cached = _read_cached_market()
        if cached:
            cached["err"] = "在线读取失败，显示于 %s 验证的缓存：%s" % (
                cached["fetched_at"], str(exc)[:100])
            cached["diagnostic"] = diagnostic
            return cached
        return {"state": "read_failed", "plugins": [], "source_kind": "github",
                "err": "GitHub 插件市场读取失败：%s" % str(exc)[:100],
                "diagnostic": diagnostic}


def _install_remote_directory(meta, name, replace=False, managed=False):
    """Download a published package, verify artifact and Manifest hashes, then install transactionally."""
    artifact = meta.get("artifact")
    if not isinstance(artifact, dict):
        return False, {"err": "市场条目没有制品信息"}
    url, expected_sha = artifact.get("url"), artifact.get("sha256")
    if (not isinstance(url, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", str(expected_sha or ""))):
        return False, {"err": "市场制品 URL 或 SHA-256 无效"}
    stage = "下载制品"
    try:
        import plugin_ops
        with tempfile.TemporaryDirectory(prefix="vigil-market-source-") as temp_root:
            package = _fetch_bytes(url, REMOTE_PACKAGE_LIMIT, expected_sha=expected_sha)
            stage = "制品 SHA-256 校验"
            if hashlib.sha256(package).hexdigest().lower() != expected_sha.lower():
                raise ValueError("插件制品 SHA-256 校验失败")
            archive_path = os.path.join(temp_root, name + ".zip")
            with open(archive_path, "wb") as stream:
                stream.write(package)
            stage = "ZIP 路径与大小校验"
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
            stage = "Manifest 元数据校验"
            manifest = json.loads(files.pop("manifest.json").decode("utf-8"))
            if (manifest.get("schema") != "vigil.brain-plugin/v1" or manifest.get("name") != name
                    or manifest.get("version") != meta.get("version") or manifest.get("protocol") != PLUGIN_PROTOCOL
                    or manifest.get("protocol") != meta.get("protocol")
                    or manifest.get("permissions") != meta.get("permissions")):
                raise ValueError("Manifest 名称、版本、协议或权限与市场清单不一致")
            entry = manifest.get("entry")
            if entry != "__init__.py" or manifest.get("frontend") != {"script": "frontend.js", "style": "frontend.css"}:
                raise ValueError("Manifest 入口或前端资源不受支持")
            stage = "Manifest 文件清单校验"
            expected_files = manifest.get("files")
            if (not isinstance(expected_files, list) or any(
                    not isinstance(x, dict) or not isinstance(x.get("path"), str)
                    for x in expected_files)):
                raise ValueError("Manifest files 必须是文件记录列表")
            declared = [x['path'] for x in expected_files]
            if len(set(declared)) != len(declared):
                raise ValueError("Manifest 文件清单存在重复路径")
            if set(declared) != set(files):
                raise ValueError("Manifest 文件清单与制品内容不一致；未声明：%s；缺失：%s" % (
                    ", ".join(sorted(set(files) - set(declared))) or "无",
                    ", ".join(sorted(set(declared) - set(files))) or "无"))
            stage = "Manifest 文件 SHA-256 校验"
            for item in expected_files:
                if not isinstance(item, dict) or not re.fullmatch(r"[a-fA-F0-9]{64}", str(item.get("sha256") or "")):
                    raise ValueError("Manifest 文件 SHA-256 无效")
                payload = files[item["path"]]
                if hashlib.sha256(payload).hexdigest().lower() != item["sha256"].lower():
                    raise ValueError("Manifest 文件 SHA-256 校验失败：" + item["path"])
            # The generic installer performs static plugin structure checks and the normal atomic commit.
            stage = "静态校验与事务安装"
            if managed:
                import plugin_manager
                return plugin_manager.update(
                    name, archive_path,
                    source="market:%s@%s" % (name, meta["version"]),
                    manifest=manifest,
                    reload=True,
                )
            ok, result = plugin_ops.install(archive_path, note="market:%s@%s" % (name, meta["version"]), replace=replace)
            if ok:
                result["manifest"] = manifest
            else:
                result.update(stage=stage, err=stage + "失败：" + result.get("err", "未知错误"))
            return ok, result
    except Exception as exc:
        return False, {"err": "远程插件下载或校验失败：%s" % str(exc)[:120],
                       "stage": stage,
                       "diagnostic": dict(_remote_diagnostic(exc), stage="remote_artifact")}


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
        item["market_version"] = p.get("version", "")
        item["installed"] = p.get("name") in installed
        try:
            import plugin_manager
            item.update(plugin_manager.status(p.get("name"), p.get("version", "")))
        except Exception:
            pass
        plugins.append(item)
    # Also expose locally installed extensions that are no longer published in
    # the current market, so users can diagnose or remove them safely.
    try:
        import plugin_manager
        known = {str(p.get("name")) for p in plugins}
        desc_by_name: dict[str, str] = {}
        try:
            import plugin_host
            for row in plugin_host.listing():
                if row.get("name"):
                    desc_by_name[str(row["name"])] = str(row.get("desc") or "")
        except Exception:
            pass
        for item in plugin_manager.list_plugins(market.get("plugins", [])):
            if item.get("name") in known:
                continue
            # 🔴 这些条目没有市场元数据；别让界面显示成"未知作者/市场 v未知"。
            # 内置插件标明身份；版本、作者、描述尽量从运行时补齐。
            item["in_market"] = False
            if item.get("builtin") or item.get("source_kind") == "builtin":
                item["author"] = "Vigil 官方内置"
            item.setdefault("author", "未知")
            if not item.get("desc"):
                item["desc"] = desc_by_name.get(str(item.get("name")), "")
            plugins.append(item)
    except Exception:
        pass
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
        "diagnostic": market.get("diagnostic"),
        "plugins": plugins,
        "installed_count": sum(1 for item in plugins if item.get("installed")),
    }


def api_install(body, query):
    """POST /api/market/install —— 安装插件（从本地仓库复制到 brain/plugins/）。

    body: {"name": "插件名"}
    """
    name = str((body or {}).get("name") or "").strip()
    import plugin_ops
    import re
    from plugin_structure import _filename_issue
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name) or _filename_issue(name):
        return {"ok": False, "err": "插件名不合法或为 Windows 保留名称"}

    # 在市场清单里找
    market = _read_market()
    if market.get("state") not in ("ready", "cached_stale"):
        return {"ok": False, "err": market.get("err") or "插件市场当前不可用",
                "state": market.get("state"), "diagnostic": market.get("diagnostic")}
    meta = None
    for p in market.get("plugins", []):
        if p.get("name") == name:
            meta = p
            break
    if not meta:
        return {"ok": False, "err": "市场里找不到插件 %s" % name}

    import plugin_manager
    if plugin_manager._source(name) == "builtin":
        return {"ok": False, "name": name, "state": "builtin",
                "err": "这是随程序发布的内置插件，不能作为用户插件重复安装"}

    return install_entry(meta, name)


def install_entry(meta, name, *, replace=False, note_source=""):
    """安装一个**已经过市场清单校验**的条目，并把它装载进运行时。

    调用方必须先完成来源校验（市场清单查找或链接解析）。
    ``api_install`` 与链接安装共用这一段，避免两条链路的装后处理出现分叉：
    重复安装判定、依赖检查、制品哈希校验、事务提交、重载与运行时状态。
    """
    # Never import newly downloaded code during the install transaction.
    already = name in _list_local()
    if already:
        return {"ok": False, "name": name, "state": "already_installed",
                "err": "插件已安装，请使用更新操作", "installed_version":
                plugin_ops.read_registry().get("plugins", {}).get(name, {}).get("version", "")}
    try:
        import plugin_manager
        deps = plugin_manager._dependencies(meta, plugin_ops.read_registry())
        if deps:
            return {"ok": False, "name": name, "state": "error", "err": "依赖检查失败：" + "; ".join(deps)}
    except Exception:
        pass
    if not meta.get("artifact"):
        return {"ok": False, "err": "该条目没有可校验的制品（artifact）"}
    ok, result = _install_remote_directory(meta, name, replace=replace)
    if ok:
        try:
            import plugin_manager
            plugin_manager._update_meta(name, result.get("manifest") or meta,
                                        source=note_source or "market:%s@%s" % (
                                            name, meta.get("version", "")))
            reload_result = plugin_manager.reload(name)
            result["runtime_state"] = ("loaded" if reload_result.get("ok")
                                       else reload_result.get("state", "pending"))
            if reload_result.get("restart_needed"):
                result["restart_needed"] = True
                result["state"] = "restart_required"
            if not reload_result.get("ok"):
                result["runtime_warning"] = reload_result.get("err", "重载失败")
            else:
                result["restart_needed"] = False
        except Exception as exc:
            result["runtime_warning"] = "安装后重载失败：%s" % str(exc)[:120]
            result["runtime_state"] = "error"
        result["msg"] = ("插件已安装并加载" if result.get("runtime_state") == "loaded"
                          else "插件已安装；需要重启 Brain 后生效" if result.get("restart_needed")
                          else "插件已安装，但运行时加载失败")
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

    import plugin_manager
    source_before = plugin_manager._source(name)
    ok, result = plugin_manager.uninstall(name, purge_data=False)
    if ok:
        if result.get("builtin_restored"):
            message = "用户版本已卸载，已恢复内置版本；用户配置和素材保留"
        else:
            message = "插件已卸载；用户配置和素材保留"
        result.update(ok=True, name=name, source_before=source_before, msg=message)
    else:
        result["ok"] = False
    return result


def api_update(body, query):
    """POST /api/market/update —— 下载并原子替换市场版本。"""
    name = str((body or {}).get("name") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", name):
        return {"ok": False, "err": "插件名不合法"}
    market = _read_market()
    if market.get("state") not in ("ready", "cached_stale"):
        return {"ok": False, "state": market.get("state"), "err": market.get("err") or "插件市场当前不可用"}
    meta = next((p for p in market.get("plugins", []) if p.get("name") == name), None)
    if not meta:
        return {"ok": False, "err": "市场里找不到插件 %s" % name}
    import plugin_manager
    source = plugin_manager._source(name)
    if source == "builtin":
        return {"ok": False, "name": name, "state": "builtin",
                "err": "内置插件不能通过市场替换；请更新 Vigil 程序"}
    if source != "user" or name not in _list_local():
        return {"ok": False, "state": "not_installed", "err": "插件尚未安装，请先安装"}
    import plugin_ops
    deps = plugin_manager._dependencies(meta, plugin_ops.read_registry())
    if deps:
        return {"ok": False, "name": name, "state": "error", "err": "依赖检查失败：" + "; ".join(deps)}
    # Reuse the market verifier, then delegate replacement and rollback to the
    # unified manager instead of maintaining a second lifecycle transaction.
    ok, result = _install_remote_directory(meta, name, replace=True, managed=True)
    result["ok"] = bool(ok)
    return result


def _reload_plugins():
    """热重载插件目录。失败静默，重启后仍会生效。"""
    try:
        # Keep market refreshes on the same lifecycle coordinator as install,
        # update and uninstall. This preserves the running Brain and validates
        # start(brain) hooks instead of merely replacing module objects.
        import plugin_manager
        result = plugin_manager.reload()
        if not result.get("ok"):
            raise RuntimeError(result.get("err") or "统一插件重载未完成")
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
                "state_message": message, "source": "github",
                "diagnostic": market.get("diagnostic")}
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


def api_lifecycle(body, query):
    """POST /api/market/lifecycle — unified enable/disable/reload/status."""
    import plugin_manager
    body = body or {}
    name = str(body.get("name") or "").strip()
    action = str(body.get("action") or "status").strip().lower()
    if action == "status":
        return {"ok": True, "plugin": plugin_manager.status(name)}
    if action == "enable":
        ok, result = plugin_manager.enable(name)
    elif action == "disable":
        ok, result = plugin_manager.disable(name)
    elif action == "reload":
        result = plugin_manager.reload(name)
        ok = bool(result.get("ok"))
    elif action == "uninstall":
        ok, result = plugin_manager.uninstall(name, purge_data=bool(body.get("purge_data")))
    else:
        return {"ok": False, "err": "不支持的生命周期操作"}
    result["ok"] = bool(ok)
    return result


ROUTES = [
    ("GET", "/api/market/list", api_list),
    ("POST", "/api/market/install", api_install),
    ("POST", "/api/market/update", api_update),
    ("POST", "/api/market/uninstall", api_uninstall),
    ("POST", "/api/market/refresh", api_refresh),
    ("POST", "/api/market/lifecycle", api_lifecycle),
]
