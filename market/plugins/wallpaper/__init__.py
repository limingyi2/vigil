# -*- coding: utf-8 -*-
"""Vigil Wallpaper Engine plugin v4.

The plugin owns discovery and read-only media access. Rendering is shipped in the
plugin frontend and runs in a sandboxed iframe; the Electron host only provides the
same generic, expiring resource capability used by every media plugin.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
from pathlib import Path
from typing import Any

NAME = "wallpaper"
VERSION = "4.1.2"
DESC = "独立 Wallpaper Engine 壁纸：Scene/WebGL、视频、网页与高质量界面适配"
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
    "opacity": 0.52,
    "scrim": 0.24,
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
]
