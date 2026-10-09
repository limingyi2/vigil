# -*- coding: utf-8 -*-
"""表情包收藏与随手发（微信私聊+群聊）。

收藏（2026-10-09 改版）：群里/私聊出现的表情包，桥接**每张**都会送进
图库（/api/stickers/collect），云端按内容哈希去重、新图用视觉模型打
标签（是什么、什么情绪、什么场合用）——图库从"9 张未知图"变成
"带说明的弹药库"。
发送：这里按概率提示她「语境合适就甩一张」，她看着带标签的清单自己挑，
在回复里写 [img:文件名]，由桥接的斗图管线发出。
"""
import random

NAME = "sticker_pack"
DESC = "表情包：自动收藏聊天里的图并打标签，语境合适时甩一张"
FEATURE = None          # 始终启用

PICK_PROB = 0.18


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
    if getattr(ctx, "src", "") not in ("wechat", "wechat_group"):
        return ""
    if random.random() >= PICK_PROB:
        return ""
    listing = _listing(8)
    if not listing:
        return ""
    return ("\n\n【表情包】可以甩一张收藏的表情包活跃气氛。规则：\n"
            "- 看每张的描述，挑**跟现在语境最贴**的一张；没有贴的就一张都"
            "别发，别硬凑。\n"
            "- 甩图就专心甩图，**别去查热榜/热梗榜，也别提榜单** —— 那是"
            "另一回事，混在一起很怪。\n"
            "- 他刚发过图、或者你上一条已经发过图了，这轮就别再发。\n"
            "- 决定发的话，在回复的最后单独一行写 [img:文件名]。\n"
            + listing)
