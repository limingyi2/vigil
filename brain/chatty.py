# -*- coding: utf-8 -*-
"""说话多少 —— 一个 1~10 的旋钮，按渠道各存一份。

用户报的是"她有时候有点吵"。这里只做三件事：
  1. 把值存进 `config.json` 的 `chatty` 段（`{渠道: 数字}`）
  2. 给提示词一段**刻度说明 + 当前值**，让她自己按这个数调密度
  3. 给面板/App 一对读写接口

🔴 刻意**不写**「几档该用什么语气」那种映射表（"低档就别发表情"之类）。
每加一个场景就得补一条，永远补不完，而且会把她管死。
给的是**刻度本身**：1 长什么样、10 长什么样、现在几 —— 剩下她自己判断。
只留一条硬约束（一句话别拆成三条），因为那才是"吵"的主要来源。
"""
import io
import json
import os

MIN, MAX, DEFAULT = 1, 10, 5


def _path():
    try:
        import runtime
        return runtime.CFG_PATH
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config", "config.json")


def _read():
    try:
        with io.open(_path(), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _clamp(v):
    """不在 1~10 里的一律夹回来。收到脏值时退回默认，别让它写进配置。"""
    try:
        n = int(round(float(v)))
    except Exception:
        return DEFAULT
    return max(MIN, min(MAX, n))


def all_levels():
    """`{渠道: 数字}`，一定含 `default`。"""
    d = _read().get("chatty")
    if not isinstance(d, dict):
        d = {}
    out = {"default": _clamp(d.get("default", DEFAULT))}
    for k, v in d.items():
        if str(k) == "default":
            continue
        out[str(k)] = _clamp(v)
    return out


def level(src=None):
    """这个渠道现在是几。没单独配就落回 default。"""
    s = str(src or "").strip().lower()
    d = all_levels()
    return d.get(s, d["default"]) if s else d["default"]


def set_level(n, src=None):
    """写回 config.json。src 为空时改的是 default（所有渠道的兜底）。

    写失败的返回值跟成功一样 —— 所以调用方**必须回读 config 确认**
    （面板那边也是这么做的：界面显示的必须是运行时读到的值）。
    """
    v = _clamp(n)
    p = _path()
    cfg = _read()
    d = cfg.get("chatty")
    if not isinstance(d, dict):
        d = {}
    d[str(src or "").strip().lower() or "default"] = v
    cfg["chatty"] = d
    try:
        # 跟 features.set_features 用同一套写法（indent=2 + newline="\n"）：
        # 两边格式不一致的话，配置文件会被改来改去地重排，
        # diff 全是噪音，真改动反而看不见。
        with io.open(p, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(cfg, fp, ensure_ascii=False, indent=2)
    except Exception:
        return all_levels()
    return all_levels()


def rule_for(src=None):
    """拼进 draft_rules 的那一段。src 为空时按 default。"""
    n = level(src)
    return (
        "\n【说话多少】这个尺度是 1 到 10：1 = 惜字如金，一句就够；"
        "10 = 最黏人，爱追问、能连着发几条。他把你调到了 %d。\n"
        "- 按这个数决定你这轮发几条、每条多长、要不要接着往下扯。\n"
        "- 不论几档，**一句话能说完的别拆成三条** —— 把短句拆开发才是"
        "最像机器的说法。\n"
        "- 他明显在收尾（「嗯」「好」「睡了」）时别硬开新话题。\n" % n)
