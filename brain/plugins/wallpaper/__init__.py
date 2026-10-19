# -*- coding: utf-8 -*-
"""
wallpaper 插件 —— 接入 Wallpaper Engine (Steam)
把 WE 创意工坊里的壁纸渲染到 Vigil 客户端背景。

支持类型：
  - video : <video> 直接播放
  - web   : <iframe sandbox> 加载 index.html
  - scene : 提取预览图作为静态背景（不做实时 WebGL 渲染）
  - application : 不支持（需要运行外部程序）

API:
  GET  /api/plugin/wallpaper/we/library       壁纸库列表（含缩略图、类型、名称）
  GET  /api/plugin/wallpaper/we/media/<id>/<file>  代理壁纸内的媒体文件（视频/图片）
  GET  /api/plugin/wallpaper/we/preview/<id>  壁纸预览图（优先用 WE 生成的预览图）
  GET  /api/plugin/wallpaper/config            当前配置
  POST /api/plugin/wallpaper/config            保存配置
"""

import json
import os
import re
import mimetypes
from pathlib import Path

# ── 插件元信息 ──────────────────────────────────────────────
NAME = "wallpaper"
UI_PERMISSIONS = ["brain.plugin.wallpaper.api"]
VERSION = "3.0.0"
DESC = "Wallpaper Engine 壁纸接入：扫描 Steam 创意工坊，把视频/网页/场景壁纸渲染到客户端背景"

# ── 配置持久化 ──────────────────────────────────────────────
CONFIG_DIR = Path(os.path.expanduser("~")) / ".vigil" / "wallpaper"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config():
    try:
        if CONFIG_FILE.exists():
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {
        "enabled": False,
        "wallpaper_id": None,       # 创意工坊 ID，如 "3098013340"
        "wallpaper_type": None,     # video / web / scene
        "wallpaper_file": None,     # 壁纸主文件名
        "opacity": 0.7,             # 壁纸层不透明度 0-1
        "scrim": 0.4,               # 遮罩不透明度 0-1
        "blur": 0,                  # 毛玻璃模糊 px
        "muted": True,              # 视频是否静音
        "we_path": None,            # 手动指定 WE 创意工坊路径（None=自动探测）
    }


def _save_config(cfg):
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception:
        pass


# ── Wallpaper Engine 路径探测 ────────────────────────────────
def _find_we_workshop():
    """返回 WE 创意工坊目录路径，找不到返回 None。

    探测顺序：
    1. 配置里手动指定的 we_path
    2. 注册表 HKCU/HKLM 的 Steam 路径
    3. Steam libraryfolders.vdf 里的所有库
    4. 常见硬编码路径
    """
    cfg = _load_config()
    if cfg.get("we_path") and Path(cfg["we_path"]).exists():
        return cfg["we_path"]

    candidates = []

    # 注册表
    try:
        import winreg
        for root, subkey in [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
        ]:
            try:
                with winreg.OpenKey(root, subkey) as k:
                    steam_path, _ = winreg.QueryValueEx(k, "SteamPath" if root == winreg.HKEY_CURRENT_USER else "InstallPath")
                    if steam_path:
                        candidates.append(Path(steam_path))
            except Exception:
                continue
    except Exception:
        pass

    # libraryfolders.vdf（从第一个找到的 Steam 路径出发）
    # 注意：遍历副本 list(candidates)，否则循环内 append 会导致无限增长
    for steam in list(candidates):
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                text = vdf.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                    p = m.group(1).replace("\\\\", "\\").replace("\\", os.sep)
                    candidates.append(Path(p))
            except Exception:
                pass

    # 常见路径
    for p in [
        r"C:\Program Files (x86)\Steam",
        r"D:\Steam", r"E:\Steam", r"F:\Steam",
        r"D:\SteamLibrary", r"E:\SteamLibrary", r"F:\SteamLibrary",
    ]:
        candidates.append(Path(p))

    # 检查每个候选下的创意工坊目录
    seen = set()
    for c in candidates:
        if not c or str(c) in seen:
            continue
        seen.add(str(c))
        workshop = c / "steamapps" / "workshop" / "content" / "431960"
        if workshop.exists():
            return str(workshop)

    return None


# ── 壁纸库扫描（带缓存）──────────────────────────────────────
CACHE_FILE = CONFIG_DIR / "library_cache.json"
CACHE_TTL = 3600  # 缓存 1 小时


