# -*- coding: utf-8 -*-
"""知识库的全部可调项 —— 一处定义，别处只读。

两类设置的归属，照 paths.py 的规矩分：

- **你会手改的**放 `config/config.json` 的 `kb` 段（换嵌入后端、换模型）。
- **程序自己写的**放 `data/kb/settings.json`（界面上的开关、阈值、top_k）。

读的时候后者覆盖前者；写的时候**只写后者**。不写 config.json 有两个原因：
插件不该重写用户的配置文件（注释和键序都会丢），而且两份真源一定会打架。
"""
import json
import os
import threading

from paths import CONFIG_PATH, DATA_DIR

SETTINGS_PATH = os.path.join(DATA_DIR, "kb", "settings.json")

_LOCK = threading.RLock()
_cache = None

DEFAULTS = {
    "enabled": True,        # 总开关：关掉 = 工具不出现 + 每轮不注入
    "inject": True,         # 每轮自动检索并注入 system
    "top_k": 3,             # 注入用几条
    # 注入门槛。宁可不给，也别把不相关的段落塞进提示词 ——
    # 塞错的代价是"她拿别的文档内容回答"，比"她不知道"严重得多。
    "min_score": 0.45,
    "max_chars": 1200,      # 注入块的字数上限
    "search_k": 5,          # 工具检索默认返回几条
    "chunk_size": 800,      # 切块字数
    "chunk_overlap": 80,    # 块间重叠，防止语义被切断
    "batch": 32,            # 向量化批量
    "backend": "api",       # api | local | fastembed，见 embedder.py
    "model": "BAAI/bge-m3",  # local 后端加载的模型名
    "use_unified_search": True,  # 检索走统一检索（storage），旧 npy 做降级
    "sync_to_unified": True,     # 投喂时自动同步到统一检索（文本+向量）
}

# 只允许这几个键被写进 settings.json —— 别的键进来就是拼错了名字。
_WRITABLE = ("enabled", "inject", "top_k", "min_score", "max_chars",
             "search_k", "backend", "model")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def load(force=False):
    """合并三份来源：默认值 < config.json 的 kb 段 < data/kb/settings.json。"""
    global _cache
    with _LOCK:
        if _cache is not None and not force:
            return dict(_cache)
        cfg = dict(DEFAULTS)
        cfg.update(_read_json(CONFIG_PATH).get("kb") or {})
        cfg.update(_read_json(SETTINGS_PATH))
        out = {}
        for k, v in cfg.items():
            if k not in DEFAULTS:
                continue
            # 类型按默认值收口：界面上传上来的是字符串，别让 "3" 混进算术里。
            d = DEFAULTS[k]
            try:
                if isinstance(d, bool):
                    v = bool(v) if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
                elif isinstance(d, int):
                    v = int(float(v))
                elif isinstance(d, float):
                    v = float(v)
                else:
                    v = str(v)
            except Exception:
                v = d
            out[k] = v
        _cache = out
        return dict(out)


def get(key, default=None):
    return load().get(key, DEFAULTS.get(key, default))


def set_config(patch):
    """写运行时设置。只认 _WRITABLE 里的键，返回改动后的完整配置。"""
    patch = patch if isinstance(patch, dict) else {}
    with _LOCK:
        cur = _read_json(SETTINGS_PATH)
        for k, v in patch.items():
            if k in _WRITABLE:
                cur[k] = v
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cur, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SETTINGS_PATH)
        return load(force=True)
