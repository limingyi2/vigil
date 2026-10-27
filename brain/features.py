# -*- coding: utf-8 -*-
"""功能开关：App 设置页远程控制各附加功能的开/关，存 config.json 的 features 段。

读缓存 30 秒 —— 聊天链路每条消息都会问，不能每条都读一次盘；关掉后最多
30 秒生效。任何读取失败一律当"开"：开关系统自己坏了不能拖累功能。

DEFAULTS 里除核心功能外，插件声明了 FEATURE 的也会登记进来（见 plugin_host），
这样设置页不用为每个插件改代码，插件删了开关自然消失。

## 按渠道覆盖（config.json 的 features_by_src）

插件开关只控制已由 Plugin Host 加载的插件。`on()` 对未登记键保留历史默认值，
不能作为插件已安装的证据；暴露插件工具必须另外检查已加载插件及其工具注册。

同一个功能在不同渠道该有不同的开合 —— 群聊里她不该报天气、不该关心快递，
那是你和她的私事；群里有人随口说的单号更不该进你的监控。
所以 `features_by_src.<渠道>` 里可以给某个渠道单独覆盖：

    "features_by_src": {"<渠道名>": {"weather_alert": false, "packages": false}}

**没写的键跟全局走**，所以只写要关的那几个就够，不用抄一整份。
全局那份 `features` 是"任何渠道都没覆盖时"的初值。

 **app 跟其它渠道一样，也是一份覆盖**。写和读必须是同一套规则 —— 曾经读取侧单独跳过 app（注释写着
"app 就是基线"），写入侧却照写 `features_by_src.app`：面板在 App 渠道
点开关"点了跟没点一样"，还往配置里攒了一堆永远读不出来的"幽灵覆盖"
（界面上显示已关、运行时全是开的）。详见 `_test_features_src.py`。

为什么不复制一套渠道专属开关：复制的话每加一个功能要改两处，漏一处就出
bug（"她在群里提醒你加衣服"就是这么来的）。覆盖是**只写差异**，
全局那一份仍是统一定义。
"""
import contextvars
import json
import threading
import time

try:
    from paths import CONFIG_PATH
    _CFG = str(CONFIG_PATH)
except Exception:
    _CFG = r"C:\vigil\config\config.json"

# 核心开关与默认值（插件的由 plugin_host._sync_features 追加）
DEFAULTS = {
        #  这里曾有一个 "hotboard": True（热搜进她的世界），现已移除。
    # 已经删掉：那套 hotboard_cache + 抖音榜被「百度热梗榜」取代了，
    # 后来又随 meme 插件一起下线（P21.2 删插件），这里不再保留开关。
    "weather": True,          # 天气进她的世界
    "weather_alert": True,    # 天气变化主动提醒
    "packages": True,         # 快递监控与提醒
    "tools": True,            # 模型可自选调免费 API（查他在哪/天气/快递）
    "voice": True,            # 语音条（App 能渲染音频条；群里发不了 mp3，要关）
    # 可选插件：仓库拓扑查看（branch-atlas）是开发者工具，0.1.0 只做了只读视图。
    # 默认关 —— 界面上不留一个对聊天没用的入口；要看去设置里打开。
    "branch-atlas": False,
}

_cache = {"t": 0.0, "d": dict(DEFAULTS), "by": {}}
_lock = threading.RLock()
# 当前渠道。聊天入口在进入时 set_current_src 一次，管完这一轮。
#
#  必须是 ContextVar，不能是模块级变量：server 是 ThreadingHTTPServer，
# 两个请求的处理线程会互相覆盖同一个"当前渠道"，A 请求的功能开关/权限判断
# 可能读到 B 请求的渠道。ContextVar 按线程（按上下文）隔离，请求线程之间
# 天然不串；后台线程在 start 时复制一份父上下文，也不会回写主流程。
_cur_src = contextvars.ContextVar("features_cur_src", default="pc")


def set_current_src(src):
    """声明这一轮处理的是哪个渠道。只影响当前请求线程/上下文。"""
    s = str(src or "pc").strip().lower() or "pc"
    _cur_src.set(s)
    return s


def current_src():
    return _cur_src.get()