def _scan_library(force=False):
    """扫描 WE 创意工坊，返回壁纸列表。带磁盘缓存，避免每次都扫 100+ 目录。

    每个壁纸项：id, title, type, preview, file, rating
    """
    # 读缓存
    if not force and CACHE_FILE.exists():
        try:
            cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            import time
            if time.time() - cache.get("ts", 0) < CACHE_TTL:
                return cache.get("wallpapers", []), cache.get("we_path")
        except Exception:
            pass

    workshop = _find_we_workshop()
    if not workshop:
        return [], None

    wp_dir = Path(workshop)
    wallpapers = []

    try:
        for sub in sorted(wp_dir.iterdir()):
            if not sub.is_dir():
                continue
            wid = sub.name
            proj = sub / "project.json"
            title = wid
            wtype = "unknown"
            preview = None
            main_file = None
            rating = "Everyone"

            if proj.exists():
                try:
                    data = json.loads(proj.read_text(encoding="utf-8", errors="ignore"))
                    title = data.get("title") or wid
                    wtype = data.get("type") or "unknown"
                    rating = data.get("contentrating") or "Everyone"
                    for cand in ["preview.jpg", "preview.gif", "preview.png"]:
                        if (sub / cand).exists():
                            preview = cand
                            break
                    if wtype == "video":
                        for f in sub.iterdir():
                            if f.suffix.lower() in (".mp4", ".webm", ".mkv", ".avi"):
                                main_file = f.name
                                break
                    elif wtype == "web":
                        if (sub / "index.html").exists():
                            main_file = "index.html"
                    elif wtype == "scene":
                        main_file = "scene.pkg" if (sub / "scene.pkg").exists() else None
                except Exception:
                    pass

            if not preview:
                for f in sub.iterdir():
                    if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif"):
                        preview = f.name
                        break

            if wtype == "application":
                continue

            wallpapers.append({
                "id": wid, "title": title, "type": wtype,
                "preview": preview, "file": main_file, "rating": rating,
            })
    except Exception:
        pass

    # 写缓存
    try:
        import time
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps({
            "ts": time.time(), "we_path": workshop, "wallpapers": wallpapers,
        }, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass

    return wallpapers, workshop


# ── API 路由 ─────────────────────────────────────────────────
def api_we_library(body, query):
    """GET 壁纸库列表。

    查询参数：
      ?type=scene,web   只显示指定类型（逗号分隔）
      ?exclude=video    排除指定类型（逗号分隔）
      ?refresh=1        强制重新扫描（忽略缓存）
    """
    q = query or {}
    force = str(q.get("refresh", "")).lower() in ("1", "true", "yes")
    wallpapers, workshop = _scan_library(force=force)

    # 类型过滤
    type_filter = q.get("type", "").strip()
    exclude_filter = q.get("exclude", "").strip()
    if type_filter:
        allowed = set(t.strip().lower() for t in type_filter.split(",") if t.strip())
        wallpapers = [w for w in wallpapers if w["type"].lower() in allowed]
    if exclude_filter:
        excluded = set(t.strip().lower() for t in exclude_filter.split(",") if t.strip())
        wallpapers = [w for w in wallpapers if w["type"].lower() not in excluded]

    return {
        "ok": True,
        "we_path": workshop,
        "count": len(wallpapers),
        "wallpapers": wallpapers,
    }


def api_we_media(body, query):
    """GET 代理壁纸内的媒体文件。

    路径格式：/api/plugin/wallpaper/we/media/<id>/<file>
    由 server.py 的路径解析把 <id>/<file> 放进 query 或 body。
    这里用 query.get("id") 和 query.get("file")。
    """
    wid = (query or {}).get("id", "")
    fname = (query or {}).get("file", "")
    if not wid or not fname:
        return {"ok": False, "err": "缺少 id 或 file", "_code": 400}

    # 安全：防止路径穿越
    if not re.fullmatch(r"\d+", str(wid)) or "\\" in fname or "\x00" in fname:
        return {"ok": False, "err": "非法路径", "_code": 400}

    workshop = _find_we_workshop()
    if not workshop:
        return {"ok": False, "err": "未找到 Wallpaper Engine", "_code": 404}

    root = (Path(workshop) / wid).resolve()
    fpath = (root / fname).resolve()
    try:
        fpath.relative_to(root)
    except ValueError:
        return {"ok": False, "err": "非法路径", "_code": 400}
    if not fpath.exists() or not fpath.is_file():
        return {"ok": False, "err": "文件不存在", "_code": 404}

    # 读取文件并返回（server.py 会处理二进制响应）
    # 这里返回特殊结构，由插件路由的调用方处理
    mime, _ = mimetypes.guess_type(str(fpath))
    return {
        "__file__": str(fpath),
        "__mime__": mime or "application/octet-stream",
    }


def api_we_preview(body, query):
    """GET 壁纸预览图"""
    wid = (query or {}).get("id", "")
    if not re.fullmatch(r"\d+", str(wid)):
        return {"ok": False, "err": "非法壁纸 id", "_code": 400}

    workshop = _find_we_workshop()
    if not workshop:
        return {"ok": False, "err": "未找到 Wallpaper Engine", "_code": 404}

    wdir = Path(workshop) / wid
    if not wdir.exists():
        return {"ok": False, "err": "壁纸不存在", "_code": 404}

    # 优先 preview.jpg，然后任意图片
    preview = None
    for cand in ["preview.jpg", "preview.gif", "preview.png"]:
        if (wdir / cand).exists():
            preview = cand
            break
    if not preview:
        for f in wdir.iterdir():
            if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".gif"):
                preview = f.name
                break

    if not preview:
        return {"ok": False, "err": "无预览图", "_code": 404}

    mime, _ = mimetypes.guess_type(preview)
    return {
        "__file__": str(wdir / preview),
        "__mime__": mime or "image/jpeg",
    }


def api_config_get(body, query):
    """GET 当前配置"""
    cfg = _load_config()
    cfg["we_path"] = _find_we_workshop()
    return {"ok": True, "config": cfg}


def api_config_post(body, query):
    """POST 保存配置"""
    cfg = _load_config()
    if isinstance(body, dict):
        for k in ["enabled", "wallpaper_id", "wallpaper_type", "wallpaper_file",
                  "opacity", "scrim", "blur", "muted", "we_path"]:
            if k in body:
                cfg[k] = body[k]
    _save_config(cfg)
    return {"ok": True, "config": cfg}


# ── 路由表（完整路径，精确匹配）──────────────────────────────
ROUTES = [
    ("GET",  "/api/plugin/wallpaper/we/library", api_we_library),
    ("GET",  "/api/plugin/wallpaper/we/media",   api_we_media),    # ?id=xxx&file=xxx
    ("GET",  "/api/plugin/wallpaper/we/preview", api_we_preview),  # ?id=xxx
    ("GET",  "/api/plugin/wallpaper/config",     api_config_get),
    ("POST", "/api/plugin/wallpaper/config",     api_config_post),
]
