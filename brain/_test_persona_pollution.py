# -*- coding: utf-8 -*-
"""群聊人设污染测试：她不该有第二个名字，也不该临时编身份。

## 为什么这个测试存在

用户在群里@她问「你是谁」，她答「我是你同学，文艺部的」。
云端实查system 文本：知夏 2 处、同学 0 处、群友 2 处。

两个独立问题：
  1. 🔴 **她有两个名字**：`nicknames = ["麦麦", "知夏"]`，
     personality 还写着「以前在别的号上叫知夏，有人这么叫你也应一声」。
     本意是兼容旧称呼，实际后果：用户叫她麦麦，她自称知夏，
     而且「知夏」这个词会把知夏那套人设（恋人、同城、约好的事）带进来。
     记忆里已经查过：恋人/男朋友/初恋/异地/你们约好的事在文本里全是 0，
     说明关系那块是干净的 —— 污染只来自**名字**。
  2. 🔴 **她临时编身份**：被问「你是谁」时人设没给答案，她就编了
     「你同学 / 文艺部」。原来的诚实约束只管**知识**（没听过就说没听过），
     不管**身份**（你是谁/哪个班的/在哪上班）。

## 判据

不测「她答得对不对」（那要真调模型），只测**system 文本里有没有不该有的东西**。
文字层面的污染是根因，模型表现只是它的投影。

跑：python _test_persona_pollution.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from persona_store import load, build_system_text


PASS, FAIL = [], []


def check(name, got, want):
    if got == want:
        PASS.append(name)
        print("  PASS  %s" % name)
    else:
        FAIL.append(name)
        print("  FAIL  %s\n        期望 %r\n        实际 %r" % (name, want, got))


PKEY = "preset_group_maibot"
p = load(PKEY)
txt = build_system_text(p)

print("\n[1] 只有一个名字")
nick = p.get("nicknames") or []
check("nicknames 里没有第二个名字", len(nick), 1)
check("就叫麦麦", nick, ["麦麦"])
check("system 文本里没有「知夏」", "知夏" in txt, False)
check("system 文本里没有「林知夏」", "林知夏" in txt, False)
print("        nicknames = %r" % (nick,))

print("\n[2] 关系是群友不是恋人")
for kw in ["男朋友", "初恋", "恋人", "异地"]:
    check("没有「%s」" % kw, kw in txt, False)
check("写的是群友", "群友" in txt, True)

print("\n[3] 身份约束存在（防止临时编）")
# 这段是「你是谁」类问题的护栏，缺了她就会编「我是你同学文艺部的」
guards = ["哪个班", "在哪上班", "不要临时编"]
for g in guards:
    check("人设里写了「%s」" % g, g in p.get("personality", ""), True)

print("\n[4] 关键约束还在（别把人设改空了）")
check("必须@必回这条还在", "别人 @你，一定回" in txt, True)
check("不会没话题硬找话题还在", "不会没话题硬找话题" in txt, True)
check("宁可显得笨也不编还在", "宁可显得笨" in txt, True)
check("personality 有一定长度（不是空壳）",
      len(p.get("personality", "")) > 800, True)

print("\n[5] 别的人设不受影响")
# 🔴 用本机真实存在的 key。`base.json` **只在云端**（本机叫 default.json，
# 那是本机↔云端副本差异，不是配置错）—— 写 "base" 会因文件不存在而
# 静默回退 default.json，测的就不是同一个人了。
# default.json 是**空模板**（name=''），那是正常的，不是坏了。
o = load("preset_group")
check("preset_group 人设有 name", bool(o.get("name")), True)
# preset_group 本来就叫麦麦（那是它自己的人设，不是污染）——
# 要查的是它**只叫麦麦**、不同时挂着「知夏」。
check("preset_group 只叫麦麦", o.get("nicknames"), ["麦麦"])
check("preset_group 里没有「知夏」残留",
      "知夏" in json.dumps(o, ensure_ascii=False), False)

# linzhixia 本来就是知夏 —— 别把它误伤，也别拿它当污染判据
lz = load("linzhixia")
check("linzhixia 人设有 name", bool(lz.get("name")), True)
check("linzhixia 本人就叫知夏（不该被误改）",
      "知夏" in (lz.get("nicknames") or []), True)
check("linzhixia 没被塞进麦麦", "麦麦" in (lz.get("nicknames") or []), False)

print("\n[6]副本差异提醒（不是失败，是让人看见）")
# 本机 config/personas/ 下没有 base.json —— 云端有。本机跑会以 default 代替。
if not os.path.exists(os.path.join(os.path.dirname(__file__),
                                    "config", "personas", "base.json")):
    print("        注意：本机没有 base.json（云端有）。")
    print("        本机测试用 default 代表云端的 base，"
          "改config.json 前留意这个差异。")

print("\n" + "=" * 46)
print("通过 %d / %d" % (len(PASS), len(PASS) + len(FAIL)))
if FAIL:
    print("失败：" + "、".join(FAIL))
print("=" * 46)
sys.exit(1 if FAIL else 0)
