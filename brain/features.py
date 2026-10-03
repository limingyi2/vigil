# -*- coding: utf-8 -*-
"""功能开关：App 设置页远程控制各附加功能的开/关，存 config.json 的 features 段。

读缓存 30 秒 —— 聊天链路每条消息都会问，不能每条都读一次盘；关掉后最多
30 秒生效。任何读取失败一律当"开"：开关系统自己坏了不能拖累功能。

DEFAULTS 里除核心功能外，插件声明了 FEATURE 的也会登记进来（见 plugin_host），
这样设置页不用为每个插件改代码，插件删了开关自然消失。

## 按渠道覆盖（config.json 的 features_by_src）

同一个功能在不同渠道该有不同的开合 —— 群聊里她不该报天气、不该关心快递，
那是你和她的私事；群里有人随口说的单号更不该进你的监控。
所以 `features_by_src.<渠道>` 里可以给某个渠道单独覆盖：

    "features_by_src": {"wechat": {"weather_alert": false, "packages": false}}

**没写的键跟全局走**，所以只写要关的那几个就够，不用抄一整份。

为什么不复制一套渠道专属开关：复制的话每加一个功能要改两处，漏一处就出
bug（"她在群里提醒你加衣服"就是这么来的）。覆盖是**只写差异**，
全局那一份仍是唯一真源。
"""
import json
import time
import threading

try:
    from paths import CONFIG_PATH
    _CFG = str(CONFIG_PATH)
except Exception:
    _CFG = r"C:\Vigil\config\config.json"

# 核心开关与默认值（插件的由 plugin_host._sync_features 追加）
DEFAULTS = {
    "hotboard": True,         # 热搜进她的世界
    "weather": True,          # 天气进她的世界
    "weather_alert": True,    # 天气变化主动提醒
    "packages": True,         # 快递监控与提醒
    "rand_img_chat": True,    # 聊天里她可以甩 [rand:] 图
    "rand_img_moment": True,  # 朋友圈配图优先用随机图
    "tools": True,            # 模型可自选调免费 API（查他在哪/天气/快递）
}

_cache = {"t": 0.0, "d": dict(DEFAULTS), "by": {}}
_lock = threading.RLock()
# 当前渠道。聊天入口在进入时 set_current_src 一次，管完这一轮。
_cur_src = "app"


def set_current_src(src):
    """声明这一轮处理的是哪个渠道。"""
    global _cur_src
    _cur_src = str(src or "app").strip().lower() or "app"
    return _cur_src


def current_src():
    return _cur_src


class with_channel(object):
    """临时把当前渠道改成 src（with 语句），退出时恢复。

    主动提醒那种"在后台线程里开口"的路径用这个 —— 它们不属于任何一轮对话，
    但发到群里显然不合适。
    """

    def __init__(self, src):
        self.src = src
        self.old = None

    def __enter__(self):
        global _cur_src
        with _lock:
            self.old = _cur_src
            _cur_src = str(self.src or "app").strip().lower() or "app"
        return self.src

    def __exit__(self, *a):
        global _cur_src
        with _lock:
            _cur_src = self.old or "app"
        return False


def invalidate():
    """丢掉 30 秒缓存（插件登记完新开关、或刚写完 config 时调）。"""
    with _lock:
        _cache["t"] = 0.0


def register(key, default=True):
    """登记一个开关（插件装载时调）。已存在不改默认值 —— config 里已有的设置优先。"""
    key = str(key or "").strip()
    if not key or key in DEFAULTS:
        return False
    DEFAULTS[key] = bool(default)
    invalidate()
    return True


def _reload():
    try:
        cfg = json.load(open(_CFG, encoding="utf-8"))
        f = cfg.get("features") or {}
        d = dict(DEFAULTS)
        d.update({k: bool(f[k]) for k in d if k in f})
        by = {}
        for src, patch in (cfg.get("features_by_src") or {}).items():
            if isinstance(patch, dict):
                by[str(src).strip().lower()] = {
                    k: bool(v) for k, v in patch.items() if k in d}
        _cache["d"] = d
        _cache["by"] = by
        _cache["t"] = time.time()
    except Exception:
        pass


def all_features(src=None):
    """当前全部开关（30 秒缓存）。传 src 拿该渠道覆盖后的结果。"""
    with _lock:
        if time.time() - _cache["t"] > 30:
            _reload()
        d = dict(_cache["d"])
        src = str(src or "").strip().lower()
        # app 是基线，不查它的覆盖（省一次字典查找，也避免手滑写出
        # features_by_src.app 之后两边互相盖）
        if src and src != "app":
            d.update(_cache["by"].get(src) or {})
    return d


def on(key, src=None):
    """某功能开没开。不认识的 key 一律当开。

    src 省略时按**当前渠道**算（见 set_current_src / with_channel）——
    聊天链路和后台提醒那些地方都是这么调的，它们只管问"这个功能此刻开没开"，
    不该各自记着自己是哪个渠道。
    """
    return bool(all_features(src if src is not None else _cur_src)
               .get(key, True))


def set_features(patch, src=None):
    """App 改开关：合并写回 config.json，返回该渠道视角下的全部开关。

    带 src 时写进 features_by_src.<src>，**只写这几个键**（覆盖语义），
    不动全局那一份。写失败返回 None。
    """
    try:
        cfg = json.load(open(_CFG, encoding="utf-8"))
        if src:
            src = str(src).strip().lower()
            by = cfg.get("features_by_src") or {}
            cur = dict(by.get(src)) if isinstance(by.get(src), dict) else {}
            for k, v in (patch or {}).items():
                if k in DEFAULTS:
                    cur[k] = bool(v)
            by[src] = cur
            cfg["features_by_src"] = by
            out = all_features(src)
        else:
            f = cfg.get("features") or {}
            for k, v in (patch or {}).items():
                if k in DEFAULTS:
                    f[k] = bool(v)
            cfg["features"] = f
            out = all_features()
        with open(_CFG, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        invalidate()
        return out
    except Exception:
        return None
