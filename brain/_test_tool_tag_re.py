# -*- coding: utf-8 -*-
r"""工具标签的正则必须容得下**在册工具名**。

## 症状

用户报"插件没生效"：微信里@她问"今日运势」，她答
「深夜不睡，明日必困」—— 自己编的，没有签位没有分数。

分到最后一步才抓到真凶，而且它跟提示词毫无关系：

    TOOL_TAG_RE = r"\[tool[:：]([A-Za-z_]+)(?:[:：]([^\]]*))?\]"
                                 ^^^^^^^^^^^^^^ 只容字母下划线

于是模型哪怕在提示词里**看见了**工具名、也规规矩矩写出
`[tool:daily-fortune__today:{"who":"我"}]`，这条标签**匹配不上**：
finditer 返回空 → 工具循环直接 break → 工具从没执行 →
而 `[tool:...]` 又被兜底的 `TOOL_TAG_ANY_RE` 扫掉 → 用户只看到"她压根没调"。

## 为什么这个洞躲了这么久

内置工具名**恰好全是纯字母下划线**：`ip_city` / `tracking` / `weather`。
正则只比它们宽一点就够用了，从没测过带连字符的名字。
而**技能工具名全带连字符**（JS 包的 `包名__工具名` 约定），
所以它一调就废、而且必现。

## 判据不能只测一个正例

原来那版正则对 `[tool:search:北京]` 是好的，所以"单测通过"完全没有保护力。
这里按三层钉：
  ① **穷举在册工具名**—— 每一个都必须能被 TOOL_TAG_RE 匹配。
     这条是根因判据：字符集比在册名窄 ⇒ 那个工具永远调不通。
  ② 真实技能名（带连字符 + 双下划线 + JSON 参数）能匹配
  ③ 边界不许回归：空名不匹配（否则 `[tool::x]` 会切出空工具名）、
     多个标签在一轮里都要抓到
另外钉死字符集的**边界**：工具名收在 ASCII（字母数字下划线短横线），
模型编出来的中文名（`[tool:根本不存在:xx]`）**故意不认** ——
不认它不影响什么，因为 `TOOL_TAG_ANY_RE` 会把看着像标签的东西一律扫掉，
用户永远看不到。这条才是"不许漏标签"的真正保障。

用法：F:/vigil/venv/Scripts/python.exe _test_tool_tag_re.py
"""
import io
import os
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

PASS = [0]
FAIL = []

# 真实存在的技能工具名（云端 skill_host/skills 下装着的）
SKILL_TOOLS = [
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
        print("  FAIL  " + name + ("  <<< " + str(extra)[:260]
                                   if extra is not None else ""))


def main():
    import tools
    RE = tools.TOOL_TAG_RE

    print("[1] 根因判据：在册工具名必须逐个能被匹配")
    # 内置 + 插件 + 技能，凑一份"运行时真正认的名字"
    builtin = sorted(getattr(tools, "TOOLS", {}).keys())
    every = builtin + SKILL_TOOLS
    print("内置 %d 个 + 技能 %d 个" % (len(builtin), len(SKILL_TOOLS)))
    unmatched = []
    for n in every:
        m = RE.findall("[tool:%s:{}]" % n)
        if not m:
            unmatched.append(n)
    check("每一个在册工具名都能被 TOOL_TAG_RE 匹配（匹配不上的: %s）"
          % (unmatched or "无"), not unmatched, unmatched)
    # 单独把"带连字符/数字"这类名字拎出来说清楚
    need = [n for n in every if not re.fullmatch(r"[A-Za-z_]+", n)]
    print("含连字符/数字的（原来正则全部漏掉）: %s" % need)
    for n in need:
        check("  %s 能匹配" % n, bool(RE.findall("[tool:%s:{}]" % n)))

    print("\n[2] 真实技能调用长什么样")
    # **判据必须用 finditer，不能用 findall**（这是我自己踩的假失败）：
    # findall 对"没参与匹配的组"返回 ''，而 `Match.group()` 返回 None。
    # 生产代码（tools.py 的 run_chat_with_tools）走的是 finditer + group(2)，
    # 所以判据要跟生产同一条路，否则测的是 findall 的返回格式、
    # 跟真实行为对不上，会把好的判成坏的。
    def parse(s):
        return [(m.group(1), m.group(2)) for m in RE.finditer(s)]

    m = parse('[tool:daily-fortune__today:{"who":"我"}]')
    check("带 JSON 参数能切出工具名和参数",
          m and m[0][0] == "daily-fortune__today"
          and '"who"' in (m[0][1] or ""), m)
    check("没写参数时第二组是 None（跟旧正则一致，不许变成 ''）",
          parse("[tool:daily-fortune__today]")[0][1] is None,
          parse("[tool:daily-fortune__today]"))
    check("全角冒号照样吃",
          parse("[tool：hot-trends__hot_brief]")[0][0]
          == "hot-trends__hot_brief")

    print("\n[3] 边界不许回归")
    check("空工具名不匹配（否则 [tool::x] 会切出空串名字）",
          not parse("[tool::缺名字]"), parse("[tool::缺名字]"))
    check("没有标签的普通文本不匹配",
          not parse("这里没有工具标签"), parse("这里没有工具标签"))
    two = parse("[tool:a:1] 和 [tool:daily-fortune__today:2] 都在")
    check("一轮里多个标签都抓到",
          len(two) == 2 and two[1][0] == "daily-fortune__today", two)
    # 参数里带冒号/方括号不该把名字切坏
    tricky = parse('[tool:search:上海 {"k":"v:1"}]')
    check("参数里有冒号也不影响工具名",
          tricky and tricky[0][0] == "search" and "v:1" in (tricky[0][1] or ""),
          tricky)

    print("\n[4] 字符集边界：ASCII 之外的名字不当工具名；但绝不许漏到正文")
    # 字符集收在 [A-Za-z0-9_-]，够覆盖内置 + 插件 + 技能名。中文名基本都是
    # 模型自己编的（它爱写 [tool:根本不存在:xx]）—— 不认它是对的，
    # 但**必须**被 TOOL_TAG_ANY_RE 扫掉。这条判据真正要保护的是"用户看不见标签"，
    # 不是"必须把编的名字当工具去查一遍"。
    fake = parse("[tool:根本不存在:xx]")
    check("编造的中文名不被当成工具名（窄字符集，故意）", not fake, fake)
    stripped = tools.TOOL_TAG_ANY_RE.sub("", "[tool:根本不存在:xx]")
    check("但它照样被兜底正则扫干净（不许原样发到用户眼前）",
          stripped == "", repr(stripped))
    # 而"看着像工具标签"的东西不能因为匹配不上就留在正文里
    check("半截/畸形标签也不会漏出去",
          tools.TOOL_TAG_ANY_RE.sub("", "好[tool:中文名]的") == "好的",
          tools.TOOL_TAG_ANY_RE.sub("", "好[tool:中文名]的"))
    # 已知边界（不是回归，先记着）：技能**包名**允许点号
    # （skill.py 的 _pkg_name_ok = [A-Za-z0-9][A-Za-z0-9._-]*），
    # 所以理论上存在 `my.skill__today` 这种合法工具名，而当前字符集没有点号。
    # 实际在册的技能包名全是连字符（daily-fortune / hot-trends …），暂不受影响。
    dot = parse("[tool:my.skill__today:x]")
    print("  备注：带点号的技能名匹配 -> %s（包名允许点号，但现有技能都用连字符）"
          % ("能" if dot else "不能"))

    print("\n== %d pass / %d fail ==" % (PASS[0], len(FAIL)))
    for f in FAIL:
        print("   FAILED: " + f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())