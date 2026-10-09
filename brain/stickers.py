# -*- coding: utf-8 -*-
"""表情包素材库：她收藏的图，回复时自己决定要不要甩出来。

素材主要从聊天自然积累（你发过的表情包她会收藏，梗是你们俩的），也可以往
data/stickers/ 里手动丢图。每张图配一句视觉模型的描述，她在 prompt 里能看到每张
是什么、自己挑、自己决定发不发 —— 不写触发规则，不按概率随机。

索引：data/stickers/index.json —— {文件名: {"desc":…, "src":…, "t":…}}
"""
import datetime
import hashlib
import json
import os
import threading
import time

import paths

_DIR = paths.STICKERS_DIR
_lock = threading.Lock()
_index = None                       # 惰性加载


def _ensure_loaded():
    global _index
    if _index is None:
        try:
            os.makedirs(_DIR, exist_ok=True)
            _index = json.load(open(os.path.join(_DIR, "index.json"),
                                    encoding="utf-8"))
        except Exception:
            _index = {}
    return _index


def _save_index():
    try:
        json.dump(_index, open(os.path.join(_DIR, "index.json"), "w",
                               encoding="utf-8"), ensure_ascii=False, indent=1)
    except OSError:
        pass


def _sniff_ext(blob):
    if blob[:3] == b"\xff\xd8\xff":
        return "jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if blob[:4] == b"GIF8":
        return "gif"
    return ""


def save_bytes(blob, desc="", src=""):

    """收藏一张表情包，返回文件名；失败返回 ""。

    2026-10-09 改版：文件名改成**纯内容哈希**（md5 前 8 位）——同一张图
    不管被收到几次，永远落成同一个文件，库里不再长副本。以前是
    时间戳+哈希，同图每收一次就多一个文件（同一张黄球塞满图库就是这么
    来的）。重复收藏直接返回已有名字，索引不动；图已在库但没标签、这次
    又带了 desc 时顺手补上。
    """
    if not blob:
        return ""
    idx = _ensure_loaded()
    ext = _sniff_ext(blob)
    if not ext:
        return ""
    h = hashlib.md5(blob).hexdigest()[:8]
    name = f"{h}.{ext}"
    if name not in idx:
        try:
            with open(os.path.join(_DIR, name), "wb") as f:
                f.write(blob)
        except OSError:
            return ""
        with _lock:
            idx[name] = {"desc": (desc or "")[:120], "src": src,
                         "t": time.strftime("%Y-%m-%d %H:%M")}
            _save_index()
    elif desc and not idx[name].get("desc"):
        set_desc(name, desc)
    return name


def set_desc(name, desc):
    """更新一张表情包的标签（视觉模型生成的描述，她在清单里按这个挑）。"""
    idx = _ensure_loaded()
    name = os.path.basename(str(name or "").strip())
    if not name or name not in idx:
        return False
    with _lock:
        idx[name]["desc"] = str(desc or "")[:120]
        _save_index()
    return True


def describe_of(name):
    """读一张表情包的标签；没有返回 ""。"""
    idx = _ensure_loaded()
    name = os.path.basename(str(name or "").strip())
    if not name or name not in idx:
        return ""
    return str(idx[name].get("desc") or "")


def path_of(name):
    """文件名 -> 绝对路径；不存在/非法返回 ""。"""
    idx = _ensure_loaded()
    name = os.path.basename(str(name or "").strip())
    if not name or name not in idx:
        return ""
    p = os.path.join(_DIR, name)
    return p if os.path.exists(p) else ""


def all_names():
    return list(_ensure_loaded().keys())


def block(max_n=12):

    """给 draft prompt 的素材清单。一张都没有时返回 ""。"""
    idx = _ensure_loaded()
    if not idx:
        return ""
    items = sorted(idx.items(), key=lambda kv: kv[1].get("t", ""),
                   reverse=True)[:max_n]
    lines = []
    for name, v in items:
        desc = v.get("desc") or "（内容未知）"
        lines.append(f"- {name}：{desc}")
    head = (f"【你收藏的表情包】一共 {len(idx)} 张（下面是最新的 {len(items)} 张）。"
            "想配图就在回复的单独一行写 [img:文件名]——只有聊天软件能把这行"
            "变成图片，别写在别处；能不能用、用哪张，你自己掂量，别硬凑：")
    return head + "\n" + "\n".join(lines)
