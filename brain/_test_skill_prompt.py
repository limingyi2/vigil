# -*- coding: utf-8 -*-
"""她必须**看得见**技能的名字，否则永远不调。

## 症状与真因

用户报"插件没生效"：微信里@她问"今日运势"，她答
「今日运势：深夜不睡，明日必困。就这，别问了。」

分三步查出来的：
  ① 技能本体**完全正常** —— 直接打宿主 `daily-fortune__today` 返回
     「大吉★★★★★ · 综合 98 分 / 宜：吃顿好的、摸鱼、学个新词」。
  ② 渠道开关是开的（app/wechat 都 true），宿主在线，7 个工具可见。
  ③ `/api/chat` 的 `trace.tool` 是空 —— **她根本没调**。

真因在 `TOOL_HELP`：原来只写"先调 skill_list 看看有什么"，
**一个技能名都没有**。那个设计是为躲「构造时快照 + 宿主晚起」的时序坑，
但代价是模型没有任何理由发起那第一轮 `skill_list` —— 于是她自己编了一个。
自己编的运势没有签位、没有分数，跟技能真跑出来的差得一眼能看出来。

## 判据为什么不只看"提示词里有技能名"

写了名字不等于她会用。所以判据分三层：
  ① **入口**：TOOL_HELP 里有具体技能名（不是只有 skill_call 这个壳）
  ② **判据**：写了"问运势必须调 daily-fortune__today"——
     光有名字她仍可能觉得"我可以自己编"
  ③ **没退化**：skill_list / skill_call 这两个固定入口还在
     （它们是宿主晚起时的兜底路径，删了就彻底没有退路）

⚠️ 判据直接读源码里的字符串，**不 import skill** —— import 要宿主活着，
而"宿主活着"恰恰是本文件要验的另一件事；用 import 会出现
"因为宿主没起所以测不到提示词"的自证循环。

用法：F:/Vigil/venv/Scripts/python.exe _test_skill_prompt.py
"""
import ast
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "plugins", "skill.py")

PASS = [0]
FAIL = []

# 云端 skill_host/skills 下实际装着的包 → 提示词里必须能对上。
# 装了新的要在这里加一条，否则提示词和实际装的会悄悄漂。
KNOWN_SKILLS = [
    "daily-fortune__today",
    "daily-fortune__send",
    "hot-trends__hot_brief",
    "hot-trends__hot_status",
    "reply-meme__find_meme",
    "valorant-match__recent_schedule",
    "valorant-match__recent_results",
]


def check(name, ok, extra=None):
    if ok:
        PASS[0] += 1
        print("  PASS  " + name)
    else:
        FAIL.append(name)
        print("  FAIL  " + name + ("  <<< " + str(extra)[:220]
                                   if extra is not None else ""))


def main():
    if not os.path.isfile(SRC):
        print("  ABORT 找不到 %s" % SRC)
        return 1
    src = io.open(SRC, encoding="utf-8").read()
    ast.parse(src)          # 语法错直接炸
    print("  skill.py 语法 OK")

    m = re.search(r'TOOL_HELP\s*=\s*(?:"""(.*?)"""|\'\'\'(.*?)\'\'\')', src, re.S)
    if not m:
        print("  ABORT 找不到 TOOL_HELP 字面量 —— 下面的判据无效")
        return 1
    th = m.group(1) or m.group(2) or ""
    print("  TOOL_HELP 长度 = %d" % len(th))

    print("\n[1] 入口：提示词里有具体技能名")
    missing = [s for s in KNOWN_SKILLS if s not in th]
    check("7 个技能名都在TOOL_HELP 里（缺: %s）" % (missing or "无"),
          not missing, missing)
    # 关键：不能只有 skill_call 这个壳
    has_bare_shell = ("skill_list" in th and len(KNOWN_SKILLS) == 0)
    check("不是「只说先 skill_list 看看」那种空壳", not has_bare_shell)

    print("\n[2] 判据：写了什么时候必须调")
    check("点名了 daily-fortune__today（运势那个）",
          "daily-fortune__today" in th)
    check("有「必须」这类硬判据，不是只列工具",
          "必须" in th, [l.strip()[:70] for l in th.splitlines()
                         if "daily-fortune__today" in l][:2])
    # 判据里该说清不许编
    check("说了不许自己编结果",
          ("不许自己编" in th or "不要自己编" in th or "自己编" in th))

    print("\n[3] 没退化：宿主晚起时的兜底路径还在")
    check("skill_call 还在（模型唯一入口）", "skill_call" in th)
    check("skill_list 还在（宿主晚起时的兜底）", "skill_list" in th)
    check("调不通时有退路提示（当没这件事/不要提技能）",
          ("当没这件事" in th) or ("不要提" in th) or ("正常聊天" in th))

    print("\n[4] prompt() 补的是判据，不是第二份清单")
    pm = re.search(r'def prompt\(ctx=None\):(.*?)\n\ndef ', src, re.S)
    check("找得到 prompt()", pm is not None)
    if pm:
        pb = pm.group(1)
        named = [s for s in KNOWN_SKILLS if s in pb]
        # 允许提daily-fortune__today（判据要点名），但不该把 7 个都抄一遍
        check("prompt() 没有把整份清单抄一遍（两个真源会互相矛盾）",
              len(named) <= 1, named)
        check("prompt() 里有「必须调」这类判据",
              "必须" in pb, pb.strip()[:120])

    print("\n[5] prompt() 必须真的返回东西（原来 return None）")
    if pm:
        pb = pm.group(1)
        check("不是 return None", "return None" not in pb,
              pb.strip()[-100:])

    print("\n== %d pass / %d fail ==" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())