class with_channel(object):
    """临时把当前渠道改成 src（with 语句），退出时恢复。

    主动提醒那种"在后台线程里开口"的路径用这个 —— 它们不属于任何一轮对话，
    但发到群里显然不合适。基于 ContextVar token 复位，不会影响别的线程。
    """

    def __init__(self, src):
        self.src = src
        self._token = None

    def __enter__(self):
        s = str(self.src or "pc").strip().lower() or "pc"
        self._token = _cur_src.set(s)
        return s

    def __exit__(self, *a):
        if self._token is not None:
            _cur_src.reset(self._token)
            self._token = None
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
    """当前全部开关（30 秒缓存）。传 src 拿该渠道覆盖后的结果。

     `src` 一律参与覆盖合并，**不跳过 app**。和写入侧 set_features
    必须同规则：那边写了 features_by_src.app，这边就得读它。
    """
    with _lock:
        if time.time() - _cache["t"] > 30:
            _reload()
        d = dict(_cache["d"])
        src = str(src or "").strip().lower()
        if src:
            d.update(_cache["by"].get(src) or {})
    return d


def on(key, src=None):
    """某功能开没开。不认识的 key 保留兼容默认值；不代表插件已安装。

    src 省略时按**当前渠道**算（见 set_current_src / with_channel）——
    聊天链路和后台提醒那些地方都是这么调的，它们只管问"这个功能此刻开没开"，
    不该各自记着自己是哪个渠道。工具暴露层还必须由 Plugin Host 验证工具所有者。
    """
    return bool(all_features(src if src is not None else _cur_src.get())
               .get(key, True))


def set_features(patch, src=None):
    """App 改开关：合并写回 config.json，返回该渠道视角下的全部开关。

    带 src 时写进 features_by_src.<src>，**只写这几个键**（覆盖语义），
    不动全局那一份。写失败返回 None。

     写和读必须同一套规则。这里曾经不一致：读取侧跳过 app 的覆盖
    （当它是基线），写入侧却老老实实照渠道名写 features_by_src.app ——
    于是面板在 App 渠道点开关"点了跟没点一样"，还往配置里攒了一堆
    读不出来的"幽灵覆盖"：界面上显示已关，运行时全是开的。
    """
    try:
        cfg = json.load(open(_CFG, encoding="utf-8"))
        src = str(src or "").strip().lower()
        # 只认 DEFAULTS 里登记过的键。一个都没有（或者 patch 是空的）就直接
        # 返回：别在配置里留空壳（{"app": {}} 这种没意义又碍眼），也别白写一次盘。
        add = {k: bool(v) for k, v in (patch or {}).items() if k in DEFAULTS}
        if not add:
            return all_features(src)
        if src:
            by = cfg.get("features_by_src") or {}
            cur = dict(by.get(src)) if isinstance(by.get(src), dict) else {}
            cur.update(add)                     # 只并进这几个键，别的留着
            by[src] = cur
            cfg["features_by_src"] = by
        else:
            f = cfg.get("features") or {}
            f.update(add)
            cfg["features"] = f
        with open(_CFG, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
        #  顺序要紧：**先**让缓存失效，**再**回读。
        # 反过来的话返回的是 30 秒前的旧缓存 —— 调用方（面板）拿到的
                # 是"修改前"的答案，界面上看着就像开关没写进去。
        invalidate()
        return all_features(src)
    except Exception:
        return None


def drop(key):
    """把一个开关整个摘掉：内存里的 DEFAULTS + 配置文件里所有渠道的键。

    插件卸载时调。不摘的话配置里会留一个谁也关不掉的键 ——
    features.on(key, src) 只看键、不看插件还在不在，
    于是功能开关页会列出一个已经不存在的功能，谁点都关不掉。
    返回清掉的处数（0 = 本来就没有）。
    """
    key = str(key or "").strip()
    if not key:
        return 0
    n = 0
    if DEFAULTS.pop(key, None) is not None:
        n += 1
    try:
        cfg = json.load(open(_CFG, encoding="utf-8"))
    except Exception:
        cfg = None
    if isinstance(cfg, dict):
        dirty = False
        f = cfg.get("features")
        if isinstance(f, dict) and key in f:
            f.pop(key, None)
            n += 1                          # 全局那份也算一处，别少报
            dirty = True
        by = cfg.get("features_by_src")
        if isinstance(by, dict):
            for s in list(by):
                d = by.get(s)
                if isinstance(d, dict) and key in d:
                    d.pop(key, None)
                    n += 1
                    dirty = True
        if dirty:
            try:
                with open(_CFG, "w", encoding="utf-8", newline="\n") as fp:
                    json.dump(cfg, fp, ensure_ascii=False, indent=2)
            except Exception:
                return n              # 内存清了、文件没写成，如实返回
    invalidate()
    return n
