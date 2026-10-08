# -*- coding: utf-8 -*-
"""表情包收藏与随手发（微信群聊）。

收藏：server.py 在收到微信入站图片时调 stickers.save_bytes() 入库。
发送：这里在闲聊轮按概率提示她「可以顺手甩一张收藏的表情包」，
      她就会在回复里写 [img:文件名]，由 reply/randimg 管线放行并发出。
"""
import random

NAME = "sticker_pack"
DESC = "群聊表情包：收藏群友发的表情，闲聊时随机甩一张"
FEATURE = None          # 始终启用

PICK_PROB = 0.25


def _listing(max_n=8):
    try:
        import stickers
        return stickers.block(max_n=max_n) or ""
    except Exception:
        return ""


def prompt(ctx):
    """把收藏清单塞进上下文，让她随时能挑。"""
    return _listing(12)


def on_turn_in(ctx):
    if getattr(ctx, "src", "") != "wechat":
        return ""
    if random.random() >= PICK_PROB:
        return ""
    listing = _listing(8)
    if not listing:
        return ""
    return ("\n\n【表情包】这轮可以顺手甩一张收藏的表情包活跃气氛："
            "从下面清单里随机挑一张，在回复的最后单独一行写 [img:文件名]。\n"
            + listing)
