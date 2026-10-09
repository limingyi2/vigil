# -*- coding: utf-8 -*-
"""壁纸插件：给客户端加自定义背景层。

综合 DSH 三款壁纸插件的优点：
- 6 个内置渐变预设
- 本地上传（PNG/JPG/GIF/WebP）
- URL 粘贴
- 壁纸不透明度 / 遮罩不透明度 / 面板不透明度 / 毛玻璃模糊
- 填充方式 cover / contain
- 亮色/暗色主题自动切换遮罩颜色
- 配置持久化，重启不丢

目录型插件：同目录下 frontend.js / frontend.css 会被客户端自动注入。
"""
import json
import os
import uuid

NAME = "wallpaper"
DESC = "桌面壁纸背景：自定义图片/渐变预设，可调透明度/遮罩/毛玻璃"
FEATURE = "wallpaper"

# 配置和图片存储目录
_STORE_DIR = os.path.join(os.path.expanduser("~"), ".vigil", "wallpaper")
_CONFIG_PATH = os.path.join(_STORE_DIR, "config.json")
_IMAGE_DIR = os.path.join(_STORE_DIR, "images")

# 内置渐变预设
PRESETS = {
    "aurora": {"name": "极光", "css": "linear-gradient(135deg, #667eea 0%, #764ba2 50%, #f093fb 100%)"},
    "ocean": {"name": "海洋", "css": "linear-gradient(135deg, #2E3192 0%, #1BFFFF 100%)"},
    "sunset": {"name": "日落", "css": "linear-gradient(135deg, #f093fb 0%, #f5576c 50%, #ffecd2 100%)"},
    "forest": {"name": "森林", "css": "linear-gradient(135deg, #134E5E 0%, #71B280 100%)"},
    "night": {"name": "夜空", "css": "linear-gradient(135deg, #0f0c29 0%, #302b63 50%, #24243e 100%)"},
    "sakura": {"name": "樱花", "css": "linear-gradient(135deg, #ffecd2 0%, #fcb69f 50%, #ff9a9e 100%)"},
}

DEFAULT_CONFIG = {
    "enabled": False,
    "type": "preset",
    "preset": "aurora",
    "image_url": "",
    "image_file": "",
    "opacity": 60,
    "scrim": 30,
    "panel_opacity": 85,
    "glass_blur": 12,
    "fit": "cover",
}


def _ensure_dirs():
    os.makedirs(_STORE_DIR, exist_ok=True)
    os.makedirs(_IMAGE_DIR, exist_ok=True)


def _load_config():
    _ensure_dirs()
    if not os.path.isfile(_CONFIG_PATH):
        return dict(DEFAULT_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        merged = dict(DEFAULT_CONFIG)
        merged.update(cfg)
        return merged
    except Exception:
        return dict(DEFAULT_CONFIG)


def _save_config(cfg):
    _ensure_dirs()
    with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def api_get_config(body, query):
    cfg = _load_config()
    return {
        "ok": True,
        "config": cfg,
        "presets": {k: {"name": v["name"], "css": v["css"]} for k, v in PRESETS.items()},
    }


def api_set_config(body, query):
    cfg = _load_config()
    for key in DEFAULT_CONFIG:
        if key in body:
            cfg[key] = body[key]
    cfg["opacity"] = max(0, min(100, int(cfg.get("opacity", 60))))
    cfg["scrim"] = max(0, min(95, int(cfg.get("scrim", 30))))
    cfg["panel_opacity"] = max(0, min(100, int(cfg.get("panel_opacity", 85))))
    cfg["glass_blur"] = max(0, min(40, int(cfg.get("glass_blur", 12))))
    cfg["fit"] = cfg["fit"] if cfg["fit"] in ("cover", "contain") else "cover"
    cfg["type"] = cfg["type"] if cfg["type"] in ("preset", "image", "url") else "preset"
    _save_config(cfg)
    return {"ok": True, "config": cfg}


def api_upload(body, query):
    import base64
    _ensure_dirs()
    filename = body.get("filename", "")
    data_b64 = body.get("data", "")
    if not filename or not data_b64:
        return {"ok": False, "err": "缺少文件名或图片数据"}
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        return {"ok": False, "err": "不支持的文件类型"}
    safe_name = uuid.uuid4().hex + ext
    save_path = os.path.join(_IMAGE_DIR, safe_name)
    try:
        img_data = base64.b64decode(data_b64)
        if len(img_data) > 10 * 1024 * 1024:
            return {"ok": False, "err": "图片不能超过 10MB"}
        with open(save_path, "wb") as f:
            f.write(img_data)
    except Exception as e:
        return {"ok": False, "err": "保存失败：%s" % str(e)[:80]}
    cfg = _load_config()
    old_file = cfg.get("image_file", "")
    if old_file:
        old_path = os.path.join(_IMAGE_DIR, old_file)
        if os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except Exception:
                pass
    cfg["type"] = "image"
    cfg["image_file"] = safe_name
    cfg["enabled"] = True
    _save_config(cfg)
    return {"ok": True, "image_file": safe_name, "config": cfg}


def api_image(body, query):
    filename = ""
    if isinstance(query, dict):
        v = query.get("file", [""])
        filename = v[0] if isinstance(v, list) and v else str(v)
    if not filename:
        return {"ok": False, "err": "缺少文件名"}
    safe_name = os.path.basename(filename)
    img_path = os.path.join(_IMAGE_DIR, safe_name)
    if not os.path.isfile(img_path):
        return {"ok": False, "err": "图片不存在"}
    ext = os.path.splitext(safe_name)[1].lower()
    content_type = {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "application/octet-stream")
    import base64
    with open(img_path, "rb") as f:
        data = f.read()
    return {"ok": True, "content_type": content_type, "data": base64.b64encode(data).decode("ascii")}


ROUTES = [
    ("GET", "/api/plugin/wallpaper/config", api_get_config),
    ("POST", "/api/plugin/wallpaper/config", api_set_config),
    ("POST", "/api/plugin/wallpaper/upload", api_upload),
    ("GET", "/api/plugin/wallpaper/image", api_image),
]
