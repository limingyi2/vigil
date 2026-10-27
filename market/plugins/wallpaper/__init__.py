# -*- coding: utf-8 -*-
"""Vigil Wallpaper Engine plugin v4.

The plugin owns discovery and read-only media access. Rendering is shipped in the
plugin frontend and runs in a sandboxed iframe; the Electron host only provides the
same generic, expiring resource capability used by every media plugin.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import threading
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

NAME = "wallpaper"
VERSION = "4.2.0"
DESC = "独立 Wallpaper Engine 壁纸：Scene/WebGL、视频、网页，另有内置与在线动态壁纸"
FEATURE = "wallpaper"

# v4 deliberately uses a new config/cache namespace. Existing v3 files and user
# Workshop/imported media are never rewritten or removed during install/uninstall.
DATA_DIR = Path(os.path.expanduser("~")) / ".vigil" / "wallpaper-v4"
CONFIG_FILE = DATA_DIR / "config.json"
CACHE_FILE = DATA_DIR / "library.json"
_CACHE_TTL = 20.0

_DEFAULT_CONFIG = {
    "enabled": False,
    "wallpaper_id": None,
    "wallpaper_type": None,
    "wallpaper_file": None,
    "opacity": 1.0,
    "scrim": 0.0,
    "blur": 0,
    "fit": "cover",
    "fps": 60,
    "muted": True,
    "quality": "high",
    "transparent": True,
    "we_path": None,
    # dshwe「壁纸库与自动轮播」吸收版:轮播由客户端定时器驱动,配置持久化
    "carousel_enabled": False,
    "carousel_minutes": 30,
    "carousel_shuffle": False,
}


def _load_config() -> dict[str, Any]:
    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("config must be an object")
    except Exception:
        raw = {}
    cfg = dict(_DEFAULT_CONFIG)
    for key, value in raw.items():
        if key in cfg:
            cfg[key] = value
    # 4.1.7：旧版默认 opacity 0.52 / scrim 0.24 会给整窗蒙一层雾，一次性
    # 重置这两项（其余用户自定设置保留），标记文件避免重复打扰。
    marker = DATA_DIR / ".migrated-4.1.7"
    if not marker.exists():
        cfg["opacity"] = 1.0
        cfg["scrim"] = 0.0
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            marker.write_text("ok", encoding="utf-8")
            _save_config(cfg)
        except OSError:
            pass
    cfg["wallpaper_type"] = _norm_type(cfg.get("wallpaper_type")) or None
    cfg["fit"] = cfg["fit"] if cfg["fit"] in {"cover", "contain", "stretch"} else "cover"
    cfg["fps"] = _bounded_int(cfg.get("fps"), 15, 120, 60)
    cfg["opacity"] = _bounded_float(cfg.get("opacity"), 0.0, 1.0, 0.52)
    cfg["scrim"] = _bounded_float(cfg.get("scrim"), 0.0, 1.0, 0.24)
    cfg["blur"] = _bounded_int(cfg.get("blur"), 0, 32, 0)
    cfg["quality"] = cfg["quality"] if cfg["quality"] in {"low", "medium", "high"} else "high"
    cfg["carousel_minutes"] = _bounded_int(cfg.get("carousel_minutes"), 5, 720, 30)
    return cfg


def _save_config(cfg: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_FILE)


def _bounded_float(value: Any, low: float, high: float, fallback: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return fallback


def _bounded_int(value: Any, low: int, high: int, fallback: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return fallback


def _norm_type(value: Any) -> str:
    value = str(value or "").strip().lower()
    aliases = {"scene": "scene", "video": "video", "web": "web", "image": "image", "gif": "gif"}
    return aliases.get(value, value)


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    configured = os.environ.get("VIGIL_WALLPAPER_STEAM_ROOT", "")
    roots.extend(Path(p.strip()) for p in re.split(r"[,;]", configured) if p.strip())
    if os.name == "nt":
        try:
            import winreg  # type: ignore
            for root, subkey, name in (
                (winreg.HKEY_CURRENT_USER, r"Software\\Valve\\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\\WOW6432Node\\Valve\\Steam", "InstallPath"),
            ):
                try:
                    with winreg.OpenKey(root, subkey) as key:
                        value, _ = winreg.QueryValueEx(key, name)
                        if value:
                            roots.append(Path(value))
                except OSError:
                    pass
        except Exception:
            pass
    roots.extend(Path(p) for p in (
        r"C:\\Program Files (x86)\\Steam", r"C:\\Program Files\\Steam",
        r"D:\\Steam", r"D:\\SteamLibrary", r"E:\\SteamLibrary", r"F:\\SteamLibrary",
    ))
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        key = os.path.normcase(os.path.abspath(str(root)))
        if key in seen:
            continue
        seen.add(key)
        out.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        try:
            text = vdf.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for match in re.finditer(r'"path"\s+"([^"]+)"', text):
            out.append(Path(match.group(1).replace("\\\\", os.sep).replace("\\", os.sep)))
    return out


def _find_workshop() -> Path | None:
    cfg = _load_config()
    configured = cfg.get("we_path")
    if isinstance(configured, str) and configured:
        path = Path(configured).expanduser()
        if path.is_dir():
            return path
    for root in _steam_roots():
        path = root / "steamapps" / "workshop" / "content" / "431960"
        if path.is_dir():
            return path
    return None


def _preview_for(folder: Path) -> str | None:
    for name in ("preview.jpg", "preview.png", "preview.jpeg", "preview.gif", "preview.webp"):
        if (folder / name).is_file():
            return name
    try:
        for item in sorted(folder.iterdir()):
            if item.is_file() and item.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                return item.name
    except OSError:
        pass
    return None


def _entry_for(folder: Path) -> dict[str, Any] | None:
    if not folder.is_dir() or not re.fullmatch(r"\d+", folder.name):
        return None
    project = folder / "project.json"
    data: dict[str, Any] = {}
    try:
        if project.is_file():
            loaded = json.loads(project.read_text(encoding="utf-8", errors="ignore"))
            if isinstance(loaded, dict):
                data = loaded
    except (OSError, ValueError):
        data = {}
    kind = _norm_type(data.get("type"))
    candidates: list[str] = []
    if kind == "scene":
        candidates = ["scene.pkg", "scene.json"]
    elif kind == "web":
        candidates = ["index.html"]
    elif kind == "video":
        candidates = [p.name for p in sorted(folder.iterdir()) if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".avi", ".mov"}]
    elif kind in {"image", "gif"}:
        candidates = [p.name for p in sorted(folder.iterdir()) if p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".gif"}]
    if not kind:
        if (folder / "index.html").is_file():
            kind, candidates = "web", ["index.html"]
        else:
            candidates = [p.name for p in sorted(folder.iterdir()) if p.suffix.lower() in {".mp4", ".webm", ".png", ".jpg", ".jpeg", ".webp", ".gif"}]
            if candidates:
                kind = "video" if Path(candidates[0]).suffix.lower() in {".mp4", ".webm", ".mkv", ".avi", ".mov"} else "image"
    file_name = next((name for name in candidates if (folder / name).is_file()), None)
    if kind == "application" or not file_name:
        return None
    return {
        "id": folder.name,
        "title": str(data.get("title") or data.get("name") or folder.name)[:240],
        "type": kind,
        "file": file_name,
        "preview": _preview_for(folder),
        "rating": str(data.get("contentrating") or "Everyone")[:40],
        "author": str(data.get("author") or "")[:120],
    }


def _scan(force: bool = False) -> tuple[list[dict[str, Any]], str | None]:
    import time
    if not force:
        try:
            cached = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            if time.time() - float(cached.get("ts", 0)) < _CACHE_TTL:
                return list(cached.get("items") or []), cached.get("we_path")
        except Exception:
            pass
    workshop = _find_workshop()
    items: list[dict[str, Any]] = []
    if workshop:
        try:
            for folder in sorted(workshop.iterdir(), key=lambda p: p.name):
                item = _entry_for(folder)
                if item:
                    items.append(item)
        except OSError:
            pass
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({"ts": time.time(), "we_path": str(workshop) if workshop else None, "items": items}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return items, str(workshop) if workshop else None


def api_library(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    query = query or {}
    items, path = _scan(str(query.get("refresh", "")).lower() in {"1", "true", "yes"})
    allowed = {x.strip().lower() for x in str(query.get("type", "")).split(",") if x.strip()}
    if allowed:
        items = [x for x in items if x["type"] in allowed]
    return {"ok": True, "version": VERSION, "we_path": path, "count": len(items), "wallpapers": items}


def _safe_item(query: dict[str, str]) -> tuple[Path, str] | tuple[None, None]:
    wid = str((query or {}).get("id", ""))
    file_name = str((query or {}).get("file", ""))
    if not re.fullmatch(r"\d+", wid) or not file_name or "\\" in file_name or "\x00" in file_name:
        return None, None
    parts = Path(file_name).parts
    if file_name.startswith(("/", "\\")) or ".." in parts:
        return None, None
    # 在线壁纸优先：它们落在 DATA_DIR/cloud，不在 Workshop 目录里。
    # 文件名必须与清单声明完全一致，避免借 id 越权读同目录的其他文件。
    cloud = next((x for x in _cloud_items() if x["id"] == wid), None)
    if cloud:
        if file_name != cloud["file"]:
            return None, None
        cached = _cloud_cached(cloud)
        return (cached, file_name) if cached else (None, None)
    workshop = _find_workshop()
    if not workshop:
        return None, None
    root = (Path(workshop) / wid).resolve()
    target = (root / file_name).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None, None
    if not target.is_file() or target.is_symlink():
        return None, None
    return target, file_name


def api_resource(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    path, file_name = _safe_item(query or {})
    if path is None:
        return {"ok": False, "err": "壁纸资源不存在或路径非法", "_code": 404}
    mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    return {"__file__": str(path), "__mime__": mime, "__cache__": "no-store"}


# ── 在线壁纸：按需下载，不随插件打包 ─────────────────────────────────
#
# 插件市场安装包有 4 MB 硬上限（brain/plugin_ops.py 的 MAX_ZIP_BYTES），视频壁纸
# 不可能塞进去。所以插件只装「清单 + 播放器」：画面放在 HTTPS 主机上，用户点一下
# 才下载，校验 SHA-256 后落盘，之后离线可用。
# 播放走 Brain 的 __file__ 流式响应（plugin_files.send_file），它已经实现 HTTP
# Range 206，所以 <video> 既能 seek 也能无缝循环。
CLOUD_DIR = DATA_DIR / "cloud"
CLOUD_MANIFEST = Path(os.environ.get("VIGIL_WALLPAPER_CLOUD_MANIFEST")
                      or (Path(__file__).with_name("cloud-walls.json")))
# 只允许从这几个主机取画面。清单本身随插件分发、由市场校验 SHA-256，
# 所以这里是第二道闸，不是唯一闸。
# GitHub Release 的下载是两跳：github.com/<repo>/releases/download/... 先 302 到
# release-assets.githubusercontent.com（2026-10 实测），raw 分支才是
# objects.githubusercontent.com。跳转目标不在白名单会被下面 geturl() 拒掉，
# 所以两跳的目标主机都必须列进来。jsDelivr 作备源（仓库文件 <100MB 时可用）。
CLOUD_HOSTS = {
    "github.com",
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
    "raw.githubusercontent.com",
    "cdn.jsdelivr.net",
}
# 生产只走 HTTPS。测试会把它换成 {"http","https"} 以便用本地服务跑真下载；
# 这不是后门——清单里的 URL 仍要过主机白名单和 SHA-256 双闸。
CLOUD_SCHEMES = {"https"}
CLOUD_MAX_BYTES = 256 * 1024 * 1024    # 单个壁纸上限
CLOUD_CHUNK = 256 * 1024               # 边下边算哈希的块大小
CLOUD_TIMEOUT = 20
CLOUD_FREE_MARGIN = 64 * 1024 * 1024   # 落盘前要求的最小剩余空间

_CLOUD_LOCK = threading.Lock()
_CLOUD_STATE: dict[str, dict[str, Any]] = {}
_CLOUD_QUEUE: list[str] = []
_CLOUD_WORKER: threading.Thread | None = None
_cloud_manifest_cache: tuple[float, list[dict[str, Any]]] | None = None


def _clean_cloud_entry(entry: Any) -> dict[str, Any] | None:
    """清单条目是最不可信的输入：id/文件名/URL/哈希/体积全部重新校验。"""
    if not isinstance(entry, dict):
        return None
    wid = str(entry.get("id") or "").strip()
    name = str(entry.get("file") or "").strip()
    url = str(entry.get("url") or "").strip()
    sha = str(entry.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"\d+", wid) or not name:
        return None
    if "/" in name or "\\" in name or name.startswith(".") or "\x00" in name:
        return None
    if not re.fullmatch(r"[a-f0-9]{64}", sha):
        return None
    try:
        size = int(entry.get("bytes") or 0)
    except (TypeError, ValueError):
        return None
    if not 0 < size <= CLOUD_MAX_BYTES:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in CLOUD_SCHEMES or (parsed.hostname or "").lower() not in CLOUD_HOSTS:
        return None
    kind = _norm_type(entry.get("type")) or "video"
    if kind not in {"video", "image", "gif"}:
        return None
    return {
        "id": wid,
        "title": str(entry.get("title") or wid)[:120],
        "type": kind,
        "file": name,
        "url": url,
        "sha256": sha,
        "bytes": size,
        "poster": str(entry.get("poster") or "")[:200000],
        "credit": str(entry.get("credit") or "")[:160],
    }


def _cloud_items() -> list[dict[str, Any]]:
    """读清单；按 mtime 缓存，避免每次请求都解析 JSON。"""
    global _cloud_manifest_cache
    try:
        stamp = CLOUD_MANIFEST.stat().st_mtime
    except OSError:
        return []
    if _cloud_manifest_cache and _cloud_manifest_cache[0] == stamp:
        return _cloud_manifest_cache[1]
    items: list[dict[str, Any]] = []
    try:
        raw = json.loads(CLOUD_MANIFEST.read_text(encoding="utf-8-sig"))
    except Exception:
        raw = {}
    entries = raw.get("items") if isinstance(raw, dict) else None
    for entry in entries if isinstance(entries, list) else []:
        item = _clean_cloud_entry(entry)
        if item and all(x["id"] != item["id"] for x in items):
            items.append(item)
    _cloud_manifest_cache = (stamp, items)
    return items


def _cloud_cached(item: dict[str, Any]) -> Path | None:
    """已落盘的副本；路径必须仍在 CLOUD_DIR 内（清单里的 file 不可信）。"""
    try:
        root = CLOUD_DIR.resolve()
        path = (CLOUD_DIR / item["file"]).resolve()
        path.relative_to(root)
    except (ValueError, OSError):
        return None
    return path if path.is_file() else None


def _cloud_view(item: dict[str, Any]) -> dict[str, Any]:
    out = {k: item[k] for k in ("id", "title", "type", "file", "bytes", "poster", "credit")}
    out["total"] = item["bytes"]
    # 卡片缩略图沿用本地条目的字段名 preview：清单里内嵌一张小 data URI，
    # 下载之前就能看见画面，不必为"挑哪张"先下几百 MB。
    out["preview"] = item["poster"]
    cached = _cloud_cached(item)
    if cached:
        out.update(state="cached", got=cached.stat().st_size, percent=100)
        return out
    with _CLOUD_LOCK:
        st = dict(_CLOUD_STATE.get(item["id"]) or {})
    state = st.get("state")
    if state in {"queued", "downloading"}:
        got = int(st.get("got") or 0)
        out.update(state=state, got=got,
                   percent=int(got * 100 / item["bytes"]) if item["bytes"] else 0)
    elif state == "error":
        out.update(state="error", got=0, percent=0, err=str(st.get("err") or ""))
    elif state == "done":
        # 状态说完成但文件不在：被杀进程/被清盘，退回可重新下载。
        out.update(state="remote", got=0, percent=0)
    else:
        out.update(state="remote", got=0, percent=0)
    return out


def _cloud_set(wid: str, **fields: Any) -> None:
    with _CLOUD_LOCK:
        current = dict(_CLOUD_STATE.get(wid) or {})
        current.update(fields)
        _CLOUD_STATE[wid] = current


def _cloud_fetch_one(item: dict[str, Any]) -> None:
    """流式下载到 .part，边下边算 SHA-256，校验通过才 rename 成正式文件。"""
    wid = item["id"]
    dest = CLOUD_DIR / item["file"]
    part = dest.with_name(dest.name + ".part")
    _cloud_set(wid, state="downloading", got=0, err="")
    try:
        CLOUD_DIR.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(str(CLOUD_DIR)).free
        if free < item["bytes"] + CLOUD_FREE_MARGIN:
            raise ValueError("磁盘空间不足，至少需要 %d MB 空闲"
                             % ((item["bytes"] + CLOUD_FREE_MARGIN) // 1048576))
        hasher = hashlib.sha256()
        got = 0
        request = urllib.request.Request(item["url"], headers={
            "User-Agent": "Vigil-Wallpaper/4.2", "Accept-Encoding": "identity"})
        with urllib.request.urlopen(request, timeout=CLOUD_TIMEOUT) as response, \
                open(part, "wb") as handle:
            final = urllib.parse.urlparse(response.geturl())
            if final.scheme not in CLOUD_SCHEMES or (final.hostname or "").lower() not in CLOUD_HOSTS:
                raise ValueError("下载被重定向到未授权主机：%s" % (final.hostname or "?"))
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > item["bytes"] + 4096:
                raise ValueError("远端文件比清单声明更大")
            while True:
                chunk = response.read(CLOUD_CHUNK)
                if not chunk:
                    break
                got += len(chunk)
                if got > item["bytes"] + 4096:
                    raise ValueError("下载体积超过清单声明")
                hasher.update(chunk)
                handle.write(chunk)
                _cloud_set(wid, state="downloading", got=got)
        if got != item["bytes"]:
            raise ValueError("体积不符：清单 %d 字节，实际 %d" % (item["bytes"], got))
        if hasher.hexdigest().lower() != item["sha256"]:
            raise ValueError("SHA-256 校验失败（托管文件被改动，或清单未同步）")
        os.replace(part, dest)
        _cloud_set(wid, state="done", got=got, err="")
    except Exception as exc:
        try:
            part.unlink()
        except OSError:
            pass
        _cloud_set(wid, state="error", err="%s: %s" % (type(exc).__name__, str(exc)[:140]))


def _cloud_worker() -> None:
    global _CLOUD_WORKER
    while True:
        with _CLOUD_LOCK:
            wid = _CLOUD_QUEUE.pop(0) if _CLOUD_QUEUE else None
            if not wid:
                _CLOUD_WORKER = None
                return
            _CLOUD_STATE[wid] = {"state": "downloading", "got": 0}
        item = next((x for x in _cloud_items() if x["id"] == wid), None)
        if not item:
            _cloud_set(wid, state="error", err="清单里没有这个壁纸")
            continue
        _cloud_fetch_one(item)


def _cloud_enqueue(wid: str) -> str:
    """返回空字符串表示已入队/已在本地；否则是给用户看的原因。"""
    global _CLOUD_WORKER
    item = next((x for x in _cloud_items() if x["id"] == wid), None)
    if not item:
        return "清单里没有这个壁纸"
    if _cloud_cached(item):
        return ""
    with _CLOUD_LOCK:
        if wid in _CLOUD_QUEUE:
            return ""
        if (_CLOUD_STATE.get(wid) or {}).get("state") == "downloading":
            return ""
        _CLOUD_QUEUE.append(wid)
        _CLOUD_STATE[wid] = {"state": "queued", "got": 0}
        if _CLOUD_WORKER is None:
            _CLOUD_WORKER = threading.Thread(target=_cloud_worker,
                                             name="wallpaper-cloud", daemon=True)
            _CLOUD_WORKER.start()
    return ""


def api_cloud(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    items = _cloud_items()
    return {"ok": True, "version": VERSION, "dir": str(CLOUD_DIR), "count": len(items),
            "wallpapers": [_cloud_view(x) for x in items]}


def api_cloud_fetch(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    wid = str((body or {}).get("id") or (query or {}).get("id") or "").strip()
    if not re.fullmatch(r"\d+", wid):
        return {"ok": False, "err": "壁纸 id 非法", "_code": 400}
    reason = _cloud_enqueue(wid)
    if reason:
        return {"ok": False, "err": reason, "_code": 400}
    return {"ok": True, "id": wid}


def api_cloud_remove(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    wid = str((body or {}).get("id") or (query or {}).get("id") or "").strip()
    if not re.fullmatch(r"\d+", wid):
        return {"ok": False, "err": "壁纸 id 非法", "_code": 400}
    item = next((x for x in _cloud_items() if x["id"] == wid), None)
    if not item:
        return {"ok": False, "err": "清单里没有这个壁纸", "_code": 400}
    cached = _cloud_cached(item)
    if cached:
        try:
            cached.unlink()
        except OSError as exc:
            return {"ok": False, "err": "删除失败：%s" % str(exc)[:80], "_code": 500}
    with _CLOUD_LOCK:
        _CLOUD_STATE.pop(wid, None)
    return {"ok": True, "id": wid}


def api_config_get(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    cfg = _load_config()
    workshop = _find_workshop()
    cfg["we_path"] = str(workshop) if workshop else None
    return {"ok": True, "config": cfg}


def api_config_post(body: dict[str, Any], query: dict[str, str]) -> dict[str, Any]:
    cfg = _load_config()
    if isinstance(body, dict):
        for key, value in body.items():
            if key not in _DEFAULT_CONFIG or value is None:
                continue
            if key in {"enabled", "muted", "transparent", "carousel_enabled", "carousel_shuffle"}:
                if isinstance(value, bool):
                    cfg[key] = value
            elif key in {"wallpaper_id", "wallpaper_type", "wallpaper_file", "we_path"}:
                if isinstance(value, str) and len(value) <= 260:
                    cfg[key] = value
            elif key == "fit" and value in {"cover", "contain", "stretch"}:
                cfg[key] = value
            elif key == "quality" and value in {"low", "medium", "high"}:
                cfg[key] = value
            elif key == "opacity":
                cfg[key] = _bounded_float(value, 0, 1, cfg[key])
            elif key == "scrim":
                cfg[key] = _bounded_float(value, 0, 1, cfg[key])
            elif key == "blur":
                cfg[key] = _bounded_int(value, 0, 32, cfg[key])
            elif key == "fps":
                cfg[key] = _bounded_int(value, 15, 120, cfg[key])
            elif key == "carousel_minutes":
                cfg[key] = _bounded_int(value, 5, 720, cfg[key])
    _save_config(cfg)
    return {"ok": True, "config": cfg}


ROUTES = [
    ("GET", "/api/plugin/wallpaper/library", api_library),
    ("GET", "/api/plugin/wallpaper/resource", api_resource),
    ("GET", "/api/plugin/wallpaper/config", api_config_get),
    ("POST", "/api/plugin/wallpaper/config", api_config_post),
    # 在线壁纸（画面不随包分发，按需下载到 DATA_DIR/cloud）
    ("GET", "/api/plugin/wallpaper/cloud", api_cloud),
    ("POST", "/api/plugin/wallpaper/cloud/fetch", api_cloud_fetch),
    ("POST", "/api/plugin/wallpaper/cloud/remove", api_cloud_remove),
]
